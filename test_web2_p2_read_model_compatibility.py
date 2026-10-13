from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import (
    WarehouseActorContext,
    confirm_internal_direct_payment,
    create_payment_snapshot,
    init as init_internal,
)
from internal_order_read_v01 import (
    CONTRACT_VERSION,
    AdminReadContext,
    WarehouseReadContext,
    read_order,
    read_warehouse_history,
)
import warehouse_balance_v14 as wb
from web2_finance_read_model_adapter import (
    P3_CONTRACT_VERSION,
    ReadModelContractError,
    adapt_warehouse_history,
    to_fixture_dict,
)


def create_full_schema(db):
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


class Web2P3CompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def full_db(self):
        db = str(Path(self.tmp.name) / "p3-full.db")
        create_full_schema(db)
        init_internal(db)
        return db

    def add_snapshot_order(self, db, *, status="assembled_unpaid"):
        with sqlite3.connect(db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                ("I-P3-UI", status, "2026-10-14T00:00:00+02:00", 7001),
            )
            for pos, price in enumerate((10000, 9500), 1):
                c.execute(
                    """INSERT INTO order_items(
                         order_id,position,manufacturer,oem,requested_oem,name,
                         offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
                       VALUES(?,?,?,?,?,?,'warehouse',1,1,?,'direct_partner')""",
                    (
                        "I-P3-UI", pos, "BRP", f"OEM-{pos}", f"REQ-{pos}",
                        f"Part {pos}", price,
                    ),
                )
        create_payment_snapshot(
            db,
            order_id="I-P3-UI",
            client_id="client:1001",
            partner_amount_rub=16575,
            created_by="internal-terms:warehouse-1",
            warehouse_order_number="5234",
            expected_warehouse_id=1,
        )

    def sklad(self, warehouse=1, authorized=True):
        return WarehouseReadContext(
            actual_actor=f"warehouse:{warehouse}:user",
            warehouse_id=warehouse,
            authorized=authorized,
        )

    def test_real_p3_due_history_and_strict_order_map_together(self):
        db = self.full_db()
        self.add_snapshot_order(db)
        history = read_warehouse_history(
            db, warehouse_id=1, context=self.sklad(), search="I-P3-UI"
        )
        strict = read_order(
            db, warehouse_id=1, order_id="I-P3-UI", context=self.sklad()
        )
        self.assertEqual(history["contract_version"], CONTRACT_VERSION)
        self.assertEqual(strict["contract_version"], CONTRACT_VERSION)
        self.assertEqual(CONTRACT_VERSION, P3_CONTRACT_VERSION)

        model = adapt_warehouse_history(
            history,
            strict_orders_by_id={"I-P3-UI": strict},
        )
        row = model.rows[0]
        self.assertEqual(row.warehouse_id, 1)
        self.assertEqual(row.warehouse_order_no, "5234")
        self.assertEqual(row.customer_amount_rub, 19500)
        self.assertEqual(row.partner_amount_rub, 16575)
        self.assertEqual(row.payment_source, "internal_payment_snapshot")
        self.assertEqual(row.payment_status, "DUE")
        self.assertIsNone(row.balance_delta_rub)
        self.assertEqual(row.status, "Собран, не оплачен")
        self.assertEqual(row.status_key, "assembled-unpaid")
        self.assertEqual(row.client, "client:1001")
        self.assertEqual(row.oems, ("OEM-1", "OEM-2"))

    def test_real_p3_paid_status_and_ledger_effect_remain_separate_from_business_status(self):
        db = self.full_db()
        self.add_snapshot_order(db)
        confirm_internal_direct_payment(
            db,
            order_id="I-P3-UI",
            confirmation_id="confirm:I-P3-UI",
            actor_context=WarehouseActorContext(
                actual_actor="warehouse:1:user",
                effective_subject="warehouse:1",
                warehouse_id=1,
                authorized=True,
            ),
            payment_confirmed=True,
            confirmed_amount_rub=19500,
        )
        history = read_warehouse_history(db, warehouse_id=1, context=self.sklad())
        strict = read_order(
            db, warehouse_id=1, order_id="I-P3-UI",
            context=AdminReadContext(actual_actor="admin:1", authorized=True),
        )
        model = adapt_warehouse_history(
            history, strict_orders_by_id={"I-P3-UI": strict}
        )
        row = model.rows[0]
        self.assertEqual(row.payment_status, "PAID")
        self.assertEqual(row.status, "Собран, не оплачен")
        self.assertEqual(row.balance_delta_rub, 2925)
        self.assertEqual(model.summary.posted_rub, 2925)

    def test_real_preterms_history_accepts_nullable_p3_fields_without_inventing_values(self):
        db = str(Path(self.tmp.name) / "p3-preterms.db")
        wb.init(db)
        with sqlite3.connect(db) as c:
            c.executescript(
                """
                CREATE TABLE orders(
                  order_id TEXT PRIMARY KEY,
                  status TEXT
                );
                CREATE TABLE order_items(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  order_id TEXT NOT NULL,
                  offer_source TEXT NOT NULL,
                  warehouse_id INTEGER
                );
                INSERT INTO orders(order_id,status)
                VALUES('I-PRE','new');
                INSERT INTO order_items(order_id,offer_source,warehouse_id)
                VALUES('I-PRE','warehouse',1);
                """
            )
        history = read_warehouse_history(
            db, warehouse_id=1, context=self.sklad(), search="I-PRE"
        )
        raw = history["rows"][0]
        self.assertIsNone(raw["warehouse_order_number"])
        self.assertIsNone(raw["date"])
        self.assertIsNone(raw["customer_amount_rub"])
        self.assertIsNone(raw["partner_amount_rub"])
        self.assertIsNone(raw["payment_status"])

        model = adapt_warehouse_history(history)
        row = model.rows[0]
        self.assertIsNone(row.warehouse_order_no)
        self.assertIsNone(row.date)
        self.assertIsNone(row.customer_amount_rub)
        self.assertIsNone(row.partner_amount_rub)
        self.assertIsNone(row.payment_status)
        self.assertEqual(row.status, "Новый")
        self.assertIsNone(row.client)
        self.assertEqual(row.oems, ())
        payload = to_fixture_dict(model)["warehouse"]["rows"][0]
        self.assertIsNone(payload["warehouseOrderNo"])
        self.assertIsNone(payload["date"])
        self.assertIsNone(payload["customerAmountRub"])
        self.assertIsNone(payload["partnerAmountRub"])

    def test_snapshot_history_requires_matching_real_strict_order_payload(self):
        db = self.full_db()
        self.add_snapshot_order(db)
        history = read_warehouse_history(db, warehouse_id=1, context=self.sklad())
        with self.assertRaisesRegex(ReadModelContractError, "STRICT_ORDER_REQUIRED"):
            adapt_warehouse_history(history)

    def test_strict_order_scope_and_history_mismatch_fail_closed(self):
        db = self.full_db()
        self.add_snapshot_order(db)
        history = read_warehouse_history(db, warehouse_id=1, context=self.sklad())
        strict = read_order(
            db, warehouse_id=1, order_id="I-P3-UI", context=self.sklad()
        )
        tampered = {
            **strict,
            "scope": {**strict["scope"], "warehouse_id": 2},
        }
        with self.assertRaisesRegex(ReadModelContractError, "STRICT_ORDER_SCOPE_MISMATCH"):
            adapt_warehouse_history(
                history, strict_orders_by_id={"I-P3-UI": tampered}
            )

    def test_cross_warehouse_is_denied_by_real_p3_service_before_adapter(self):
        db = self.full_db()
        self.add_snapshot_order(db)
        with self.assertRaisesRegex(PermissionError, "WAREHOUSE_READ_SCOPE_MISMATCH"):
            read_warehouse_history(
                db, warehouse_id=1, context=self.sklad(warehouse=2)
            )

    def test_p3_mock_context_is_not_treated_as_production_authentication(self):
        # This only proves P3 disposable context behavior. It is intentionally
        # not a session/RBAC/authentication assertion.
        db = self.full_db()
        self.add_snapshot_order(db)
        with self.assertRaisesRegex(PermissionError, "WAREHOUSE_READ_NOT_AUTHORIZED"):
            read_warehouse_history(
                db, warehouse_id=1, context=self.sklad(authorized=False)
            )

    def test_wrong_contract_version_is_rejected(self):
        response = {
            "contract_version": "internal-order-read-v9",
            "scope": {"warehouse_id": 1, "viewer_type": "warehouse"},
            "summary": {
                "warehouse_id": 1,
                "posted_balance_rub": 0,
                "reserved_balance_rub": 0,
                "available_balance_rub": 0,
            },
            "rows": [],
        }
        with self.assertRaisesRegex(ReadModelContractError, "UNSUPPORTED_CONTRACT_VERSION"):
            adapt_warehouse_history(response)

    def test_full_history_summary_is_independent_from_search_and_pagination(self):
        db = self.full_db()
        self.add_snapshot_order(db)
        confirm_internal_direct_payment(
            db,
            order_id="I-P3-UI",
            confirmation_id="confirm:I-P3-UI",
            actor_context=WarehouseActorContext(
                actual_actor="warehouse:1:user",
                effective_subject="warehouse:1",
                warehouse_id=1,
                authorized=True,
            ),
            payment_confirmed=True,
            confirmed_amount_rub=19500,
        )
        hidden = read_warehouse_history(
            db, warehouse_id=1, context=self.sklad(),
            search="NO-MATCH", limit=1, offset=0,
        )
        model = adapt_warehouse_history(hidden)
        self.assertEqual(model.rows, ())
        self.assertEqual(model.summary.posted_rub, 2925)
        self.assertEqual(model.summary.full_history_balance_rub, 2925)


if __name__ == "__main__":
    unittest.main(verbosity=2)
