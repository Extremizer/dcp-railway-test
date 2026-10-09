#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Disposable regression for client checkout transaction orchestration."""

from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from client_checkout_orchestrator import persist_client_checkout_atomic
from client_finance_adapter import ClientFinanceAdapter
from common_finance_contract import PaymentRoute, default_client_payment_route
from oemixibot_finance import FinanceEngine


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "extremizer_bot.py"
ENGINE_SOURCE = ROOT / "oemixibot_finance.py"
PROVEN_ENGINE_SHA256 = "3380ce87a75201b79f93c469b8517c9705c58633294a3a15d6f1c70a9f9c3981"


def require(value, message):
    if not value:
        raise AssertionError(message)


def compile_save_order_to_history():
    source = BOT_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(BOT_SOURCE))
    target = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "save_order_to_history"
        ),
        None,
    )
    require(target is not None, "save_order_to_history not found")
    require(
        [arg.arg for arg in target.args.args][-1] == "conn",
        "shared conn argument missing",
    )
    module = ast.Module(body=[target], type_ignores=[])
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


def count_order_rows(db_path, order_id):
    with sqlite3.connect(db_path) as conn:
        return {
            "orders": conn.execute(
                "SELECT COUNT(*) FROM orders WHERE order_id=?",
                (order_id,),
            ).fetchone()[0],
            "items": conn.execute(
                "SELECT COUNT(*) FROM order_items WHERE order_id=?",
                (order_id,),
            ).fetchone()[0],
            "groups": conn.execute(
                "SELECT COUNT(*) FROM order_fulfillment_groups WHERE order_id=?",
                (order_id,),
            ).fetchone()[0],
            "ledger": conn.execute(
                "SELECT COUNT(*) FROM finance_ledger WHERE order_id=?",
                (order_id,),
            ).fetchone()[0],
        }


