"""Internal Order P5 trusted-terms / persistence / eligibility regressions."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import math
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import (
    create_payment_snapshot,
    get_payment_snapshot,
    init as init_core,
)
from internal_order_persistence_v01 import (
    insert_disposable_shared_order,
    prepare_internal_order_write,
)
from internal_order_read_v01 import WarehouseReadContext, read_order
from internal_order_terms_v01 import (
    TermsConfirmationContext,
    confirm_terms_and_create_payment_snapshot,
    get_snapshot_terms_provenance,
    seed_approved_term,
    verify_snapshot_terms_provenance,
)
from internal_payment_eligibility_v01 import (
    CONFIRM_PAYMENT_PERMISSION,
    PaymentEligibilityContext,
    evaluate_direct_payment_eligibility,
)


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
              quantity,
              customer_unit_rub,
              payment_route TEXT NOT NULL
            );
            """
        )


def valid_items():
    return [
        {
            "position": 1,
            "manufacturer": "BRP",
            "oem": "OEM-1",
            "requested_oem": "OEM-1",
            "name": "Part 1",
            "quantity": 1,
            "customer_unit_rub": 10000,
            "offer_source": "warehouse",
            "warehouse_id": 1,
            "payment_route": "direct_partner",
        },
        {
            "position": 2,
            "manufacturer": "BRP",
            "oem": "OEM-2",
            "requested_oem": "OEM-2",
            "name": "Part 2",
            "quantity": 1,
            "customer_unit_rub": 9500,
            "offer_source": "warehouse",
            "warehouse_id": 1,
            "payment_route": "direct_partner",
        },
    ]


class P5Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "p5.db")
        create_shared_schema(self.db)
        init_core(self.db)

    def persist_valid_order(self, order_id="I-P5-1", status="assembled_unpaid"):
        prepared = prepare_internal_order_write(
            order_id=order_id,
            client_id="client:1001",
            status=status,
            items=valid_items(),
        )
        with sqlite3.connect(self.db) as c:
            c.execute("BEGIN IMMEDIATE")
            insert_disposable_shared_order(c, prepared)
            c.commit()
        return prepared

    def seed_terms(self, *, version="T1", source_ref="PRICE-LIST-2026-10"):
        for oem, unit in (("OEM-1", 8000), ("OEM-2", 8575)):
            seed_approved_term(
                self.db,
                warehouse_id=1,
                terms_version=version,
                oem=oem,
                partner_unit_rub=unit,
                source_type="warehouse_price_list",
                source_ref=source_ref,
            )

    def terms_context(self, warehouse=1, authorized=True):
        return TermsConfirmationContext(
            actual_actor=f"warehouse:{warehouse}:terms-user",
            effective_subject=f"warehouse:{warehouse}",
            warehouse_id=warehouse,
            authorized=authorized,
        )

    def trusted_snapshot(
        self,
        *,
        key="terms-1",
        version="T1",
        source_ref="PRICE-LIST-2026-10",
        context=None,
        failure_hook=None,
    ):
        return confirm_terms_and_create_payment_snapshot(
            self.db,
            confirmation_key=key,
            order_id="I-P5-1",
            client_id="client:1001",
            warehouse_order_number=None,
            terms_version=version,
            terms_source_type="warehouse_price_list",
            terms_source_ref=source_ref,
            confirmed_quantities={1: 1, 2: 1},
            actor_context=context or self.terms_context(),
            _failure_hook=failure_hook,
        )

    def eligibility_context(
        self,
        *,
        warehouse=1,
        authorized=True,
        permissions=frozenset({CONFIRM_PAYMENT_PERMISSION}),
        statuses=("assembled_unpaid",),
    ):
        return PaymentEligibilityContext(
            actual_actor=f"warehouse:{warehouse}:payment-user",
            warehouse_id=warehouse,
            authorized=authorized,
            permissions=permissions,
            payment_ready_statuses=statuses,
        )

    def dump(self):
        with sqlite3.connect(self.db) as c:
            return "\n".join(c.iterdump())


