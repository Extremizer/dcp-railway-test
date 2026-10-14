"""WEB2 -> Procenka -> client order isolated E2E regression.

Uses the real handoff module, real atomic checkout adapter/orchestrator/finance
bridge, FinanceEngine, and the exact save_order_to_history/customer-price/
revalidation functions extracted from extremizer_bot.py source. External
Telegram and stock providers are isolated with deterministic stubs only.
"""
from __future__ import annotations

import ast
import asyncio
from contextlib import nullcontext
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_CEILING
from pathlib import Path
import sqlite3
import tempfile
import unittest

from client_checkout_callback_adapter import execute_prepared_checkout
from client_finance_adapter import ClientFinanceAdapter
from common_finance_contract import (
    FulfillmentRoute,
    PaymentRoute,
    default_client_payment_route,
)
from oemixibot_finance import FinanceEngine
import web_handoff

ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "extremizer_bot.py"
WEB_SOURCE = ROOT / "web_app.py"


def extract_functions(*names):
    source = BOT_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(BOT_SOURCE))
    wanted = []
    found = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names:
            wanted.append(node)
            found.add(node.name)
    missing = set(names) - found
    if missing:
        raise AssertionError(f"missing functions: {sorted(missing)}")
    module = ast.Module(body=wanted, type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, str(BOT_SOURCE), "exec")


