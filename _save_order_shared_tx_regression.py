#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Disposable regression for save_order_to_history shared transaction prep.

This test never imports extremizer_bot.py. It parses the source with AST,
extracts only save_order_to_history(), and executes that exact function in a
minimal isolated namespace backed by a temporary SQLite database.
"""

from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
import sqlite3
import tempfile

from common_finance_contract import PaymentRoute, default_client_payment_route


ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / "extremizer_bot.py"


def require(value, message):
    if not value:
        raise AssertionError(message)


def compile_target_function():
    source = SOURCE.read_text(encoding="utf-8")
    require(
        '("payment_route", "TEXT NOT NULL DEFAULT \'extremizer_balance\'")'
        in source,
        "payment_route migration missing from init_orders_db",
    )
    tree = ast.parse(source, filename=str(SOURCE))
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
    arg_names = [arg.arg for arg in target.args.args]
    require(arg_names[-1] == "conn", "shared conn argument missing")
    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, str(SOURCE), "exec")


def create_schema(db_path):
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS orders(
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
            CREATE TABLE IF NOT EXISTS order_items(
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
            CREATE TABLE IF NOT EXISTS order_fulfillment_groups(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                group_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                warehouse_id INTEGER,
                warehouse_public_name TEXT,
                delivery_preference TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS oem_delivery_profiles(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                item_type TEXT NOT NULL
            );
            """
        )


def run():
    code = compile_target_function()

    with tempfile.TemporaryDirectory(prefix="save-order-shared-tx-") as td:
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
            "PRICE_COEFFICIENT": 1.34,
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
        exec(code, namespace)
        save_order_to_history = namespace["save_order_to_history"]

        class User:
            id = 7005635854
            full_name = "Regression Client"
            username = "regression"

        cart = {
            "usa": {
                "manufacturer": "Ski-Doo",
                "oem": "417224332",
                "qty": 1,
                "price": 189.99,
                "_dealer_price_usd": 134.50,
                "offer_source": "usa",
            },
            "warehouse": {
                "manufacturer": "Test",
                "oem": "WH-1",
                "qty": 2,
                "price_snapshot_rub": 5000,
                "offer_source": "warehouse",
                "warehouse_id": 1,
                "warehouse_public_name": "склад МСК",
                "payment_route": "direct_partner",
            },
        }

        # Legacy call owns its transaction and still commits.
        save_order_to_history("E-TX-1", User(), cart)
        with sqlite3.connect(db_path) as conn:
            routes = conn.execute(
                "SELECT offer_source,payment_route FROM order_items "
                "WHERE order_id=? ORDER BY position",
                ("E-TX-1",),
            ).fetchall()
            require(
                routes
                == [
                    ("usa", "extremizer_balance"),
                    ("warehouse", "direct_partner"),
                ],
                "line payment_route snapshot mismatch",
            )

        # Caller-owned transaction can roll back order + items + groups.
        init_orders_db()
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            save_order_to_history("E-TX-2", User(), cart, conn=conn)
            require(
                conn.execute(
                    "SELECT COUNT(*) FROM orders WHERE order_id='E-TX-2'"
                ).fetchone()[0]
                == 1,
                "shared transaction did not write order",
            )
            conn.rollback()
        finally:
            conn.close()
        with sqlite3.connect(db_path) as check:
            require(
                check.execute(
                    "SELECT COUNT(*) FROM orders WHERE order_id='E-TX-2'"
                ).fetchone()[0]
                == 0,
                "caller rollback did not remove order",
            )
            require(
                check.execute(
                    "SELECT COUNT(*) FROM order_items WHERE order_id='E-TX-2'"
                ).fetchone()[0]
                == 0,
                "caller rollback did not remove items",
            )
            require(
                check.execute(
                    "SELECT COUNT(*) FROM order_fulfillment_groups "
                    "WHERE order_id='E-TX-2'"
                ).fetchone()[0]
                == 0,
                "caller rollback did not remove fulfillment groups",
            )

        # Caller commit persists the same line snapshots.
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            save_order_to_history("E-TX-3", User(), cart, conn=conn)
            conn.commit()
        finally:
            conn.close()
        with sqlite3.connect(db_path) as check:
            routes = check.execute(
                "SELECT payment_route FROM order_items "
                "WHERE order_id='E-TX-3' ORDER BY position"
            ).fetchall()
            require(
                routes == [("extremizer_balance",), ("direct_partner",)],
                "caller commit route snapshot mismatch",
            )

        # Invalid future route is rejected before any order write.
        invalid_cart = {
            "bad": dict(cart["warehouse"], payment_route="invalid-route")
        }
        try:
            save_order_to_history("E-TX-BAD", User(), invalid_cart)
        except ValueError:
            pass
        else:
            raise AssertionError("invalid payment_route accepted")
        with sqlite3.connect(db_path) as check:
            require(
                check.execute(
                    "SELECT COUNT(*) FROM orders WHERE order_id='E-TX-BAD'"
                ).fetchone()[0]
                == 0,
                "invalid route created an order",
            )

    print("PASS save_order_to_history shared transaction regression")


if __name__ == "__main__":
    run()