class StrictPersistenceBoundaryTests(P5Case):
    def test_valid_values_are_preserved_before_and_after_insert(self):
        prepared = self.persist_valid_order()
        self.assertEqual(prepared.customer_amount_rub, 19500)
        self.assertEqual(prepared.warehouse_id, 1)
        self.assertEqual(prepared.payment_route, "direct_partner")
        self.assertEqual([x.quantity for x in prepared.items], [1, 1])
        with sqlite3.connect(self.db) as c:
            rows = c.execute(
                """SELECT quantity,customer_unit_rub,warehouse_id,offer_source,
                          payment_route,oem
                   FROM order_items ORDER BY id"""
            ).fetchall()
        self.assertEqual(
            rows,
            [
                (1, 10000, 1, "warehouse", "direct_partner", "OEM-1"),
                (1, 9500, 1, "warehouse", "direct_partner", "OEM-2"),
            ],
        )

    def test_invalid_money_rejected_before_any_sql_write(self):
        bad_values = [True, 100.0, "100", "100.5", float("nan"), float("inf"), -1]
        for index, bad in enumerate(bad_values):
            items = valid_items()
            items[0]["customer_unit_rub"] = bad
            with self.subTest(value=repr(bad)):
                with self.assertRaises((TypeError, ValueError)):
                    prepare_internal_order_write(
                        order_id=f"I-BAD-M-{index}",
                        client_id="client:1001",
                        status="assembled_unpaid",
                        items=items,
                    )
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM order_items").fetchone()[0], 0)

    def test_invalid_quantity_rejected_before_any_sql_write(self):
        for index, bad in enumerate([True, 0, -1, 1.0, "1"]):
            items = valid_items()
            items[0]["quantity"] = bad
            with self.subTest(value=repr(bad)):
                with self.assertRaises((TypeError, ValueError)):
                    prepare_internal_order_write(
                        order_id=f"I-BAD-Q-{index}",
                        client_id="client:1001",
                        status="assembled_unpaid",
                        items=items,
                    )
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_invalid_warehouse_scope_source_and_route_fail_before_insert(self):
        variants = []
        x = valid_items(); x[0]["warehouse_id"] = True; variants.append(x)
        x = valid_items(); x[1]["warehouse_id"] = 2; variants.append(x)
        x = valid_items(); x[0]["offer_source"] = "usa"; variants.append(x)
        x = valid_items(); x[0]["payment_route"] = "forged"; variants.append(x)
        for i, items in enumerate(variants):
            with self.subTest(i=i):
                with self.assertRaises((TypeError, ValueError)):
                    prepare_internal_order_write(
                        order_id=f"I-BAD-S-{i}",
                        client_id="client:1001",
                        status="assembled_unpaid",
                        items=items,
                    )
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def test_strict_persistence_then_trusted_snapshot_keeps_exact_c_and_fingerprint(self):
        prepared = self.persist_valid_order()
        self.seed_terms()
        result = self.trusted_snapshot()
        self.assertEqual(result["partner_amount_rub"], 16575)
        self.assertEqual(result["snapshot"]["customer_amount_rub"], prepared.customer_amount_rub)
        with sqlite3.connect(self.db) as c:
            proof = c.execute(
                """SELECT fingerprint_version,fingerprint_sha256,item_count
                   FROM internal_order_terms_integrity_v01 WHERE order_id='I-P5-1'"""
            ).fetchone()
        self.assertEqual(proof[0], "order-items-v1")
        self.assertEqual(proof[2], 2)
        self.assertEqual(len(proof[1]), 64)


