#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atomic disposable regression: order persistence + client wallet charge."""

from __future__ import annotations

import ast
from contextlib import nullcontext
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from client_finance_adapter import ClientFinanceAdapter
from client_order_finance_bridge import (
    load_order_line_finance_snapshots,
    post_persisted_order_charge,
)
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


def run():
    before_sha = hashlib.sha256(ENGINE_SOURCE.read_bytes()).hexdigest()
    require(before_sha == PROVEN_ENGINE_SHA256, "FinanceEngine SHA mismatch")

    save_code = compile_save_order_to_history()

    with tempfile.TemporaryDirectory(prefix="atomic-client-finance-") as td:
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
            full_name = "Atomic Regression Client"
            username = "atomic_regression"

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
        engine = FinanceEngine(str(db_path))
        adapter = ClientFinanceAdapter(engine)
        client_id = str(User.id)

        # COMMIT: order + line snapshots + finance event persist together.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            save_order_to_history(
                "E-ATOMIC-COMMIT",
                User(),
                mixed_cart(),
                conn=conn,
            )
            lines = load_order_line_finance_snapshots(
                conn,
                "E-ATOMIC-COMMIT",
            )
            require(
                [line.amount_rub for line in lines] == [10000, 5000, 6000],
                "persisted line totals mismatch",
            )
            require(
                [line.payment_route.value for line in lines]
                == [
                    "extremizer_balance",
                    "extremizer_balance",
                    "direct_partner",
                ],
                "persisted payment routes mismatch",
            )
            event_id = post_persisted_order_charge(
                adapter,
                conn=conn,
                client_id=client_id,
                order_id="E-ATOMIC-COMMIT",
                actor="regression",
            )
            retry_id = post_persisted_order_charge(
                adapter,
                conn=conn,
                client_id=client_id,
                order_id="E-ATOMIC-COMMIT",
                actor="regression-retry",
            )
            require(retry_id == event_id, "atomic charge retry is not idempotent")
            order_total = conn.execute(
                "SELECT customer_total_rub FROM orders WHERE order_id=?",
                ("E-ATOMIC-COMMIT",),
            ).fetchone()[0]
            ledger = conn.execute(
                "SELECT amount FROM finance_ledger WHERE id=?",
                (event_id,),
            ).fetchone()[0]
            require(order_total == 21000, "customer-visible total mismatch")
            require(ledger == "-15000.00", "wallet debit mismatch")
            require(
                conn.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE order_id=?",
                    ("E-ATOMIC-COMMIT",),
                ).fetchone()[0]
                == 1,
                "retry created a second ORDER_CHARGE",
            )
            conn.commit()
        finally:
            conn.close()

        # ROLLBACK after finance charge removes every write in the checkout.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            save_order_to_history(
                "E-ATOMIC-ROLLBACK",
                User(),
                mixed_cart(),
                conn=conn,
            )
            post_persisted_order_charge(
                adapter,
                conn=conn,
                client_id=client_id,
                order_id="E-ATOMIC-ROLLBACK",
                actor="regression",
            )
            require(
                conn.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE order_id=?",
                    ("E-ATOMIC-ROLLBACK",),
                ).fetchone()[0]
                == 1,
                "charge missing before rollback",
            )
            conn.rollback()
        finally:
            conn.close()

        with sqlite3.connect(db_path) as check:
            for table in (
                "orders",
                "order_items",
                "order_fulfillment_groups",
                "finance_ledger",
            ):
                column = "order_id"
                require(
                    check.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column}=?",
                        ("E-ATOMIC-ROLLBACK",),
                    ).fetchone()[0]
                    == 0,
                    f"rollback left rows in {table}",
                )

        # Missing price snapshot stops finance and caller can roll back all writes.
        bad_cart = mixed_cart()
        bad_cart["usa"] = dict(bad_cart["usa"])
        bad_cart["usa"]["_dealer_price_usd"] = None
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            save_order_to_history(
                "E-ATOMIC-BAD-PRICE",
                User(),
                bad_cart,
                conn=conn,
            )
            try:
                load_order_line_finance_snapshots(
                    conn,
                    "E-ATOMIC-BAD-PRICE",
                )
            except ValueError as exc:
                require(
                    str(exc) == "missing customer_unit_rub snapshot",
                    "wrong missing-price error",
                )
            else:
                raise AssertionError("missing customer price snapshot accepted")
            conn.rollback()
        finally:
            conn.close()
        with sqlite3.connect(db_path) as check:
            require(
                check.execute(
                    "SELECT COUNT(*) FROM orders WHERE order_id=?",
                    ("E-ATOMIC-BAD-PRICE",),
                ).fetchone()[0]
                == 0,
                "failed finance validation left an order",
            )

        # Wrong client cannot be charged for another client's persisted order.
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            save_order_to_history(
                "E-ATOMIC-WRONG-OWNER",
                User(),
                mixed_cart(),
                conn=conn,
            )
            try:
                post_persisted_order_charge(
                    adapter,
                    conn=conn,
                    client_id="999999",
                    order_id="E-ATOMIC-WRONG-OWNER",
                    actor="regression",
                )
            except ValueError as exc:
                require(str(exc) == "order owner mismatch", "wrong owner error")
            else:
                raise AssertionError("wrong client was charged")
            require(
                conn.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE order_id=?",
                    ("E-ATOMIC-WRONG-OWNER",),
                ).fetchone()[0]
                == 0,
                "owner mismatch created finance event",
            )
            conn.rollback()
        finally:
            conn.close()

    after_sha = hashlib.sha256(ENGINE_SOURCE.read_bytes()).hexdigest()
    require(after_sha == before_sha, "FinanceEngine changed during regression")
    print("PASS atomic order + client finance regression")
    print("customer_total_rub=21000; ORDER_CHARGE=-15000.00")
    print("FinanceEngine SHA256:", after_sha)


if __name__ == "__main__":
    run()
