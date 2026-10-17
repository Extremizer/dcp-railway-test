"""Internal Order P3 authoritative/scoped read-model regression."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import (
    WarehouseActorContext,
    confirm_internal_direct_payment,
    create_payment_snapshot,
    init,
)
from internal_order_read_v01 import (
    CONTRACT_VERSION,
    AdminReadContext,
    WarehouseReadContext,
    read_order,
    read_warehouse_history,
)
import warehouse_balance_v14 as wb
import warehouse_history_v14 as history


def create_shared_schema(db):
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
              quantity INTEGER NOT NULL,
              customer_unit_rub INTEGER,
              payment_route TEXT NOT NULL
            );
            """
        )


class InternalOrderP3ReadTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"p3.db")
        create_shared_schema(self.db)
        init(self.db)

    def add_internal(
        self, order_id="I-P3-1", warehouse=1, status="assembled_unpaid",
        prices=(10000,9500), warehouse_no="5234", client_id="client:1001",
        p=16575, make_snapshot=True,
    ):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (order_id,status,"2026-10-13T21:00:00+02:00",7001),
            )
            for pos,price in enumerate(prices,1):
                c.execute(
                    """INSERT INTO order_items(
                         order_id,position,manufacturer,oem,requested_oem,name,
                         offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
                       VALUES(?,?,?,?,?,?,'warehouse',?,1,?,'direct_partner')""",
                    (
                        order_id,pos,"BRP",f"OEM-{pos}",f"REQ-{pos}",
                        f"Part {pos}",warehouse,price,
                    ),
                )
        if make_snapshot:
            create_payment_snapshot(
                self.db,
                order_id=order_id,
                client_id=client_id,
                partner_amount_rub=p,
                created_by=f"internal-terms:warehouse-{warehouse}",
                warehouse_order_number=warehouse_no,
                expected_warehouse_id=warehouse,
            )

    def add_usa(self, order_id="E-P3-USA"):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (order_id,"new","2026-10-13T21:00:00+02:00",7001),
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,manufacturer,oem,requested_oem,name,
                     offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
                   VALUES(?,1,'BRP','USA-OEM','USA-OEM','USA Part',
                          'usa',NULL,1,5000,'extremizer_balance')""",
                (order_id,),
            )

    def admin(self):
        return AdminReadContext(actual_actor="admin:1",authorized=True)

    def sklad(self,warehouse=1,authorized=True):
        return WarehouseReadContext(
            actual_actor=f"warehouse:{warehouse}:user",warehouse_id=warehouse,
            authorized=authorized,
        )

    def confirm(self,order_id="I-P3-1",warehouse=1):
        return confirm_internal_direct_payment(
            self.db,
            order_id=order_id,
            confirmation_id=f"confirm:{order_id}",
            actor_context=WarehouseActorContext(
                actual_actor=f"warehouse:{warehouse}:user",
                effective_subject=f"warehouse:{warehouse}",
                warehouse_id=warehouse,
                authorized=True,
            ),
            payment_confirmed=True,
            confirmed_amount_rub=19500,
        )

    def dump(self):
        with sqlite3.connect(self.db) as c:
            return "\n".join(c.iterdump())

    def test_due_snapshot_is_authoritative_for_history_and_strict_read(self):
        self.add_internal()
        row=history.order_rows(self.db,1,search="I-P3-1")[0]
        self.assertEqual(row["warehouse_id"],1)
        self.assertEqual(row["extremizer_order_number"],"I-P3-1")
        self.assertEqual(row["warehouse_order_number"],"5234")
        self.assertEqual(row["client_id"],"client:1001")
        self.assertEqual(row["customer_amount_rub"],19500)
        self.assertEqual(row["partner_amount_rub"],16575)
        self.assertEqual(row["partner_amount_source"],"internal_payment_snapshot")
        self.assertEqual(row["payment_status"],"DUE")
        self.assertEqual(row["payment_route"],"direct_partner")
        self.assertIsNone(row["balance_rub"])

        result=read_order(
            self.db,warehouse_id=1,order_id="I-P3-1",context=self.sklad()
        )
        self.assertEqual(result["contract_version"],CONTRACT_VERSION)
        self.assertEqual(result["scope"],{"warehouse_id":1,"viewer_type":"warehouse"})
        self.assertEqual(result["order"]["partner_amount_source"],"internal_payment_snapshot")
        self.assertEqual(result["order"]["payment_status"],"DUE")
        self.assertEqual(
            [(x["oem"],x["quantity"]) for x in result["items"]],
            [("OEM-1",1),("OEM-2",1)],
        )

    def test_paid_status_and_posted_balance_are_reflected(self):
        self.add_internal()
        self.confirm()
        result=read_order(
            self.db,warehouse_id=1,order_id="I-P3-1",context=self.admin()
        )
        self.assertEqual(result["order"]["payment_status"],"PAID")
        self.assertEqual(result["order"]["balance_rub"],2925)
        self.assertEqual(result["order"]["customer_amount_rub"],19500)
        self.assertEqual(result["order"]["partner_amount_rub"],16575)

    def test_history_without_snapshot_does_not_invent_p_and_strict_read_fails(self):
        self.add_internal(make_snapshot=False)
        row=history.order_rows(self.db,1,search="I-P3-1")[0]
        self.assertIsNone(row["partner_amount_rub"])
        self.assertIsNone(row["partner_amount_source"])
        self.assertIsNone(row["payment_status"])
        self.assertIsNone(row["client_id"])
        with self.assertRaisesRegex(ValueError,"INTERNAL_PAYMENT_SNAPSHOT_REQUIRED"):
            read_order(
                self.db,warehouse_id=1,order_id="I-P3-1",context=self.sklad()
            )

    def test_legacy_partner_like_table_is_never_p_source(self):
        self.add_internal(make_snapshot=False)
        with sqlite3.connect(self.db) as c:
            c.execute(
                "CREATE TABLE warehouse_partner_settlements(order_id TEXT,warehouse_id INTEGER,partner_amount INTEGER)"
            )
            c.execute(
                "INSERT INTO warehouse_partner_settlements VALUES('I-P3-1',1,12345)"
            )
        row=history.order_rows(self.db,1,search="I-P3-1")[0]
        self.assertIsNone(row["partner_amount_rub"])
        self.assertIsNone(row["partner_amount_source"])

    def test_sklad_scope_cannot_read_another_warehouse(self):
        self.add_internal()
        with self.assertRaisesRegex(PermissionError,"WAREHOUSE_READ_SCOPE_MISMATCH"):
            read_order(
                self.db,warehouse_id=1,order_id="I-P3-1",context=self.sklad(2)
            )
        with self.assertRaisesRegex(PermissionError,"WAREHOUSE_READ_SCOPE_MISMATCH"):
            read_warehouse_history(
                self.db,warehouse_id=1,context=self.sklad(2)
            )

    def test_admin_must_still_select_exact_warehouse_scope(self):
        self.add_internal(warehouse=1)
        result=read_order(
            self.db,warehouse_id=1,order_id="I-P3-1",context=self.admin()
        )
        self.assertEqual(result["scope"]["warehouse_id"],1)
        with self.assertRaises(PermissionError):
            read_order(
                self.db,warehouse_id=2,order_id="I-P3-1",context=self.sklad(1)
            )

    def test_tampered_snapshot_warehouse_mismatch_fails_closed(self):
        self.add_internal()
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE internal_order_payment_snapshots_v01 SET warehouse_id=2 WHERE order_id='I-P3-1'"
            )
        with self.assertRaisesRegex(ValueError,"INTERNAL_ORDER_SCOPE_MISMATCH"):
            history.order_rows(self.db,2,search="I-P3-1")
        with self.assertRaisesRegex(ValueError,"INTERNAL_ORDER_SCOPE_MISMATCH"):
            read_order(
                self.db,warehouse_id=2,order_id="I-P3-1",context=self.sklad(2)
            )

    def test_wrong_or_unknown_order_fails_closed(self):
        self.add_internal()
        with self.assertRaisesRegex(ValueError,"INTERNAL_PAYMENT_SNAPSHOT_REQUIRED"):
            read_order(
                self.db,warehouse_id=1,order_id="I-NOT-THERE",context=self.sklad()
            )

    def test_snapshot_client_is_returned_exactly_and_no_caller_client_override_exists(self):
        self.add_internal(client_id="client:canonical-55")
        result=read_order(
            self.db,warehouse_id=1,order_id="I-P3-1",context=self.sklad()
        )
        self.assertEqual(result["order"]["client_id"],"client:canonical-55")
        # Cross-check against shared order owner is intentionally impossible in
        # P3 because current shared orders has no canonical client_id column.
        self.assertNotIn("expected_client_id", result)

    def test_reads_are_database_read_only(self):
        self.add_internal()
        before=self.dump()
        for _ in range(3):
            read_order(
                self.db,warehouse_id=1,order_id="I-P3-1",context=self.sklad()
            )
            read_warehouse_history(
                self.db,warehouse_id=1,context=self.sklad(),search="I-P3"
            )
        after=self.dump()
        self.assertEqual(before,after)

    def test_failed_payment_rollback_remains_due_in_read_model(self):
        self.add_internal()
        def fail(step):
            if step=="warehouse_accrual":
                raise RuntimeError("injected")
        with self.assertRaisesRegex(RuntimeError,"injected"):
            confirm_internal_direct_payment(
                self.db,
                order_id="I-P3-1",
                confirmation_id="confirm:rollback",
                actor_context=WarehouseActorContext(
                    actual_actor="warehouse:1:user",
                    effective_subject="warehouse:1",
                    warehouse_id=1,
                    authorized=True,
                ),
                payment_confirmed=True,
                confirmed_amount_rub=19500,
                _failure_hook=fail,
            )
        result=read_order(
            self.db,warehouse_id=1,order_id="I-P3-1",context=self.sklad()
        )
        self.assertEqual(result["order"]["payment_status"],"DUE")
        self.assertIsNone(result["order"]["balance_rub"])

    def test_whole_ledger_total_3425_ignores_pagination_and_search(self):
        # Four direct-payment margins: +2925 +1500 +3000 +4500.
        data=[
            ("I-A",12925,10000,2925),
            ("I-B",11500,10000,1500),
            ("I-C",13000,10000,3000),
            ("I-D",14500,10000,4500),
        ]
        for order,c,p,_ in data:
            self.add_internal(order_id=order,prices=(c,),p=p,warehouse_no=order)
            confirm_internal_direct_payment(
                self.db,
                order_id=order,
                confirmation_id=f"confirm:{order}",
                actor_context=WarehouseActorContext(
                    actual_actor="warehouse:1:user",
                    effective_subject="warehouse:1",
                    warehouse_id=1,
                    authorized=True,
                ),
                payment_confirmed=True,
                confirmed_amount_rub=c,
            )
        # Spend full P=8500 from accumulated warehouse balance.
        self.add_internal(
            order_id="I-SPEND",prices=(9000,),p=8500,warehouse_no="W-SPEND"
        )
        wb.reserve_full_balance(
            self.db,reservation_id="spend-8500",warehouse_id=1,
            order_id="I-SPEND",required_amount_rub=8500,actual_actor="admin:1"
        )
        wb.apply_reserved_balance(
            self.db,reservation_id="spend-8500",actual_actor="admin:1"
        )
        page=read_warehouse_history(
            self.db,warehouse_id=1,context=self.sklad(),limit=1
        )
        self.assertEqual(len(page["rows"]),1)
        self.assertEqual(page["summary"]["available_balance_rub"],3425)
        hidden=read_warehouse_history(
            self.db,warehouse_id=1,context=self.sklad(),
            search="NO-MATCH",limit=1
        )
        self.assertEqual(hidden["rows"],[])
        self.assertEqual(hidden["summary"]["posted_balance_rub"],3425)
        self.assertEqual(hidden["summary"]["reserved_balance_rub"],0)
        self.assertEqual(hidden["summary"]["available_balance_rub"],3425)

    def test_usa_order_is_not_exposed_as_internal_warehouse_order(self):
        self.add_usa()
        result=read_warehouse_history(
            self.db,warehouse_id=1,context=self.sklad()
        )
        self.assertFalse(
            any(r["extremizer_order_number"]=="E-P3-USA" for r in result["rows"])
        )
        with self.assertRaisesRegex(ValueError,"INTERNAL_PAYMENT_SNAPSHOT_REQUIRED"):
            read_order(
                self.db,warehouse_id=1,order_id="E-P3-USA",context=self.sklad()
            )

    def test_unauthorized_read_contexts_are_denied(self):
        self.add_internal()
        with self.assertRaisesRegex(PermissionError,"WAREHOUSE_READ_NOT_AUTHORIZED"):
            read_order(
                self.db,warehouse_id=1,order_id="I-P3-1",
                context=self.sklad(1,authorized=False)
            )
        with self.assertRaisesRegex(PermissionError,"ADMIN_READ_NOT_AUTHORIZED"):
            read_order(
                self.db,warehouse_id=1,order_id="I-P3-1",
                context=AdminReadContext(actual_actor="admin:1",authorized=False)
            )


if __name__=="__main__":
    unittest.main(verbosity=2)