def run():
    before_sha = hashlib.sha256(ENGINE_SOURCE.read_bytes()).hexdigest()
    require(before_sha == PROVEN_ENGINE_SHA256, "FinanceEngine SHA mismatch")

    save_code = compile_save_order_to_history()

    with tempfile.TemporaryDirectory(prefix="checkout-orchestrator-") as td:
        db_path = Path(td) / "orders.db"

        def init_orders_db():
            create_schema(db_path)

        def customer_rub_price_from_dp(dp, coefficient, rate):
            if dp is None:
                return None
            return int(round(float(dp) * float(coefficient) * float(rate)))

        namespace = {
            "sqlite3": sqlite3,
            "datetime": datetime,
            "nullcontext": nullcontext,
            "PaymentRoute": PaymentRoute,
            "default_client_payment_route": default_client_payment_route,
            "ORDERS_DB_FILE": db_path,
            "USD_RUB_RATE": 100.0,
            "PRICE_COEFFICIENT": 1.0,
            "ITEM_TYPE_ALLOWED_TARIFFS": {
                "part": ("comfort", "economy"),
                "accessory": ("comfort", "economy"),
                "gear": ("economy", "mix"),
            },
            "init_orders_db": init_orders_db,
            "customer_rub_price_from_dp": customer_rub_price_from_dp,
            "get_oem_reference": lambda current_oem, requested_oem: None,
            "infer_item_type_from_dcp_catalog": lambda catalog: None,
        }
        exec(save_code, namespace)
        save_order_to_history = namespace["save_order_to_history"]

        class User:
            id = 7005635854
            full_name = "Checkout Regression Client"
            username = "checkout_regression"

        def mixed_cart():
            return {
                "usa": {
                    "manufacturer": "Ski-Doo",
                    "oem": "417224332",
                    "qty": 1,
                    "price": 189.99,
                    "_dealer_price_usd": 100.0,
                    "offer_source": "usa",
                },
                "warehouse_extremizer": {
                    "manufacturer": "Test",
                    "oem": "WH-E",
                    "qty": 2,
                    "price_snapshot_rub": 2500,
                    "offer_source": "warehouse",
                    "warehouse_id": 1,
                    "warehouse_public_name": "склад МСК",
                },
                "warehouse_direct": {
                    "manufacturer": "Test",
                    "oem": "WH-D",
                    "qty": 3,
                    "price_snapshot_rub": 2000,
                    "offer_source": "warehouse",
                    "warehouse_id": 2,
                    "warehouse_public_name": "склад ЯРС",
                    "payment_route": "direct_partner",
                },
            }

        create_schema(db_path)
        adapter = ClientFinanceAdapter(FinanceEngine(str(db_path)))

        # 1. Successful orchestration commits order and one finance event.
        result = persist_client_checkout_atomic(
            db_path=db_path,
            save_order_fn=save_order_to_history,
            finance_adapter=adapter,
            order_id="E-ORCH-COMMIT",
            user=User(),
            cart=mixed_cart(),
            actor="regression",
        )
        require(result["order_id"] == "E-ORCH-COMMIT", "wrong result order_id")
        require(result["client_id"] == str(User.id), "wrong result client_id")
        require(result["finance_event_id"] is not None, "finance event missing")

        with sqlite3.connect(db_path) as conn:
            order_total = conn.execute(
                "SELECT customer_total_rub FROM orders WHERE order_id=?",
                ("E-ORCH-COMMIT",),
            ).fetchone()[0]
            debit = conn.execute(
                "SELECT amount FROM finance_ledger WHERE order_id=?",
                ("E-ORCH-COMMIT",),
            ).fetchone()[0]
            routes = conn.execute(
                "SELECT payment_route FROM order_items WHERE order_id=? ORDER BY position",
                ("E-ORCH-COMMIT",),
            ).fetchall()
        require(order_total == 21000, "customer-visible total mismatch")
        require(debit == "-15000.00", "finance debit mismatch")
        require(
            routes
            == [
                ("extremizer_balance",),
                ("extremizer_balance",),
                ("direct_partner",),
            ],
            "payment route snapshots mismatch",
        )

        # 2. Bridge failure rolls back order persistence automatically.
        bad_cart = mixed_cart()
        bad_cart["usa"] = dict(bad_cart["usa"])
        bad_cart["usa"]["_dealer_price_usd"] = None
        try:
            persist_client_checkout_atomic(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-ORCH-BAD-PRICE",
                user=User(),
                cart=bad_cart,
                actor="regression",
            )
        except ValueError as exc:
            require(
                str(exc) == "missing customer_unit_rub snapshot",
                "wrong bridge failure",
            )
        else:
            raise AssertionError("bad price checkout committed")
        require(
            count_order_rows(db_path, "E-ORCH-BAD-PRICE")
            == {"orders": 0, "items": 0, "groups": 0, "ledger": 0},
            "bridge failure was not atomic",
        )

        # 3. save_order failure also leaves no partial finance/order state.
        invalid_route_cart = {
            "bad": dict(
                mixed_cart()["warehouse_direct"],
                payment_route="invalid-route",
            )
        }
        try:
            persist_client_checkout_atomic(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-ORCH-BAD-ROUTE",
                user=User(),
                cart=invalid_route_cart,
                actor="regression",
            )
        except ValueError:
            pass
        else:
            raise AssertionError("invalid route checkout committed")
        require(
            count_order_rows(db_path, "E-ORCH-BAD-ROUTE")
            == {"orders": 0, "items": 0, "groups": 0, "ledger": 0},
            "save_order failure left partial rows",
        )

        # 4. Direct-partner-only checkout persists order but creates no wallet event.
        direct_only = {
            "direct": {
                "manufacturer": "Test",
                "oem": "WH-DIRECT",
                "qty": 2,
                "price_snapshot_rub": 3000,
                "offer_source": "warehouse",
                "warehouse_id": 3,
                "warehouse_public_name": "склад КРС",
                "payment_route": "direct_partner",
            }
        }
        direct_result = persist_client_checkout_atomic(
            db_path=db_path,
            save_order_fn=save_order_to_history,
            finance_adapter=adapter,
            order_id="E-ORCH-DIRECT",
            user=User(),
            cart=direct_only,
            actor="regression",
        )
        require(
            direct_result["finance_event_id"] is None,
            "direct-partner-only order created wallet charge",
        )
        rows = count_order_rows(db_path, "E-ORCH-DIRECT")
        require(rows["orders"] == 1 and rows["items"] == 1, "direct order missing")
        require(rows["ledger"] == 0, "direct order debited wallet")

    after_sha = hashlib.sha256(ENGINE_SOURCE.read_bytes()).hexdigest()
    require(after_sha == before_sha, "FinanceEngine changed during regression")
    print("PASS client checkout orchestrator regression")
    print("customer_total_rub=21000; ORDER_CHARGE=-15000.00")
    print("direct_partner-only finance_event_id=None")
    print("FinanceEngine SHA256:", after_sha)


if __name__ == "__main__":
    run()
