from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

import warehouse_balance_v14 as wb
import warehouse_history_v14 as history
from web2_finance_read_model_adapter import (
    BUSINESS_STATUS_TO_KEY,
    ReadModelContractError,
    adapt_warehouse_history,
    to_fixture_dict,
)


class Web2FinanceReadModelAdapterTests(unittest.TestCase):
    def setUp(self):
        # Python 3.14 on Windows keeps sqlite handles alive longer in the
        # existing Theme 1 balance helper; do not turn that interpreter-only
        # cleanup timing into an adapter assertion failure.
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "adapter.db")
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

    def add_order(self, order_id, customer_total, warehouse_no, date="2026-10-13"):
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

    def accrue(self, order_id, warehouse_no, customer, partner, confirmation):
        self.add_order(order_id, customer, warehouse_no)
        wb.confirm_direct_payment_and_accrue(
            self.db,
            confirmation_id=confirmation,
            warehouse_id=1,
            order_id=order_id,
            customer_amount_rub=customer,
            partner_amount_rub=partner,
            actual_actor="warehouse:user1",
            effective_subject="warehouse:1",
        )

    def test_maps_actual_theme1_read_model_without_recomputing_finance(self):
        self.accrue("I-A", "5241", 19500, 16575, "pay-a")
        raw = history.read_history(self.db, 1)
        model = adapt_warehouse_history(
            raw,
            order_metadata_by_id={
                "I-A": {
                    "status": "Собран, оплачен",
                    "client": "Клиент <A>",
                    "oems": ["417300574"],
                }
            },
        )
        self.assertEqual(model.summary.posted_rub, 2925)
        self.assertEqual(model.summary.full_history_balance_rub, 2925)
        self.assertEqual(model.rows[0].warehouse_id, 1)
        self.assertEqual(model.rows[0].extremizer_order_no, "I-A")
        self.assertEqual(model.rows[0].warehouse_order_no, "5241")
        self.assertEqual(model.rows[0].customer_amount_rub, 19500)
        self.assertEqual(model.rows[0].partner_amount_rub, 16575)
        self.assertTrue(model.rows[0].partner_amount_authoritative)
        self.assertEqual(
            model.rows[0].payment_source,
            "direct_payment_confirmation",
        )
        self.assertEqual(model.rows[0].balance_delta_rub, 2925)
        self.assertEqual(model.rows[0].status_key, "assembled-paid")

    def test_missing_authoritative_p_stays_none_for_unpaid_order(self):
        self.add_order("I-UNPAID", 19500, "5234")
        raw = history.read_history(self.db, 1)
        model = adapt_warehouse_history(
            raw,
            order_metadata_by_id={
                "I-UNPAID": {
                    "status": "Собран, не оплачен",
                    "client": "Клиент",
                    "oems": ["860200987"],
                }
            },
        )
        row = model.rows[0]
        self.assertIsNone(row.partner_amount_rub)
        self.assertFalse(row.partner_amount_authoritative)
        self.assertIsNone(row.payment_source)
        self.assertIsNone(row.balance_delta_rub)
        payload = to_fixture_dict(model)
        self.assertIsNone(payload["warehouse"]["rows"][0]["partnerAmountRub"])

    def test_balance_spend_maps_negative_p_and_strike(self):
        self.accrue("I-SOURCE", "5240", 19000, 10000, "pay-source")
        self.add_order("I-SPEND", 8500, "5241")
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
        raw = history.read_history(self.db, 1, limit=20)
        model = adapt_warehouse_history(
            raw,
            order_metadata_by_id={
                "I-SOURCE": {"status": "Отгружен", "client": "A", "oems": ["A"]},
                "I-SPEND": {"status": "Отгружен", "client": "B", "oems": ["B"]},
            },
        )
        rows = {row.extremizer_order_no: row for row in model.rows}
        spend = rows["I-SPEND"]
        self.assertEqual(spend.partner_amount_rub, 8500)
        self.assertEqual(spend.payment_source, "warehouse_balance_reservation")
        self.assertEqual(spend.balance_delta_rub, -8500)
        self.assertTrue(spend.customer_amount_struck)

    def test_status_is_order_core_metadata_not_finance_inference(self):
        self.add_order("I-NEW", 12000, "5250")
        raw = history.read_history(self.db, 1)
        with self.assertRaisesRegex(
            ReadModelContractError,
            "ORDER_METADATA_REQUIRED",
        ):
            adapt_warehouse_history(raw, order_metadata_by_id={})
        with self.assertRaisesRegex(
            ReadModelContractError,
            "UNSUPPORTED_ORDER_STATUS",
        ):
            adapt_warehouse_history(
                raw,
                order_metadata_by_id={
                    "I-NEW": {"status": "System Error", "client": "", "oems": []}
                },
            )
        self.assertEqual(
            set(BUSINESS_STATUS_TO_KEY),
            {"Новый", "Собран, не оплачен", "Собран, оплачен", "Отгружен"},
        )

    def test_system_error_is_separate_from_business_status(self):
        self.add_order("I-NEW", 12000, "5250")
        raw = history.read_history(self.db, 1)
        model = adapt_warehouse_history(
            raw,
            order_metadata_by_id={
                "I-NEW": {"status": "Новый", "client": "A", "oems": ["X"]}
            },
            system_errors=[
                {
                    "code": "WAREHOUSE_READ_MODEL_STALE",
                    "message": "Не удалось обновить данные",
                    "extremizer_order_no": "I-NEW",
                    "warehouse_order_no": "5250",
                }
            ],
        )
        self.assertEqual(model.rows[0].status, "Новый")
        self.assertEqual(model.system_errors[0].code, "WAREHOUSE_READ_MODEL_STALE")
        payload = to_fixture_dict(model)
        self.assertEqual(payload["warehouse"]["rows"][0]["statusKey"], "new")
        self.assertEqual(
            payload["warehouse"]["systemErrors"][0]["context"]["extremizerOrderNo"],
            "I-NEW",
        )

    def test_full_history_total_survives_server_search_pagination(self):
        for index, difference in enumerate((2925, 1500, 3000, 4500), start=1):
            partner = 10000
            self.accrue(
                f"I-{index}",
                f"52{index:02d}",
                partner + difference,
                partner,
                f"pay-{index}",
            )
        self.add_order("I-SPEND", 8500, "5299")
        wb.reserve_full_balance(
            self.db,
            reservation_id="spend",
            warehouse_id=1,
            order_id="I-SPEND",
            required_amount_rub=8500,
            actual_actor="admin:1",
        )
        wb.apply_reserved_balance(self.db, reservation_id="spend", actual_actor="admin:1")

        hidden = history.read_history(self.db, 1, search="no-such-order", limit=1)
        model = adapt_warehouse_history(hidden, order_metadata_by_id={})
        self.assertEqual(model.rows, ())
        self.assertEqual(model.summary.posted_rub, 3425)
        self.assertEqual(model.summary.full_history_balance_rub, 3425)


if __name__ == "__main__":
    unittest.main(verbosity=2)