def create_schema(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE orders(
                order_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                customer_name TEXT,
                username TEXT,
                total_usd REAL NOT NULL,
                usd_rub_rate REAL NOT NULL,
                customer_total_rub REAL,
                pricing_coefficient REAL,
                auto_pricing_status TEXT,
                origin TEXT,
                delivery_tariff TEXT,
                updated_at TEXT
            );
            CREATE TABLE order_items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                manufacturer TEXT,
                oem TEXT,
                requested_oem TEXT,
                name TEXT,
                quantity INTEGER NOT NULL,
                price_usd REAL,
                item_type TEXT,
                item_type_source TEXT,
                dealer_price_usd REAL,
                dealer_price_source TEXT,
                dealer_price_checked_at TEXT,
                dealer_price_status TEXT,
                customer_unit_rub INTEGER,
                reference_actual_weight_kg REAL,
                reference_volume_weight_kg REAL,
                reference_weight_state TEXT,
                reference_weight_source TEXT,
                offer_source TEXT NOT NULL DEFAULT 'usa',
                payment_route TEXT NOT NULL DEFAULT 'extremizer_balance',
                fulfillment_route TEXT NOT NULL DEFAULT 'through_extremizer',
                warehouse_id INTEGER,
                warehouse_public_name TEXT,
                price_snapshot_rub REAL,
                available_snapshot REAL,
                selected_delivery_tariff TEXT,
                delivery_selected_at TEXT
            );
            CREATE TABLE order_fulfillment_groups(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                group_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                warehouse_id INTEGER,
                warehouse_public_name TEXT,
                delivery_preference TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE oem_delivery_profiles(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer TEXT,
                oem TEXT,
                item_type TEXT
            );
            CREATE TABLE finance_ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_type TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                wallet_currency TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount TEXT NOT NULL,
                description TEXT NOT NULL,
                reference_type TEXT NOT NULL,
                reference_id TEXT NOT NULL,
                order_id TEXT,
                item_slice_id INTEGER,
                arrival_id INTEGER,
                actor TEXT NOT NULL,
                reason TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                payment_method TEXT,
                source_amount TEXT,
                source_currency TEXT,
                fx_code TEXT,
                fx_rate TEXT
            );
            """
        )


class User:
    id = 7005635854
    full_name = "WEB2 Client"
    username = "web2_client"


class Web2ProcenkaCheckoutE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "orders.db")

    def test_handoff_is_atomic_one_time_and_user_bound(self):
        token = web_handoff.create_handoff(
            [{
                "manufacturer": "Ski-Doo",
                "oem": "417300574",
                "requested_oem": "417300574",
                "offer_source": "usa",
                "warehouse_id": None,
                "qty": 1,
                # Browser-originated price-like fields are not authority.
                "price_snapshot_rub": 1,
            }],
            db_file=self.db,
        )
        payload = web_handoff.claim_handoff(token, User.id, self.db)
        self.assertEqual(payload["items"][0]["oem"], "417300574")
        self.assertIsNone(web_handoff.claim_handoff(token, User.id, self.db))
        self.assertIsNone(web_handoff.claim_handoff(token, User.id + 1, self.db))

        self.assertTrue(
            web_handoff.link_handoff_order(token, "E-WEB-1", User.id, self.db)
        )
        self.assertFalse(
            web_handoff.link_handoff_order(token, "E-WEB-2", User.id, self.db)
        )
        self.assertFalse(
            web_handoff.link_handoff_order(token, "E-WEB-1", User.id + 1, self.db)
        )

        expired = web_handoff.create_handoff(
            [{"manufacturer": "Ski-Doo", "oem": "X", "offer_source": "usa", "qty": 1}],
            db_file=self.db,
        )
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "UPDATE web_cart_handoffs SET expires_at=? WHERE token=?",
                ((datetime.now().astimezone() - timedelta(minutes=1)).isoformat(), expired),
            )
        self.assertIsNone(web_handoff.claim_handoff(expired, User.id, self.db))
        self.assertIsNone(web_handoff.claim_handoff("not-a-real-token", User.id, self.db))

    def _bot_namespace(self, *, dealer_price=100.01, warehouse_offer=None):
        code = extract_functions(
            "customer_rub_price_from_dp",
            "_revalidate_usa_cart_item",
            "_revalidate_warehouse_cart_item",
            "save_order_to_history",
        )

        current_dp = {"value": dealer_price}
        current_offer = {"value": warehouse_offer}

        def get_dealer_price_cache(manufacturer, oem, mark_used=False):
            value = current_dp["value"]
            if value is None:
                return None
            return {
                "dealer_price_usd": value,
                "source": "test-server-dp",
                "last_verified_at": "2026-10-14T08:00:00+02:00",
                "fresh": True,
            }

        def find_offer(oem, warehouse_id):
            return current_offer["value"]

        class Log:
            def info(self, *args, **kwargs):
                pass

        namespace = {
            "Decimal": Decimal,
            "ROUND_CEILING": ROUND_CEILING,
            "PRICE_COEFFICIENT": 1.34,
            "USD_RUB_RATE": 95.0,
            "get_dealer_price_cache": get_dealer_price_cache,
            "_find_client_warehouse_offer": find_offer,
            "warehouse_stock_service": type(
                "StockStub",
                (),
                {"refresh_warehouse_oem": staticmethod(lambda *a, **k: None)},
            ),
            "asyncio": asyncio,
            "log": Log(),
            "sqlite3": sqlite3,
            "datetime": datetime,
            "nullcontext": nullcontext,
            "PaymentRoute": PaymentRoute,
            "FulfillmentRoute": FulfillmentRoute,
            "default_client_payment_route": default_client_payment_route,
            "ORDERS_DB_FILE": Path(self.db),
            "ITEM_TYPE_ALLOWED_TARIFFS": {
                "part": ("comfort", "economy"),
                "accessory": ("comfort", "economy"),
                "gear": ("economy", "mix"),
            },
            "init_orders_db": lambda: None,
            "get_oem_reference": lambda current_oem, requested_oem: None,
            "infer_item_type_from_dcp_catalog": lambda catalog: None,
        }
        exec(code, namespace)
        return namespace, current_dp, current_offer

    def test_server_prices_round_up_and_revalidate_before_confirm(self):
        ns, current_dp, current_offer = self._bot_namespace(
            warehouse_offer={
                "is_fresh": True,
                "available_quantity": 5,
                "price_rub": 2500,
                "public_name": "Склад 1",
            }
        )
        price = ns["customer_rub_price_from_dp"](100.01)
        self.assertEqual(price, 12800)
        self.assertEqual(price % 100, 0)

        usa = {
            "manufacturer": "Ski-Doo",
            "oem": "417300574",
            "_customer_unit_rub_snapshot": price,
        }
        self.assertEqual(ns["_revalidate_usa_cart_item"](usa)["status"], "ok")
        current_dp["value"] = 101.0
        self.assertEqual(ns["_revalidate_usa_cart_item"](usa)["status"], "changed")
        current_dp["value"] = None
        self.assertEqual(ns["_revalidate_usa_cart_item"](usa)["status"], "unverified")

        warehouse = {
            "oem": "WH-1",
            "warehouse_id": 1,
            "qty": 2,
            "price_snapshot_rub": 2500,
        }
        self.assertEqual(
            asyncio.run(ns["_revalidate_warehouse_cart_item"](warehouse))["status"],
            "ok",
        )
        current_offer["value"] = {
            "is_fresh": True,
            "available_quantity": 1,
            "price_rub": 2500,
            "public_name": "Склад 1",
        }
        self.assertEqual(
            asyncio.run(ns["_revalidate_warehouse_cart_item"](warehouse))["status"],
            "changed",
        )
        current_offer["value"] = {
            "is_fresh": True,
            "available_quantity": 5,
            "price_rub": 2600,
            "public_name": "Склад 1",
        }
        self.assertEqual(
            asyncio.run(ns["_revalidate_warehouse_cart_item"](warehouse))["status"],
            "changed",
        )

    def test_handoff_api_contract_has_no_browser_price_authority(self):
        source = WEB_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(WEB_SOURCE))
        handoff_class = next(
            n for n in tree.body
            if isinstance(n, ast.ClassDef) and n.name == "HandoffItem"
        )
        fields = {
            n.target.id
            for n in handoff_class.body
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
        }
        self.assertNotIn("price", fields)
        self.assertNotIn("price_rub", fields)
        self.assertNotIn("price_snapshot_rub", fields)

        # API verifies the current server card/offer and stock before token creation.
        self.assertIn("card = build_oem_card(item.oem, item.manufacturer)", source)
        self.assertIn('selected_offer.get("price_rub")', source)
        self.assertIn('detail={"code": "offer_unavailable"}', source)
        self.assertIn('detail={"code": "insufficient_stock"}', source)

    def test_mixed_web_cart_confirm_persists_once_and_rolls_back_on_core_error(self):
        create_schema(self.db)
        ns, _current_dp, _current_offer = self._bot_namespace()
        save_order = ns["save_order_to_history"]
        price = ns["customer_rub_price_from_dp"](100.01)
        self.assertEqual(price, 12800)

        cart = {
            "usa": {
                "manufacturer": "Ski-Doo",
                "oem": "417300574",
                "requested_oem": "417300574",
                "qty": 1,
                "price": 200.0,
                "_dealer_price_usd": 100.01,
                "_dealer_price_source": "dp_cache:test",
                "_dealer_price_checked_at": "2026-10-14T08:00:00+02:00",
                "_dealer_price_status": "CACHE_FALLBACK",
                "_customer_unit_rub_snapshot": 12800,
                "offer_source": "usa",
                "_origin": "web",
                "_web_handoff_token": "test-token",
            },
            "warehouse": {
                "manufacturer": "Test",
                "oem": "WH-1",
                "requested_oem": "WH-1",
                "qty": 2,
                "price_snapshot_rub": 2500,
                "available_snapshot": 5,
                "offer_source": "warehouse",
                "warehouse_id": 1,
                "warehouse_public_name": "Склад 1",
                "_origin": "web",
                "_web_handoff_token": "test-token",
            },
        }

        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM finance_ledger").fetchone()[0], 0)

        adapter = ClientFinanceAdapter(FinanceEngine(self.db))
        hooks = []

        async def run_checkout(order_id, checkout_cart):
            return await execute_prepared_checkout(
                db_path=self.db,
                save_order_fn=save_order,
                finance_adapter=adapter,
                order_id=order_id,
                user=User(),
                cart=checkout_cart,
                delivery_preference="comfort",
                origin="web",
                actor="isolated_web2_procenka_e2e",
                manager_notify=lambda: hooks.append("manager"),
                pricing_analytics=lambda: hooks.append("analytics"),
                supplier_orders=lambda: hooks.append("supplier"),
                warehouse_reservation=lambda: (
                    hooks.append("reserve") or {"ok": True, "reservation_ids": [1]}
                ),
                web_handoff_link=lambda: hooks.append("handoff") or True,
                auto_quote_promotion=lambda: None,
                finalize_session=lambda: hooks.append("finalize"),
            )

        result = asyncio.run(run_checkout("E-WEB2-E2E", cart))
        self.assertEqual(result.order_id, "E-WEB2-E2E")
        self.assertEqual(result.client_id, str(User.id))
        self.assertEqual(
            hooks,
            ["manager", "analytics", "supplier", "reserve", "handoff", "finalize"],
        )

        with sqlite3.connect(self.db) as conn:
            order = conn.execute(
                "SELECT telegram_user_id,customer_total_rub,origin FROM orders WHERE order_id=?",
                ("E-WEB2-E2E",),
            ).fetchone()
            items = conn.execute(
                """SELECT offer_source,quantity,customer_unit_rub,warehouse_id,payment_route
                   FROM order_items WHERE order_id=? ORDER BY position""",
                ("E-WEB2-E2E",),
            ).fetchall()
            ledger = conn.execute(
                "SELECT event_type,amount,buyer_id FROM finance_ledger WHERE order_id=?",
                ("E-WEB2-E2E",),
            ).fetchall()
        self.assertEqual(order, (User.id, 17800.0, "web"))
        self.assertEqual(
            items,
            [
                ("usa", 1, 12800, None, "extremizer_balance"),
                ("warehouse", 2, 2500, 1, "direct_partner"),
            ],
        )
        self.assertEqual(ledger, [("ORDER_CHARGE", "-12800.00", str(User.id))])

        # Same durable order ID cannot create a second order or second charge.
        with self.assertRaises(sqlite3.IntegrityError):
            asyncio.run(run_checkout("E-WEB2-E2E", cart))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE order_id='E-WEB2-E2E'"
                ).fetchone()[0],
                1,
            )

        bad = {"bad": dict(cart["usa"], _dealer_price_usd=None)}
        with self.assertRaises(ValueError):
            asyncio.run(run_checkout("E-WEB2-ROLLBACK", bad))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE order_id='E-WEB2-ROLLBACK'"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE order_id='E-WEB2-ROLLBACK'"
                ).fetchone()[0],
                0,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
