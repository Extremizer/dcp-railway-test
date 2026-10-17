"""Internal Order P5 isolated trusted-boundary regressions."""
from pathlib import Path
import json
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import create_payment_snapshot, init as core_init
from internal_order_p5_boundary import (
    CONFIRM_PAYMENT_PERMISSION,
    PaymentEligibilityContext,
    TrustedTermsCommandContext,
    create_trusted_terms_payment_snapshot,
    evaluate_payment_eligibility,
    validate_internal_persistence_line,
    verify_terms_provenance,
)
from internal_order_terms_v01 import init as terms_init, seed_approved_term


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
              offer_source TEXT NOT NULL,
              warehouse_id INTEGER,
              oem TEXT,
              quantity INTEGER NOT NULL,
              customer_unit_rub INTEGER,
              payment_route TEXT NOT NULL
            );
            """
        )


class P5BoundaryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "p5.db")
        create_schema(self.db)
        core_init(self.db)
        terms_init(self.db)
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                ("I-P5-1", "assembled_unpaid", "2026-10-14T01:00:00+02:00", 7001),
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,offer_source,warehouse_id,oem,quantity,
                     customer_unit_rub,payment_route)
                   VALUES('I-P5-1',1,'warehouse',1,'OEM-1',1,10000,'direct_partner')"""
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,offer_source,warehouse_id,oem,quantity,
                     customer_unit_rub,payment_route)
                   VALUES('I-P5-1',2,'warehouse',1,'OEM-2',1,9500,'direct_partner')"""
            )
        for oem, p in (("OEM-1", 8000), ("OEM-2", 8575)):
            seed_approved_term(
                self.db,
                warehouse_id=1,
                terms_version="T1",
                oem=oem,
                partner_unit_rub=p,
                source_type="warehouse_price_list",
                source_ref="PRICE-LIST-2026-10",
            )

    def trusted_context(self, *, warehouse=1, authorized=True, permission=True):
        return TrustedTermsCommandContext(
            actual_actor=f"warehouse:{warehouse}:user-7",
            effective_subject=f"warehouse:{warehouse}",
            warehouse_id=warehouse,
            authorized=authorized,
            permissions=(frozenset({CONFIRM_PAYMENT_PERMISSION}) if permission else frozenset()),
        )

    def eligibility_context(self, *, warehouse=1, authorized=True, permission=True):
        return PaymentEligibilityContext(
            actual_actor=f"warehouse:{warehouse}:user-7",
            warehouse_id=warehouse,
            authorized=authorized,
            permissions=(frozenset({CONFIRM_PAYMENT_PERMISSION}) if permission else frozenset()),
        )

    def create_trusted(self):
        return create_trusted_terms_payment_snapshot(
            self.db,
            confirmation_key="confirm-terms-1",
            order_id="I-P5-1",
            client_id="client:7001",
            warehouse_order_number=None,
            terms_version="T1",
            terms_source_type="warehouse_price_list",
            terms_source_ref="PRICE-LIST-2026-10",
            confirmed_quantities={1: 1, 2: 1},
            context=self.trusted_context(),
        )

    def test_strict_persistence_boundary_rejects_coercion(self):
        good = dict(
            order_id="I-P5-X", order_item_id=1, offer_source="warehouse",
            warehouse_id=1, oem="OEM-X", quantity=2, customer_unit_rub=10100,
            payment_route="direct_partner",
        )
        result = validate_internal_persistence_line(**good)
        self.assertEqual(result["quantity"], 2)
        self.assertEqual(result["customer_unit_rub"], 10100)
        self.assertEqual(len(result["fingerprint_sha256"]), 64)
        for field, bad in (
            ("quantity", True), ("quantity", 1.0), ("quantity", "1"), ("quantity", 0),
            ("customer_unit_rub", True), ("customer_unit_rub", 100.0),
            ("customer_unit_rub", "100"), ("customer_unit_rub", -1),
            ("warehouse_id", "1"),
        ):
            with self.subTest(field=field, bad=bad):
                payload = dict(good)
                payload[field] = bad
                with self.assertRaises((TypeError, ValueError)):
                    validate_internal_persistence_line(**payload)

    def test_trusted_command_has_no_browser_p_and_enforces_permission(self):
        result = self.create_trusted()
        self.assertEqual(result["partner_amount_rub"], 16575)
        self.assertEqual(result["snapshot"]["partner_amount_rub"], 16575)
        with self.assertRaisesRegex(PermissionError, "CONFIRM_PAYMENT"):
            create_trusted_terms_payment_snapshot(
                self.db, confirmation_key="no-permission", order_id="I-P5-1",
                client_id="client:7001", warehouse_order_number=None,
                terms_version="T1", terms_source_type="warehouse_price_list",
                terms_source_ref="PRICE-LIST-2026-10",
                confirmed_quantities={1: 1, 2: 1},
                context=self.trusted_context(permission=False),
            )

    def test_provenance_survives_current_source_deletion(self):
        self.create_trusted()
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM internal_warehouse_terms_v01")
        proof = verify_terms_provenance(self.db, "I-P5-1")
        self.assertEqual(proof["calculated_partner_amount_rub"], 16575)
        self.assertEqual(proof["terms_source_ref"], "PRICE-LIST-2026-10")

    def test_tampered_evidence_fails_closed(self):
        self.create_trusted()
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                "SELECT confirmed_lines_json FROM internal_terms_confirmation_audit_v01 WHERE confirmation_key='confirm-terms-1'"
            ).fetchone()
            evidence = json.loads(row[0])
            evidence[0]["partner_line_rub"] = 1
            c.execute(
                "UPDATE internal_terms_confirmation_audit_v01 SET confirmed_lines_json=? WHERE confirmation_key='confirm-terms-1'",
                (json.dumps(evidence, separators=(",", ":")),),
            )
        with self.assertRaisesRegex(ValueError, "TAMPERED_TERMS_EVIDENCE"):
            verify_terms_provenance(self.db, "I-P5-1")

    def test_due_is_not_payable_without_owner_status_decision(self):
        self.create_trusted()
        result = evaluate_payment_eligibility(
            self.db, order_id="I-P5-1", context=self.eligibility_context()
        )
        self.assertFalse(result["payable"])
        self.assertTrue(result["checks"]["payment_due"])
        self.assertIn("PAYMENT_READY_STATUS_NOT_CONFIGURED", result["reasons"])

    def test_all_p5_proofs_can_be_eligible_only_with_explicit_status_config(self):
        self.create_trusted()
        result = evaluate_payment_eligibility(
            self.db, order_id="I-P5-1", context=self.eligibility_context(),
            payment_ready_statuses={"assembled_unpaid"},
        )
        self.assertTrue(result["payable"])
        self.assertEqual(result["reasons"], [])

    def test_cross_warehouse_and_missing_permission_fail_closed(self):
        self.create_trusted()
        cross = evaluate_payment_eligibility(
            self.db, order_id="I-P5-1", context=self.eligibility_context(warehouse=2),
            payment_ready_statuses={"assembled_unpaid"},
        )
        self.assertFalse(cross["payable"])
        self.assertIn("CROSS_WAREHOUSE_DENY", cross["reasons"])
        denied = evaluate_payment_eligibility(
            self.db, order_id="I-P5-1", context=self.eligibility_context(permission=False),
            payment_ready_statuses={"assembled_unpaid"},
        )
        self.assertFalse(denied["payable"])
        self.assertIn("CONFIRM_PAYMENT_PERMISSION_REQUIRED", denied["reasons"])

    def test_legacy_snapshot_remains_readable_but_nonpayable(self):
        create_payment_snapshot(
            self.db, order_id="I-P5-1", client_id="client:7001",
            partner_amount_rub=16575, created_by="legacy-p3", expected_warehouse_id=1,
        )
        result = evaluate_payment_eligibility(
            self.db, order_id="I-P5-1", context=self.eligibility_context(),
            payment_ready_statuses={"assembled_unpaid"},
        )
        self.assertFalse(result["payable"])
        self.assertIn("TRUSTED_TERMS_PROVENANCE_REQUIRED", result["reasons"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
