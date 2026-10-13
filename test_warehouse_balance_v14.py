"""Disposable SQLite regression for CONTRACT v1.4 warehouse balance."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sqlite3
import tempfile
import unittest

import warehouse_balance_v14 as wb


class WarehouseBalanceV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "test.db")
        wb.init(self.db)

    def confirm(self, cid="pay-1", warehouse=1, order="I12J-4582",
                customer=19500, partner=16575):
        return wb.confirm_direct_payment_and_accrue(
            self.db,
            confirmation_id=cid,
            warehouse_id=warehouse,
            order_id=order,
            customer_amount_rub=customer,
            partner_amount_rub=partner,
            actual_actor="warehouse:user-1",
            effective_subject="warehouse:1",
        )

    def test_full_payment_posts_difference_once_and_is_immediately_available(self):
        before = wb.balance_summary(self.db, 1)
        self.assertEqual(before["available_rub"], 0)
        result = self.confirm()
        self.assertEqual(result["difference_rub"], 2925)
        self.assertFalse(result["replayed"])
        self.assertEqual(result["balance"]["available_rub"], 2925)

        replay = self.confirm()
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["balance"]["available_rub"], 2925)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute("SELECT COUNT(*) FROM warehouse_internal_balance_ledger").fetchone()[0],
                1,
            )

    def test_confirmation_conflict_does_not_mutate_balance(self):
        self.confirm()
        with self.assertRaisesRegex(ValueError, "confirmation conflict"):
            self.confirm(customer=20000)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_balances_are_isolated_per_warehouse(self):
        self.confirm()
        self.confirm(cid="pay-2", warehouse=2, order="I12J-4583",
                     customer=18000, partner=16500)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)
        self.assertEqual(wb.balance_summary(self.db, 2)["available_rub"], 1500)

    def test_full_coverage_only(self):
        self.confirm()
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_WAREHOUSE_BALANCE"):
            wb.reserve_full_balance(
                self.db, reservation_id="r1", warehouse_id=1, order_id="target",
                required_amount_rub=2926, actual_actor="admin:1"
            )
        self.assertEqual(wb.balance_summary(self.db, 1)["reserved_rub"], 0)

        reserved = wb.reserve_full_balance(
            self.db, reservation_id="r2", warehouse_id=1, order_id="target",
            required_amount_rub=2925, actual_actor="admin:1"
        )
        self.assertEqual(reserved["amount_rub"], 2925)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 0)
        applied = wb.apply_reserved_balance(
            self.db, reservation_id="r2", actual_actor="admin:1"
        )
        self.assertEqual(applied["balance"]["available_rub"], 0)
        self.assertEqual(applied["balance"]["spent_rub"], 2925)
        replay = wb.apply_reserved_balance(
            self.db, reservation_id="r2", actual_actor="admin:1"
        )
        self.assertEqual(replay["status"], "applied")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM warehouse_internal_balance_ledger "
                    "WHERE event_type='WAREHOUSE_BALANCE_APPLIED'"
                ).fetchone()[0],
                1,
            )

    def test_concurrent_full_coverage_reservation_cannot_double_spend(self):
        self.confirm()
        def reserve(args):
            rid, oid = args
            try:
                wb.reserve_full_balance(
                    self.db, reservation_id=rid, warehouse_id=1, order_id=oid,
                    required_amount_rub=2925, actual_actor="admin:1"
                )
                return "reserved"
            except ValueError as exc:
                self.assertIn("INSUFFICIENT_WAREHOUSE_BALANCE", str(exc))
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            result = list(pool.map(reserve, [("a","order-a"),("b","order-b")]))
        self.assertCountEqual(result, ["reserved", "rejected"])
        self.assertEqual(wb.balance_summary(self.db, 1)["reserved_rub"], 2925)

    def test_release_restores_availability(self):
        self.confirm()
        wb.reserve_full_balance(
            self.db, reservation_id="r", warehouse_id=1, order_id="target",
            required_amount_rub=2925, actual_actor="admin:1"
        )
        wb.release_reserved_balance(self.db, reservation_id="r", actual_actor="admin:1")
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_append_only_correction_never_rewrites_original(self):
        self.confirm()
        event = wb.post_correction(
            self.db, correction_id="cancel-1", warehouse_id=1,
            order_id="I12J-4582", amount_rub=-2925,
            actual_actor="admin:1", reason="approved cancellation"
        )
        self.assertGreater(event, 0)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 0)
        same = wb.post_correction(
            self.db, correction_id="cancel-1", warehouse_id=1,
            order_id="I12J-4582", amount_rub=-2925,
            actual_actor="admin:1", reason="approved cancellation"
        )
        self.assertEqual(event, same)
        with sqlite3.connect(self.db) as c:
            rows = c.execute(
                "SELECT event_type,amount_rub FROM warehouse_internal_balance_ledger "
                "ORDER BY id"
            ).fetchall()
        self.assertEqual(rows, [("DIRECT_PAYMENT_MARGIN", 2925), ("CORRECTION", -2925)])

    def test_correction_idempotency_strict_identity(self):
        self.confirm()
        base = dict(
            correction_id="corr-1",
            warehouse_id=1,
            order_id="I12J-4582",
            amount_rub=-2925,
            actual_actor="admin:1",
            reason="approved cancellation",
            effective_subject="warehouse:1",
        )
        event_id = wb.post_correction(self.db, **base)
        self.assertGreater(event_id, 0)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 0)

        # Exact replay returns the original event.
        self.assertEqual(wb.post_correction(self.db, **base), event_id)

        conflicts = [
            dict(base, reason="different reason"),
            dict(base, amount_rub=-2924),
            dict(base, warehouse_id=2),
            dict(base, order_id="I12J-9999"),
            dict(base, actual_actor="admin:2"),
            dict(base, effective_subject="warehouse:2"),
        ]
        for replay in conflicts:
            with self.subTest(replay=replay):
                before_1 = wb.balance_summary(self.db, 1)
                before_2 = wb.balance_summary(self.db, 2)
                with self.assertRaisesRegex(
                    ValueError, "warehouse balance correction conflict"
                ):
                    wb.post_correction(self.db, **replay)
                self.assertEqual(wb.balance_summary(self.db, 1), before_1)
                self.assertEqual(wb.balance_summary(self.db, 2), before_2)

        with sqlite3.connect(self.db) as c:
            ledger = c.execute(
                """SELECT event_type,amount_rub,warehouse_id,order_id,
                          actual_actor,effective_subject
                   FROM warehouse_internal_balance_ledger
                   ORDER BY id"""
            ).fetchall()
            reasons = c.execute(
                """SELECT ledger_event_id,reason
                   FROM warehouse_balance_correction_reasons
                   ORDER BY ledger_event_id"""
            ).fetchall()
        self.assertEqual(
            ledger,
            [
                ("DIRECT_PAYMENT_MARGIN", 2925, 1, "I12J-4582",
                 "warehouse:user-1", "warehouse:1"),
                ("CORRECTION", -2925, 1, "I12J-4582",
                 "admin:1", "warehouse:1"),
            ],
        )
        self.assertEqual(reasons, [(event_id, "approved cancellation")])

    def test_negative_margin_stays_blocked_pending_owner_decision(self):
        with self.assertRaisesRegex(ValueError, "NEGATIVE_INTERNAL_MARGIN_NOT_APPROVED"):
            self.confirm(customer=10000, partner=11000)


if __name__ == "__main__":
    unittest.main()
