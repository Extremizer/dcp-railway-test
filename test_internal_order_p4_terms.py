"""Internal Order P4 terms-integrity / authoritative-P regressions."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import (
    WarehouseActorContext,
    confirm_internal_direct_payment,
    create_payment_snapshot,
    get_payment_snapshot,
    init,
)
from internal_order_read_v01 import (
    WarehouseReadContext,
    read_order,
)
from internal_order_terms_v01 import (
    TermsConfirmationContext,
    confirm_terms_and_create_payment_snapshot,
    seed_approved_term,
)
import warehouse_balance_v14 as wb


def create_schema(db):
    with sqlite3.connect(db) as c:
        c.executescript(
            """
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              telegram_user_id INTEGER NOT NULL
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              manufacturer TEXT,
              oem TEXT,
              requested_oem TEXT,
              name TEXT,
              offer_source TEXT NOT NULL,
              warehouse_id INTEGER,
              quantity,
              customer_unit_rub,
              payment_route TEXT NOT NULL
            );
            """
        )


class P4Base(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"p4.db")
        create_schema(self.db)
        init(self.db)

    def add_order(self,order_id="I-P4-1",warehouse=1,qtys=(1,1),prices=(10000,9500)):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (order_id,"assembled_unpaid","2026-10-14T00:00:00+02:00",7001),
            )
            for pos,(qty,price) in enumerate(zip(qtys,prices),1):
                c.execute(
                    """INSERT INTO order_items(
                         order_id,position,manufacturer,oem,requested_oem,name,
                         offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
                       VALUES(?,?,?,?,?,?,'warehouse',?,?,?,'direct_partner')""",
                    (
                        order_id,pos,"BRP",f"OEM-{pos}",f"REQ-{pos}",
                        f"Part {pos}",warehouse,qty,price,
                    ),
                )

    def snapshot(self,order_id="I-P4-1",p=16575):
        return create_payment_snapshot(
            self.db,
            order_id=order_id,
            client_id="client:1001",
            partner_amount_rub=p,
            created_by="p4-test",
            warehouse_order_number=None,
            expected_warehouse_id=1,
        )

    def actor(self):
        return WarehouseActorContext(
            actual_actor="warehouse:1:user",
            effective_subject="warehouse:1",
            warehouse_id=1,
            authorized=True,
        )

    def confirm(self,order_id="I-P4-1",amount=19500,hook=None):
        return confirm_internal_direct_payment(
            self.db,
            order_id=order_id,
            confirmation_id=f"confirm:{order_id}",
            actor_context=self.actor(),
            payment_confirmed=True,
            confirmed_amount_rub=amount,
            _failure_hook=hook,
        )

    def counts(self,order_id="I-P4-1"):
        with sqlite3.connect(self.db) as c:
            state=c.execute(
                "SELECT payment_status FROM internal_order_payment_state_v01 WHERE order_id=?",
                (order_id,),
            ).fetchone()
            return {
                "status": state[0] if state else None,
                "ledger": c.execute(
                    "SELECT COUNT(*) FROM warehouse_internal_balance_ledger WHERE order_id=?",
                    (order_id,),
                ).fetchone()[0],
                "info": c.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE order_id=? AND event_type='INFO_DIRECT_WAREHOUSE_PAYMENT'",
                    (order_id,),
                ).fetchone()[0],
                "audit": c.execute(
                    "SELECT COUNT(*) FROM internal_order_audit_v01 WHERE order_id=? AND action='CONFIRM_INTERNAL_DIRECT_PAYMENT'",
                    (order_id,),
                ).fetchone()[0],
            }


class TermsIntegrityTests(P4Base):
    def test_unchanged_terms_success(self):
        self.add_order()
        self.snapshot()
        result=self.confirm()
        self.assertEqual(result["payment_status"],"PAID")
        self.assertEqual(result["difference_rub"],2925)

    def _drift_rejected(self,sql,args=()):
        self.add_order()
        self.snapshot()
        before=self.counts()
        with sqlite3.connect(self.db) as c:
            c.execute(sql,args)
        with self.assertRaisesRegex(ValueError,"INTERNAL_ORDER_TERMS_DRIFT"):
            self.confirm()
        self.assertEqual(self.counts(),before)
        self.assertEqual(wb.balance_summary(self.db,1)["posted_rub"],0)

    def test_changed_quantity_denied(self):
        self._drift_rejected("UPDATE order_items SET quantity=2 WHERE id=1")

    def test_changed_customer_price_denied(self):
        self._drift_rejected("UPDATE order_items SET customer_unit_rub=9999 WHERE id=1")

    def test_changed_warehouse_denied(self):
        self._drift_rejected("UPDATE order_items SET warehouse_id=2 WHERE id=1")

    def test_changed_route_denied(self):
        self._drift_rejected(
            "UPDATE order_items SET payment_route='extremizer_balance' WHERE id=1"
        )

    def test_changed_oem_denied(self):
        self._drift_rejected("UPDATE order_items SET oem='OTHER-OEM' WHERE id=1")

    def test_changed_offer_source_denied(self):
        self._drift_rejected("UPDATE order_items SET offer_source='usa' WHERE id=1")

    def test_changed_item_id_set_denied(self):
        self.add_order()
        self.snapshot()
        with sqlite3.connect(self.db) as c:
            row=c.execute(
                """SELECT position,manufacturer,oem,requested_oem,name,offer_source,
                          warehouse_id,quantity,customer_unit_rub,payment_route
                   FROM order_items WHERE id=1"""
            ).fetchone()
            c.execute("DELETE FROM order_items WHERE id=1")
            c.execute(
                """INSERT INTO order_items(
                     position,manufacturer,oem,requested_oem,name,offer_source,
                     warehouse_id,quantity,customer_unit_rub,payment_route,order_id)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                tuple(row)+("I-P4-1",),
            )
        with self.assertRaisesRegex(ValueError,"INTERNAL_ORDER_TERMS_DRIFT"):
            self.confirm()
        self.assertEqual(self.counts()["status"],"DUE")

    def test_legacy_snapshot_without_integrity_proof_is_readable_but_not_payable(self):
        self.add_order()
        self.snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                "DELETE FROM internal_order_terms_integrity_v01 WHERE order_id='I-P4-1'"
            )
        self.assertEqual(get_payment_snapshot(self.db,"I-P4-1")["partner_amount_rub"],16575)
        with self.assertRaisesRegex(ValueError,"INTERNAL_TERMS_INTEGRITY_PROOF_REQUIRED"):
            self.confirm()
        self.assertEqual(self.counts()["status"],"DUE")

    def test_nullable_warehouse_order_number_preserved_in_read_contract(self):
        self.add_order()
        self.snapshot()
        result=read_order(
            self.db,
            warehouse_id=1,
            order_id="I-P4-1",
            context=WarehouseReadContext(
                actual_actor="warehouse:1:reader",
                warehouse_id=1,
                authorized=True,
            ),
        )
        self.assertIsNone(result["order"]["warehouse_order_number"])
        self.assertEqual(
            result["order"]["partner_amount_source"],
            "internal_payment_snapshot",
        )

    def test_toctou_writer_is_blocked_after_integrity_check(self):
        self.add_order()
        self.snapshot()
        attempts=[]
        def hook(step):
            if step=="payment_state":
                other=sqlite3.connect(self.db,timeout=0.05)
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        other.execute("UPDATE order_items SET quantity=2 WHERE id=1")
                        other.commit()
                    attempts.append("blocked")
                finally:
                    other.close()
        result=self.confirm(hook=hook)
        self.assertEqual(result["payment_status"],"PAID")
        self.assertEqual(attempts,["blocked"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT quantity FROM order_items WHERE id=1").fetchone()[0],1)


class StrictMoneyTests(P4Base):
    def test_partner_amount_rejects_bool_float_decimal_string_negative(self):
        self.add_order()
        for bad in (True,1.5,"1.5","100",-1):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError,ValueError)):
                    create_payment_snapshot(
                        self.db,
                        order_id="I-P4-1",
                        client_id="client:1001",
                        partner_amount_rub=bad,
                        created_by="p4-test",
                        expected_warehouse_id=1,
                    )
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM internal_order_payment_snapshots_v01").fetchone()[0],0
            )

    def test_confirmed_amount_rejects_bool_float_string_and_negative(self):
        self.add_order()
        self.snapshot()
        for bad in (True,19500.0,"19500",-1):
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError,ValueError)):
                    confirm_internal_direct_payment(
                        self.db,
                        order_id="I-P4-1",
                        confirmation_id=f"bad:{repr(bad)}",
                        actor_context=self.actor(),
                        payment_confirmed=True,
                        confirmed_amount_rub=bad,
                    )
        self.assertEqual(self.counts()["status"],"DUE")

    def test_invalid_quantity_types_or_values_fail_snapshot(self):
        bad_values=(0,-1,1.5,"2")
        for idx,bad in enumerate(bad_values):
            order=f"I-Q-{idx}"
            self.add_order(order_id=order)
            with sqlite3.connect(self.db) as c:
                c.execute(
                    "UPDATE order_items SET quantity=? WHERE order_id=? AND position=1",
                    (bad,order),
                )
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError,ValueError)):
                    self.snapshot(order_id=order)

    def test_invalid_customer_unit_rub_fails_snapshot_without_truncation(self):
        for idx,bad in enumerate((1.25,"100.50",-1)):
            order=f"I-M-{idx}"
            self.add_order(order_id=order)
            with sqlite3.connect(self.db) as c:
                c.execute(
                    "UPDATE order_items SET customer_unit_rub=? WHERE order_id=? AND position=1",
                    (bad,order),
                )
            with self.subTest(bad=bad):
                with self.assertRaises((TypeError,ValueError)):
                    self.snapshot(order_id=order)


