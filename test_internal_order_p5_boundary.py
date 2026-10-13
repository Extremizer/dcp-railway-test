"""Internal Order P5 trusted terms / persistence / eligibility regressions."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import (
    WarehouseActorContext,
    confirm_internal_direct_payment,
    create_payment_snapshot,
    init as core_init,
)
from internal_order_persistence_v01 import (
    insert_disposable_shared_order,
    prepare_internal_order_write,
)
from internal_order_read_v01 import (
    WarehouseReadContext,
    read_order,
)
from internal_order_terms_v01 import (
    TermsConfirmationContext,
    confirm_terms_and_create_payment_snapshot,
    get_snapshot_terms_provenance,
    seed_approved_term,
    verify_snapshot_terms_provenance,
)
from internal_payment_eligibility_v01 import (
    PaymentEligibilityContext,
    evaluate_direct_payment_eligibility,
)


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


class P5Case(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"p5.db")
        create_schema(self.db)
        core_init(self.db)

    def prepared(self,order_id="I-P5-1",warehouse=1,status="assembled_unpaid"):
        return prepare_internal_order_write(
            order_id=order_id,
            client_id="client:1001",
            status=status,
            items=[
                {
                    "position":1,
                    "manufacturer":"BRP",
                    "oem":"OEM-1",
                    "requested_oem":"OEM-1",
                    "name":"Part 1",
                    "quantity":1,
                    "customer_unit_rub":10000,
                    "offer_source":"warehouse",
                    "warehouse_id":warehouse,
                    "payment_route":"direct_partner",
                },
                {
                    "position":2,
                    "manufacturer":"BRP",
                    "oem":"OEM-2",
                    "requested_oem":"OEM-2",
                    "name":"Part 2",
                    "quantity":1,
                    "customer_unit_rub":9500,
                    "offer_source":"warehouse",
                    "warehouse_id":warehouse,
                    "payment_route":"direct_partner",
                },
            ],
        )

    def insert_prepared(self,prepared=None):
        prepared=prepared or self.prepared()
        with sqlite3.connect(self.db) as c:
            insert_disposable_shared_order(c,prepared)
        return prepared

    def seed_terms(self,version="T1",source_ref="PL-1"):
        seed_approved_term(
            self.db,warehouse_id=1,terms_version=version,oem="OEM-1",
            partner_unit_rub=8000,source_type="warehouse_price_list",
            source_ref=source_ref,
        )
        seed_approved_term(
            self.db,warehouse_id=1,terms_version=version,oem="OEM-2",
            partner_unit_rub=8575,source_type="warehouse_price_list",
            source_ref=source_ref,
        )

    def terms_ctx(self,warehouse=1,authorized=True):
        return TermsConfirmationContext(
            actual_actor=f"warehouse:{warehouse}:terms",
            effective_subject=f"warehouse:{warehouse}",
            warehouse_id=warehouse,
            authorized=authorized,
        )

    def trusted_snapshot(self,key="terms:p5"):
        self.seed_terms()
        return confirm_terms_and_create_payment_snapshot(
            self.db,
            confirmation_key=key,
            order_id="I-P5-1",
            client_id="client:1001",
            warehouse_order_number=None,
            terms_version="T1",
            terms_source_type="warehouse_price_list",
            terms_source_ref="PL-1",
            confirmed_quantities={1:1,2:1},
            actor_context=self.terms_ctx(),
        )

    def elig_ctx(self,warehouse=1,authorized=True,permission=True,statuses=("assembled_unpaid",)):
        perms=frozenset({"CONFIRM_PAYMENT"}) if permission else frozenset()
        return PaymentEligibilityContext(
            actual_actor=f"warehouse:{warehouse}:payment",
            warehouse_id=warehouse,
            authorized=authorized,
            permissions=perms,
            payment_ready_statuses=tuple(statuses),
        )

    def eligibility(self,context=None,warehouse=1):
        return evaluate_direct_payment_eligibility(
            self.db,
            order_id="I-P5-1",
            warehouse_id=warehouse,
            context=context or self.elig_ctx(),
        )

    def dump(self):
        with sqlite3.connect(self.db) as c:
            return "\n".join(c.iterdump())


class StrictPersistenceBoundaryTests(P5Case):
    def test_validated_values_are_preserved_exactly_before_insert(self):
        prepared=self.prepared()
        self.assertEqual(prepared.customer_amount_rub,19500)
        self.assertEqual(prepared.warehouse_id,1)
        self.assertEqual(prepared.payment_route,"direct_partner")
        self.assertEqual([x.quantity for x in prepared.items],[1,1])
        self.assertEqual([x.customer_unit_rub for x in prepared.items],[10000,9500])
        self.insert_prepared(prepared)
        with sqlite3.connect(self.db) as c:
            rows=c.execute(
                """SELECT quantity,customer_unit_rub,warehouse_id,offer_source,payment_route
                   FROM order_items ORDER BY id"""
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (1,10000,1,"warehouse","direct_partner"),
                (1,9500,1,"warehouse","direct_partner"),
            ],
        )

    def test_bad_money_and_quantity_fail_before_any_insert(self):
        bads=[
            ("quantity",True),
            ("quantity",1.5),
            ("quantity","1"),
            ("quantity",0),
            ("quantity",-1),
            ("customer_unit_rub",True),
            ("customer_unit_rub",1.5),
            ("customer_unit_rub","10000"),
            ("customer_unit_rub",float("nan")),
            ("customer_unit_rub",float("inf")),
            ("customer_unit_rub",-1),
            ("warehouse_id",True),
            ("warehouse_id","1"),
        ]
        for idx,(field,bad) in enumerate(bads):
            items=[
                {
                    "position":1,"manufacturer":"BRP","oem":"OEM-1",
                    "requested_oem":"OEM-1","name":"Part","quantity":1,
                    "customer_unit_rub":10000,"offer_source":"warehouse",
                    "warehouse_id":1,"payment_route":"direct_partner",
                }
            ]
            items[0][field]=bad
            with self.subTest(field=field,bad=repr(bad)):
                with self.assertRaises((TypeError,ValueError)):
                    prepared=prepare_internal_order_write(
                        order_id=f"I-BAD-{idx}",
                        client_id="client:1",
                        status="assembled_unpaid",
                        items=items,
                    )
                    with sqlite3.connect(self.db) as c:
                        insert_disposable_shared_order(c,prepared)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM order_items").fetchone()[0],0)

    def test_invalid_source_route_or_mixed_warehouse_fail_before_insert(self):
        base={
            "position":1,"manufacturer":"BRP","oem":"OEM-1",
            "requested_oem":"OEM-1","name":"Part","quantity":1,
            "customer_unit_rub":10000,"offer_source":"warehouse",
            "warehouse_id":1,"payment_route":"direct_partner",
        }
        bad_source=dict(base,offer_source="usa")
        with self.assertRaises(ValueError):
            prepare_internal_order_write(
                order_id="I-X",client_id="c",status="assembled_unpaid",
                items=[bad_source],
            )
        bad_route=dict(base,payment_route="bogus")
        with self.assertRaises(ValueError):
            prepare_internal_order_write(
                order_id="I-Y",client_id="c",status="assembled_unpaid",
                items=[bad_route],
            )
        second=dict(base,position=2,oem="OEM-2",warehouse_id=2)
        with self.assertRaises(ValueError):
            prepare_internal_order_write(
                order_id="I-Z",client_id="c",status="assembled_unpaid",
                items=[base,second],
            )


class TrustedTermsProvenanceTests(P5Case):
    def setUp(self):
        super().setUp()
        self.insert_prepared()

    def test_trusted_command_calculates_exact_p_and_binds_provenance(self):
        result=self.trusted_snapshot()
        self.assertEqual(result["partner_amount_rub"],16575)
        self.assertEqual(result["snapshot"]["customer_amount_rub"],19500)
        self.assertEqual(result["snapshot"]["partner_amount_rub"],16575)
        self.assertTrue(result["provenance"]["verified"])
        prov=get_snapshot_terms_provenance(self.db,"I-P5-1")
        self.assertEqual(prov["confirmation_key"],"terms:p5")
        self.assertEqual(prov["terms_version"],"T1")
        self.assertEqual(prov["calculated_partner_amount_rub"],16575)

    def test_direct_snapshot_bypass_has_no_trusted_provenance(self):
        create_payment_snapshot(
            self.db,
            order_id="I-P5-1",
            client_id="client:1001",
            partner_amount_rub=16575,
            created_by="direct-low-level",
            expected_warehouse_id=1,
        )
        with self.assertRaisesRegex(ValueError,"TRUSTED_TERMS_PROVENANCE_REQUIRED"):
            verify_snapshot_terms_provenance(self.db,"I-P5-1")
        decision=self.eligibility()
        self.assertFalse(decision["eligible"])
        self.assertTrue(
            any(r["code"]=="TRUSTED_TERMS_PROVENANCE_REQUIRED" for r in decision["reasons"])
        )

    def test_unapproved_or_forged_source_cannot_create_trusted_snapshot(self):
        self.seed_terms()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_warehouse_terms_v01
                   SET approved=0 WHERE warehouse_id=1 AND oem='OEM-1'"""
            )
        with self.assertRaisesRegex(ValueError,"APPROVED_WAREHOUSE_TERM_REQUIRED"):
            confirm_terms_and_create_payment_snapshot(
                self.db,
                confirmation_key="forged",
                order_id="I-P5-1",
                client_id="client:1001",
                warehouse_order_number=None,
                terms_version="T1",
                terms_source_type="warehouse_price_list",
                terms_source_ref="PL-1",
                confirmed_quantities={1:1,2:1},
                actor_context=self.terms_ctx(),
            )
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM internal_order_payment_snapshots_v01").fetchone()[0],0
            )

    def test_current_terms_change_or_revocation_does_not_rewrite_historical_proof(self):
        self.trusted_snapshot()
        before=verify_snapshot_terms_provenance(self.db,"I-P5-1")
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_warehouse_terms_v01
                   SET partner_unit_rub=1, approved=0
                   WHERE warehouse_id=1 AND terms_version='T1'"""
            )
        after=verify_snapshot_terms_provenance(self.db,"I-P5-1")
        self.assertEqual(before["evidence_sha256"],after["evidence_sha256"])
        self.assertEqual(after["calculated_partner_amount_rub"],16575)
        self.assertTrue(self.eligibility()["eligible"])

    def test_tampered_audit_evidence_fails_closed(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_terms_confirmation_audit_v01
                   SET calculated_partner_amount_rub=1
                   WHERE confirmation_key='terms:p5'"""
            )
        with self.assertRaises(ValueError):
            verify_snapshot_terms_provenance(self.db,"I-P5-1")
        self.assertFalse(self.eligibility()["eligible"])

    def test_tampered_provenance_hash_fails_closed(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_order_snapshot_terms_provenance_v01
                   SET evidence_sha256='bad'
                   WHERE order_id='I-P5-1'"""
            )
        with self.assertRaisesRegex(ValueError,"PROVENANCE_MISMATCH"):
            verify_snapshot_terms_provenance(self.db,"I-P5-1")
        self.assertFalse(self.eligibility()["eligible"])

    def test_replay_is_one_confirmation_and_one_provenance(self):
        first=self.trusted_snapshot()
        second=confirm_terms_and_create_payment_snapshot(
            self.db,
            confirmation_key="terms:p5",
            order_id="I-P5-1",
            client_id="client:1001",
            warehouse_order_number=None,
            terms_version="T1",
            terms_source_type="warehouse_price_list",
            terms_source_ref="PL-1",
            confirmed_quantities={1:1,2:1},
            actor_context=self.terms_ctx(),
        )
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_terms_confirmation_audit_v01"
            ).fetchone()[0],1)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_order_snapshot_terms_provenance_v01"
            ).fetchone()[0],1)

    def test_conflicting_second_snapshot_is_rejected(self):
        self.trusted_snapshot()
        with self.assertRaisesRegex(ValueError,"snapshot conflict"):
            create_payment_snapshot(
                self.db,
                order_id="I-P5-1",
                client_id="client:1001",
                partner_amount_rub=16000,
                created_by="other",
                expected_warehouse_id=1,
            )

    def test_provenance_write_rolls_back_with_snapshot_and_audit(self):
        self.seed_terms()
        def fail(step):
            if step=="provenance":
                raise RuntimeError("injected:provenance")
        with self.assertRaisesRegex(RuntimeError,"injected:provenance"):
            confirm_terms_and_create_payment_snapshot(
                self.db,
                confirmation_key="rollback-prov",
                order_id="I-P5-1",
                client_id="client:1001",
                warehouse_order_number=None,
                terms_version="T1",
                terms_source_type="warehouse_price_list",
                terms_source_ref="PL-1",
                confirmed_quantities={1:1,2:1},
                actor_context=self.terms_ctx(),
                _failure_hook=fail,
            )
        with sqlite3.connect(self.db) as c:
            for table in (
                "internal_terms_confirmation_audit_v01",
                "internal_order_payment_snapshots_v01",
                "internal_order_terms_integrity_v01",
                "internal_order_snapshot_terms_provenance_v01",
            ):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0],0)


class PaymentEligibilityTests(P5Case):
    def setUp(self):
        super().setUp()
        self.insert_prepared()
        self.trusted_snapshot()

    def test_due_with_all_server_proofs_is_eligible_under_mock_status_policy(self):
        result=self.eligibility()
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reasons"],[])

    def test_due_is_not_payable_without_permission_or_status_policy(self):
        no_perm=self.eligibility(context=self.elig_ctx(permission=False))
        self.assertFalse(no_perm["eligible"])
        self.assertTrue(any(
            x["code"]=="CONFIRM_PAYMENT_PERMISSION_REQUIRED" for x in no_perm["reasons"]
        ))
        no_policy=self.eligibility(context=self.elig_ctx(statuses=()))
        self.assertFalse(no_policy["eligible"])
        self.assertTrue(any(
            x["code"]=="PAYMENT_READY_STATUS_POLICY_REQUIRED" for x in no_policy["reasons"]
        ))

    def test_wrong_status_is_not_payable(self):
        result=self.eligibility(context=self.elig_ctx(statuses=("some-approved-status",)))
        self.assertFalse(result["eligible"])
        self.assertTrue(any(x["code"]=="ORDER_NOT_PAYMENT_READY" for x in result["reasons"]))

    def test_cross_warehouse_is_denied(self):
        result=self.eligibility(context=self.elig_ctx(warehouse=2),warehouse=1)
        self.assertFalse(result["eligible"])
        self.assertTrue(any(
            x["code"]=="ACTOR_WAREHOUSE_SCOPE_MISMATCH" for x in result["reasons"]
        ))

    def test_changed_order_items_make_due_nonpayable(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE order_items SET quantity=2 WHERE id=1")
        result=self.eligibility()
        self.assertFalse(result["eligible"])
        self.assertTrue(any(
            x["code"]=="INTERNAL_ORDER_TERMS_DRIFT" for x in result["reasons"]
        ))

    def test_paid_is_not_payable_again(self):
        confirm_internal_direct_payment(
            self.db,
            order_id="I-P5-1",
            confirmation_id="pay:p5",
            actor_context=WarehouseActorContext(
                actual_actor="warehouse:1:payment",
                effective_subject="warehouse:1",
                warehouse_id=1,
                authorized=True,
            ),
            payment_confirmed=True,
            confirmed_amount_rub=19500,
        )
        result=self.eligibility()
        self.assertFalse(result["eligible"])
        self.assertTrue(any(
            x["code"]=="PAYMENT_STATUS_NOT_DUE" for x in result["reasons"]
        ))

    def test_legacy_snapshot_without_fingerprint_is_readable_but_nonpayable(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "DELETE FROM internal_order_terms_integrity_v01 WHERE order_id='I-P5-1'"
            )
        read=read_order(
            self.db,
            warehouse_id=1,
            order_id="I-P5-1",
            context=WarehouseReadContext(
                actual_actor="warehouse:1:reader",
                warehouse_id=1,
                authorized=True,
            ),
        )
        self.assertEqual(read["order"]["payment_status"],"DUE")
        result=self.eligibility()
        self.assertFalse(result["eligible"])
        self.assertTrue(any(
            x["code"]=="INTERNAL_TERMS_INTEGRITY_PROOF_REQUIRED" for x in result["reasons"]
        ))

    def test_eligibility_is_read_only(self):
        before=self.dump()
        for _ in range(3):
            self.eligibility()
        self.assertEqual(before,self.dump())


if __name__=="__main__":
    unittest.main(verbosity=2)