class TrustedTermsProvenanceTests(P5Case):
    def setUp(self):
        super().setUp()
        self.persist_valid_order()
        self.seed_terms()

    def test_trusted_boundary_calculates_p_and_binds_full_provenance_chain(self):
        result = self.trusted_snapshot()
        self.assertEqual(result["partner_amount_rub"], 16575)
        self.assertTrue(result["provenance"]["verified"])
        provenance = verify_snapshot_terms_provenance(self.db, "I-P5-1")
        self.assertEqual(provenance["confirmation_key"], "terms-1")
        self.assertEqual(provenance["calculated_partner_amount_rub"], 16575)
        self.assertEqual(provenance["terms_version"], "T1")
        self.assertEqual(provenance["terms_source_ref"], "PRICE-LIST-2026-10")

    def test_low_level_snapshot_bypass_has_no_trusted_provenance(self):
        create_payment_snapshot(
            self.db,
            order_id="I-P5-1",
            client_id="client:1001",
            partner_amount_rub=16575,
            created_by="untrusted-caller",
            expected_warehouse_id=1,
        )
        with self.assertRaisesRegex(ValueError, "TRUSTED_TERMS_PROVENANCE_REQUIRED"):
            verify_snapshot_terms_provenance(self.db, "I-P5-1")

    def test_unauthorized_or_forged_warehouse_cannot_create_trusted_snapshot(self):
        with self.assertRaisesRegex(PermissionError, "NOT_AUTHORIZED"):
            self.trusted_snapshot(context=self.terms_context(authorized=False))
        with self.assertRaisesRegex(PermissionError, "WAREHOUSE_SCOPE_MISMATCH"):
            self.trusted_snapshot(context=self.terms_context(warehouse=2))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM internal_order_payment_snapshots_v01").fetchone()[0],
                0,
            )

    def test_unapproved_or_missing_terms_are_rejected(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_warehouse_terms_v01
                   SET approved=0 WHERE warehouse_id=1 AND terms_version='T1' AND oem='OEM-2'"""
            )
        with self.assertRaisesRegex(ValueError, "APPROVED_WAREHOUSE_TERM_REQUIRED"):
            self.trusted_snapshot()

    def test_terms_source_change_after_confirmation_does_not_rewrite_historical_proof(self):
        self.trusted_snapshot()
        original = verify_snapshot_terms_provenance(self.db, "I-P5-1")
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_warehouse_terms_v01
                   SET approved=0,partner_unit_rub=999999,source_ref='WITHDRAWN'
                   WHERE warehouse_id=1 AND terms_version='T1'"""
            )
        again = verify_snapshot_terms_provenance(self.db, "I-P5-1")
        self.assertEqual(again["evidence_sha256"], original["evidence_sha256"])
        self.assertEqual(again["calculated_partner_amount_rub"], 16575)
        self.assertEqual(get_payment_snapshot(self.db, "I-P5-1")["partner_amount_rub"], 16575)

    def test_exact_replay_does_not_create_second_confirmation_or_provenance(self):
        first = self.trusted_snapshot()
        second = self.trusted_snapshot()
        self.assertFalse(first["replayed"])
        self.assertTrue(second["replayed"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_terms_confirmation_audit_v01"
            ).fetchone()[0], 1)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_order_snapshot_terms_provenance_v01"
            ).fetchone()[0], 1)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_order_payment_snapshots_v01"
            ).fetchone()[0], 1)

    def test_changed_version_same_confirmation_key_is_conflict(self):
        self.trusted_snapshot()
        seed_approved_term(
            self.db,warehouse_id=1,terms_version="T2",oem="OEM-1",
            partner_unit_rub=8000,source_type="warehouse_price_list",source_ref="PL2"
        )
        seed_approved_term(
            self.db,warehouse_id=1,terms_version="T2",oem="OEM-2",
            partner_unit_rub=8575,source_type="warehouse_price_list",source_ref="PL2"
        )
        with self.assertRaisesRegex(ValueError, "confirmation conflict"):
            self.trusted_snapshot(version="T2", source_ref="PL2")

    def test_existing_conflicting_low_level_snapshot_rolls_back_terms_audit(self):
        create_payment_snapshot(
            self.db,
            order_id="I-P5-1",
            client_id="client:1001",
            partner_amount_rub=16000,
            created_by="legacy-low-level",
            expected_warehouse_id=1,
        )
        with self.assertRaisesRegex(ValueError, "snapshot conflict"):
            self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_terms_confirmation_audit_v01"
            ).fetchone()[0], 0)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_order_snapshot_terms_provenance_v01"
            ).fetchone()[0], 0)

    def test_tampered_audit_evidence_fails_provenance(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_terms_confirmation_audit_v01
                   SET calculated_partner_amount_rub=16574
                   WHERE confirmation_key='terms-1'"""
            )
        with self.assertRaises((ValueError, TypeError)):
            verify_snapshot_terms_provenance(self.db, "I-P5-1")

    def test_tampered_provenance_hash_fails(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_order_snapshot_terms_provenance_v01
                   SET evidence_sha256='deadbeef'
                   WHERE order_id='I-P5-1'"""
            )
        with self.assertRaisesRegex(ValueError, "PROVENANCE_MISMATCH"):
            verify_snapshot_terms_provenance(self.db, "I-P5-1")

    def test_failure_after_provenance_insert_rolls_back_whole_creation(self):
        def fail(step):
            if step == "provenance":
                raise RuntimeError("injected provenance failure")
        with self.assertRaisesRegex(RuntimeError, "injected provenance"):
            self.trusted_snapshot(failure_hook=fail)
        with sqlite3.connect(self.db) as c:
            for table in (
                "internal_terms_confirmation_audit_v01",
                "internal_order_payment_snapshots_v01",
                "internal_order_terms_integrity_v01",
                "internal_order_snapshot_terms_provenance_v01",
            ):
                self.assertEqual(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)

    def test_concurrent_same_confirmation_serializes_to_one_creation_plus_replay(self):
        def call():
            return self.trusted_snapshot()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: call(), range(2)))
        self.assertEqual(sorted(x["replayed"] for x in results), [False, True])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_terms_confirmation_audit_v01"
            ).fetchone()[0], 1)
            self.assertEqual(c.execute(
                "SELECT COUNT(*) FROM internal_order_snapshot_terms_provenance_v01"
            ).fetchone()[0], 1)


