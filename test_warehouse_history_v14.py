"""CONTRACT v1.4 warehouse history read-model regression."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

import warehouse_balance_v14 as wb
import warehouse_history_v14 as history


class WarehouseHistoryV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "history.db")
        wb.init(self.db)
        with sqlite3.connect(self.db) as c:
            c.executescript(
                """CREATE TABLE orders(
                     order_id TEXT PRIMARY KEY,
                     warehouse_order_number TEXT,
                     created_at TEXT
                   );
                   CREATE TABLE order_items(
                     id INTEGER PRIMARY KEY AUTOINCREMENT,
                     order_id TEXT NOT NULL,
                     offer_source TEXT NOT NULL,
                     warehouse_id INTEGER,
                     quantity INTEGER NOT NULL,
                     customer_unit_rub INTEGER
                   );"""
            )

    def add_order(self, order_id, customer_total, warehouse_no=None, date="2026-10-13"):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders(order_id,warehouse_order_number,created_at) VALUES(?,?,?)",
                (order_id, warehouse_no, date),
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,offer_source,warehouse_id,quantity,customer_unit_rub)
                   VALUES(?,'warehouse',1,1,?)""",
                (order_id, customer_total),
            )

    def accrue(self, n, difference, partner=10000):
        order = f"I-H-{n}"
        customer = partner + difference
        self.add_order(order, customer, warehouse_no=f"W{n}")
        wb.confirm_direct_payment_and_accrue(
            self.db,
            confirmation_id=f"pay-{n}",
            warehouse_id=1,
            order_id=order,
            customer_amount_rub=customer,
            partner_amount_rub=partner,
            actual_actor="warehouse:user1",
            effective_subject="warehouse:1",
        )
        return order

    def test_control_total_is_full_ledger_not_visible_page(self):
        self.accrue(1, 2925)
        self.accrue(2, 1500)
        self.accrue(3, 3000)
        self.accrue(4, 4500)
        self.add_order("I-SPEND", 9000, warehouse_no="W-SPEND")
        wb.reserve_full_balance(
            self.db,
            reservation_id="spend-8500",
            warehouse_id=1,
            order_id="I-SPEND",
            required_amount_rub=8500,
            actual_actor="admin:1",
        )
        wb.apply_reserved_balance(
            self.db,
            reservation_id="spend-8500",
            actual_actor="admin:1",
        )

        full = history.read_history(self.db, 1, limit=1)
        self.assertEqual(full["summary"]["posted_balance_rub"], 3425)
        self.assertEqual(full["summary"]["reserved_balance_rub"], 0)
        self.assertEqual(full["summary"]["available_balance_rub"], 3425)
        self.assertEqual(full["summary"]["applied_balance_rub"], 8500)
        self.assertEqual(len(full["rows"]), 1)

        # Search can hide every row; financial total still uses all ledger rows.
        hidden = history.read_history(self.db, 1, search="does-not-exist", limit=1)
        self.assertEqual(hidden["rows"], [])
        self.assertEqual(hidden["summary"]["available_balance_rub"], 3425)

    def test_active_reservation_is_separate_from_posted_and_available(self):
        self.accrue(1, 5000, partner=10000)
        self.add_order("I-RES", 3000, warehouse_no="W-RES")
        wb.reserve_full_balance(
            self.db,
            reservation_id="r-3000",
            warehouse_id=1,
            order_id="I-RES",
            required_amount_rub=3000,
            actual_actor="admin:1",
        )
        total = history.summary(self.db, 1)
        self.assertEqual(total["posted_balance_rub"], 5000)
        self.assertEqual(total["reserved_balance_rub"], 3000)
        self.assertEqual(total["available_balance_rub"], 2000)

    def test_unpaid_order_has_blank_balance_cell(self):
        self.add_order("I-UNPAID", 19500, warehouse_no="5234")
        rows = history.order_rows(self.db, 1, search="I-UNPAID")
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["extremizer_order_number"], "I-UNPAID")
        self.assertEqual(row["warehouse_order_number"], "5234")
        self.assertEqual(row["customer_amount_rub"], 19500)
        self.assertIsNone(row["partner_amount_rub"])
        self.assertIsNone(row["balance_rub"])

    def test_direct_payment_row_shows_historical_positive_effect(self):
        order = self.accrue(1, 2925, partner=16575)
        row = history.order_rows(self.db, 1, search=order)[0]
        self.assertEqual(row["customer_amount_rub"], 19500)
        self.assertEqual(row["partner_amount_rub"], 16575)
        self.assertEqual(row["partner_amount_source"], "direct_payment_confirmation")
        self.assertEqual(row["balance_rub"], 2925)

    def test_balance_spend_row_shows_negative_p_and_does_not_rewrite_source_order(self):
        source = self.accrue(1, 9000, partner=10000)
        self.add_order("I-SPEND", 8500, warehouse_no="W-SPEND")
        wb.reserve_full_balance(
            self.db,
            reservation_id="r-spend",
            warehouse_id=1,
            order_id="I-SPEND",
            required_amount_rub=8500,
            actual_actor="admin:1",
        )
        wb.apply_reserved_balance(
            self.db,
            reservation_id="r-spend",
            actual_actor="admin:1",
        )
        rows = {
            x["extremizer_order_number"]: x
            for x in history.order_rows(self.db, 1, limit=20)
        }
        self.assertEqual(rows[source]["balance_rub"], 9000)
        self.assertEqual(rows["I-SPEND"]["partner_amount_rub"], 8500)
        self.assertEqual(
            rows["I-SPEND"]["partner_amount_source"],
            "warehouse_balance_reservation",
        )
        self.assertEqual(rows["I-SPEND"]["balance_rub"], -8500)

    def test_read_model_does_not_require_or_merge_legacy_partner_settlements(self):
        # Deliberately create a legacy-looking table with a huge unrelated value.
        with sqlite3.connect(self.db) as c:
            c.execute(
                """CREATE TABLE warehouse_partner_ledger(
                     warehouse_id INTEGER, amount_rub INTEGER
                   )"""
            )
            c.execute("INSERT INTO warehouse_partner_ledger VALUES(1,999999)")
        self.accrue(1, 2925)
        total = history.summary(self.db, 1)
        self.assertEqual(total["posted_balance_rub"], 2925)
        self.assertEqual(total["available_balance_rub"], 2925)


if __name__ == "__main__":
    unittest.main()