class AuthoritativePTermsTests(P4Base):
    def setUp(self):
        super().setUp()
        self.add_order()
        seed_approved_term(
            self.db,warehouse_id=1,terms_version="T1",oem="OEM-1",
            partner_unit_rub=8000,source_type="warehouse_price_list",
            source_ref="PRICE-LIST-2026-10"
        )
        seed_approved_term(
            self.db,warehouse_id=1,terms_version="T1",oem="OEM-2",
            partner_unit_rub=8575,source_type="warehouse_price_list",
            source_ref="PRICE-LIST-2026-10"
        )

    def ctx(self,warehouse=1,authorized=True):
        return TermsConfirmationContext(
            actual_actor=f"warehouse:{warehouse}:terms-user",
            effective_subject=f"warehouse:{warehouse}",
            warehouse_id=warehouse,
            authorized=authorized,
        )

    def create_terms(self,key="terms-1",version="T1",source="PRICE-LIST-2026-10",hook=None):
        return confirm_terms_and_create_payment_snapshot(
            self.db,
            confirmation_key=key,
            order_id="I-P4-1",
            client_id="client:1001",
            warehouse_order_number=None,
            terms_version=version,
            terms_source_type="warehouse_price_list",
            terms_source_ref=source,
            confirmed_quantities={1:1,2:1},
            actor_context=self.ctx(),
            _failure_hook=hook,
        )

    def test_server_calculated_p_and_provenance(self):
        result=self.create_terms()
        self.assertEqual(result["partner_amount_rub"],16575)
        self.assertEqual(result["snapshot"]["partner_amount_rub"],16575)
        with sqlite3.connect(self.db) as c:
            row=c.execute(
                """SELECT terms_version,terms_source_type,terms_source_ref,
                          calculated_partner_amount_rub
                   FROM internal_terms_confirmation_audit_v01"""
            ).fetchone()
        self.assertEqual(row,("T1","warehouse_price_list","PRICE-LIST-2026-10",16575))

    def test_exact_replay_and_conflicting_replay(self):
        first=self.create_terms()
        second=self.create_terms()
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        with self.assertRaisesRegex(ValueError,"confirmation conflict"):
            self.create_terms(source="OTHER")

    def test_missing_terms_source_denied(self):
        with self.assertRaisesRegex(ValueError,"APPROVED_WAREHOUSE_TERM_REQUIRED"):
            self.create_terms(version="MISSING")

    def test_changed_terms_source_or_version_denied(self):
        with self.assertRaisesRegex(ValueError,"WAREHOUSE_TERMS_SOURCE_MISMATCH"):
            self.create_terms(source="WRONG-SOURCE")

    def test_cross_warehouse_actor_denied(self):
        with self.assertRaisesRegex(PermissionError,"WAREHOUSE_SCOPE_MISMATCH"):
            confirm_terms_and_create_payment_snapshot(
                self.db,
                confirmation_key="x",
                order_id="I-P4-1",
                client_id="client:1001",
                warehouse_order_number=None,
                terms_version="T1",
                terms_source_type="warehouse_price_list",
                terms_source_ref="PRICE-LIST-2026-10",
                confirmed_quantities={1:1,2:1},
                actor_context=self.ctx(warehouse=2),
            )

    def test_unconfirmed_or_partial_quantity_denied(self):
        with self.assertRaisesRegex(ValueError,"ITEM_SET_MISMATCH"):
            confirm_terms_and_create_payment_snapshot(
                self.db,
                confirmation_key="partial-set",
                order_id="I-P4-1",
                client_id="client:1001",
                warehouse_order_number=None,
                terms_version="T1",
                terms_source_type="warehouse_price_list",
                terms_source_ref="PRICE-LIST-2026-10",
                confirmed_quantities={1:1},
                actor_context=self.ctx(),
            )
        with self.assertRaisesRegex(ValueError,"MUST_MATCH_PAYMENT_READY_ORDER"):
            confirm_terms_and_create_payment_snapshot(
                self.db,
                confirmation_key="partial-qty",
                order_id="I-P4-1",
                client_id="client:1001",
                warehouse_order_number=None,
                terms_version="T1",
                terms_source_type="warehouse_price_list",
                terms_source_ref="PRICE-LIST-2026-10",
                confirmed_quantities={1:2,2:1},
                actor_context=self.ctx(),
            )

    def test_terms_creation_rolls_back_after_each_write(self):
        for step in ("audit","snapshot"):
            db2=str(Path(self.tmp.name)/f"{step}.db")
            create_schema(db2)
            init(db2)
            with sqlite3.connect(db2) as c:
                c.execute(
                    "INSERT INTO orders VALUES('I-X','assembled_unpaid','t',1)"
                )
                c.execute(
                    """INSERT INTO order_items(
                         order_id,position,manufacturer,oem,requested_oem,name,
                         offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
                       VALUES('I-X',1,'BRP','OEM-1','OEM-1','x','warehouse',1,1,10000,'direct_partner')"""
                )
            seed_approved_term(
                db2,warehouse_id=1,terms_version="T1",oem="OEM-1",
                partner_unit_rub=8000,source_type="warehouse_price_list",
                source_ref="PRICE-LIST-2026-10"
            )
            def fail(current):
                if current==step:
                    raise RuntimeError(f"injected:{step}")
            with self.assertRaisesRegex(RuntimeError,f"injected:{step}"):
                confirm_terms_and_create_payment_snapshot(
                    db2,
                    confirmation_key=f"key-{step}",
                    order_id="I-X",
                    client_id="client:x",
                    warehouse_order_number=None,
                    terms_version="T1",
                    terms_source_type="warehouse_price_list",
                    terms_source_ref="PRICE-LIST-2026-10",
                    confirmed_quantities={1:1},
                    actor_context=TermsConfirmationContext(
                        actual_actor="warehouse:1:user",
                        effective_subject="warehouse:1",
                        warehouse_id=1,
                        authorized=True,
                    ),
                    _failure_hook=fail,
                )
            with sqlite3.connect(db2) as c:
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM internal_terms_confirmation_audit_v01").fetchone()[0],0
                )
                self.assertEqual(
                    c.execute("SELECT COUNT(*) FROM internal_order_payment_snapshots_v01").fetchone()[0],0
                )


if __name__=="__main__":
    unittest.main(verbosity=2)