class PaymentEligibilityTests(P5Case):
    def setUp(self):
        super().setUp()
        self.persist_valid_order()
        self.seed_terms()

    def evaluate(self, context=None):
        return evaluate_direct_payment_eligibility(
            self.db,
            order_id="I-P5-1",
            warehouse_id=1,
            context=context or self.eligibility_context(),
        )

    def codes(self, result):
        return {x["code"] for x in result["reasons"]}

    def test_due_is_not_payable_when_snapshot_was_created_via_low_level_bypass(self):
        create_payment_snapshot(
            self.db,
            order_id="I-P5-1",
            client_id="client:1001",
            partner_amount_rub=16575,
            created_by="bypass",
            expected_warehouse_id=1,
        )
        result = self.evaluate()
        self.assertFalse(result["eligible"])
        self.assertIn("TRUSTED_TERMS_PROVENANCE_REQUIRED", self.codes(result))
        read = read_order(
            self.db,
            warehouse_id=1,
            order_id="I-P5-1",
            context=WarehouseReadContext(
                actual_actor="warehouse:1:reader",
                warehouse_id=1,
                authorized=True,
            ),
        )
        self.assertEqual(read["order"]["payment_status"], "DUE")
        self.assertEqual(read["order"]["partner_amount_rub"], 16575)

    def test_trusted_due_order_can_be_eligible_under_explicit_mock_policy(self):
        self.trusted_snapshot()
        result = self.evaluate()
        self.assertTrue(result["eligible"])
        self.assertEqual(result["reasons"], [])

    def test_unresolved_payment_ready_policy_fails_closed(self):
        self.trusted_snapshot()
        result = self.evaluate(
            self.eligibility_context(statuses=())
        )
        self.assertFalse(result["eligible"])
        self.assertIn("PAYMENT_READY_STATUS_POLICY_REQUIRED", self.codes(result))

    def test_missing_permission_or_wrong_warehouse_fails_closed(self):
        self.trusted_snapshot()
        no_perm = self.evaluate(
            self.eligibility_context(permissions=frozenset())
        )
        self.assertFalse(no_perm["eligible"])
        self.assertIn("CONFIRM_PAYMENT_PERMISSION_REQUIRED", self.codes(no_perm))
        cross = evaluate_direct_payment_eligibility(
            self.db,
            order_id="I-P5-1",
            warehouse_id=1,
            context=self.eligibility_context(warehouse=2),
        )
        self.assertFalse(cross["eligible"])
        self.assertIn("ACTOR_WAREHOUSE_SCOPE_MISMATCH", self.codes(cross))

    def test_changed_order_items_invalidates_eligibility(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE order_items SET quantity=2 WHERE id=1")
        result = self.evaluate()
        self.assertFalse(result["eligible"])
        self.assertIn("INTERNAL_ORDER_TERMS_DRIFT", self.codes(result))

    def test_status_change_even_to_another_allowed_status_is_not_same_snapshot(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE orders SET status='other_ready' WHERE order_id='I-P5-1'")
        result = self.evaluate(
            self.eligibility_context(statuses=("assembled_unpaid", "other_ready"))
        )
        self.assertFalse(result["eligible"])
        self.assertIn("ORDER_STATUS_CHANGED_SINCE_SNAPSHOT", self.codes(result))

    def test_paid_is_not_eligible(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_order_payment_state_v01
                   SET payment_status='PAID' WHERE order_id='I-P5-1'"""
            )
        result = self.evaluate()
        self.assertFalse(result["eligible"])
        self.assertIn("PAYMENT_STATUS_NOT_DUE", self.codes(result))

    def test_tampered_provenance_fails_closed(self):
        self.trusted_snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_order_snapshot_terms_provenance_v01
                   SET calculated_partner_amount_rub=1 WHERE order_id='I-P5-1'"""
            )
        result = self.evaluate()
        self.assertFalse(result["eligible"])
        self.assertTrue(
            {"TRUSTED_TERMS_PROVENANCE_MISMATCH", "TRUSTED_TERMS_PROVENANCE_REQUIRED"}
            & self.codes(result)
        )

    def test_evaluator_is_read_only(self):
        self.trusted_snapshot()
        before = self.dump()
        for _ in range(3):
            self.evaluate()
        after = self.dump()
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main(verbosity=2)
