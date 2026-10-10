#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Disposable orchestration regression for the isolated callback adapter."""

from __future__ import annotations

import ast
import asyncio
from contextlib import nullcontext
from datetime import datetime
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from client_checkout_callback_adapter import (
    CHECKOUT_PHASES,
    CheckoutPostCommitError,
    execute_prepared_checkout,
)
from client_finance_adapter import ClientFinanceAdapter
from common_finance_contract import PaymentRoute, default_client_payment_route
from oemixibot_finance import FinanceEngine


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "extremizer_bot.py"
ENGINE_SOURCE = ROOT / "oemixibot_finance.py"
PROVEN_ENGINE_SHA256 = "17546efcea563f0f3e1942947f085e31ed6d88a9ee6401e2c18147171f57d863"


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


def order_state(db_path, order_id):
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
            "ledger": conn.execute(
                "SELECT COUNT(*) FROM finance_ledger WHERE order_id=?",
                (order_id,),
            ).fetchone()[0],
        }


async def run_async():
    before_sha = hashlib.sha256(ENGINE_SOURCE.read_bytes()).hexdigest()
    require(before_sha == PROVEN_ENGINE_SHA256, "FinanceEngine SHA mismatch")

    require(
        CHECKOUT_PHASES["inside_transaction"]
        == (
            "save_order_to_history",
            "load_persisted_line_finance_snapshots",
            "post_client_order_charge",
            "commit",
        ),
        "inside-transaction phase changed",
    )
    require(
        CHECKOUT_PHASES["after_commit_critical"][0]
        == "manager_notification",
        "manager notification must be post-commit",
    )

    save_code = compile_save_order_to_history()

    with tempfile.TemporaryDirectory(prefix="callback-adapter-") as td:
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
            full_name = "Callback Adapter Client"
            username = "callback_adapter"

        def cart():
            return {
                "usa": {
                    "manufacturer": "Ski-Doo",
                    "oem": "417224332",
                    "qty": 1,
                    "price": 189.99,
                    "_dealer_price_usd": 100.0,
                    "offer_source": "usa",
                },
                "warehouse": {
                    "manufacturer": "Test",
                    "oem": "WH-1",
                    "qty": 2,
                    "price_snapshot_rub": 2500,
                    "offer_source": "warehouse",
                    "warehouse_id": 1,
                    "warehouse_public_name": "склад МСК",
                },
            }

        create_schema(db_path)
        adapter = ClientFinanceAdapter(FinanceEngine(str(db_path)))

        # Success: every side effect observes already committed order+finance.
        events = []

        def assert_committed(label):
            state = order_state(db_path, "E-CB-SUCCESS")
            require(
                state == {"orders": 1, "items": 2, "ledger": 1},
                f"{label} ran before atomic commit: {state}",
            )
            events.append(label)

        async def manager_notify():
            assert_committed("manager")

        def supplier_orders():
            assert_committed("supplier")

        def reserve():
            assert_committed("reserve")
            return {"ok": True, "reservation_ids": [1]}

        def web_link():
            assert_committed("web")
            return False

        def auto_quote():
            assert_committed("quote")
            return "QUOTE"

        def finalize():
            assert_committed("finalize")

        result = await execute_prepared_checkout(
            db_path=db_path,
            save_order_fn=save_order_to_history,
            finance_adapter=adapter,
            order_id="E-CB-SUCCESS",
            user=User(),
            cart=cart(),
            actor="regression",
            manager_notify=manager_notify,
            supplier_orders=supplier_orders,
            warehouse_reservation=reserve,
            web_handoff_link=web_link,
            auto_quote_promotion=auto_quote,
            finalize_session=finalize,
        )
        require(
            events == ["manager", "supplier", "reserve", "web", "quote", "finalize"],
            f"post-commit order wrong: {events}",
        )
        require(result.auto_quote == "QUOTE", "auto quote result missing")
        require(
            result.warnings == ("web_handoff_link_failed",),
            "best-effort web warning missing",
        )

        # Core failure: no post-commit hook may run.
        bad = cart()
        bad["usa"] = dict(bad["usa"])
        bad["usa"]["_dealer_price_usd"] = None
        called = []
        try:
            await execute_prepared_checkout(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-CB-CORE-FAIL",
                user=User(),
                cart=bad,
                actor="regression",
                manager_notify=lambda: called.append("manager"),
                supplier_orders=lambda: called.append("supplier"),
                warehouse_reservation=lambda: called.append("reserve"),
                finalize_session=lambda: called.append("finalize"),
            )
        except ValueError as exc:
            require(
                str(exc) == "missing customer_unit_rub snapshot",
                "wrong core failure",
            )
        else:
            raise AssertionError("core failure unexpectedly committed")
        require(called == [], f"post-commit hooks ran after core failure: {called}")
        require(
            order_state(db_path, "E-CB-CORE-FAIL")
            == {"orders": 0, "items": 0, "ledger": 0},
            "core failure left partial state",
        )

        # Manager notification failure happens after commit and stops later actions.
        called = []

        def manager_fail():
            called.append("manager")
            require(
                order_state(db_path, "E-CB-MANAGER-FAIL")
                == {"orders": 1, "items": 2, "ledger": 1},
                "manager failure happened before commit",
            )
            raise RuntimeError("telegram down")

        try:
            await execute_prepared_checkout(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-CB-MANAGER-FAIL",
                user=User(),
                cart=cart(),
                actor="regression",
                manager_notify=manager_fail,
                supplier_orders=lambda: called.append("supplier"),
                warehouse_reservation=lambda: called.append("reserve"),
                finalize_session=lambda: called.append("finalize"),
            )
        except CheckoutPostCommitError as exc:
            require(exc.core_committed is True, "post-commit error lost committed flag")
            require(exc.action == "manager_notification", "wrong failed action")
            require(exc.completed_actions == (), "wrong completed actions")
        else:
            raise AssertionError("manager failure was not surfaced")
        require(called == ["manager"], f"later hooks ran after manager failure: {called}")
        require(
            order_state(db_path, "E-CB-MANAGER-FAIL")
            == {"orders": 1, "items": 2, "ledger": 1},
            "post-commit manager failure rolled back durable core",
        )

        # Supplier failure retains core + manager success, but does not clear session.
        called = []

        async def manager_ok():
            called.append("manager")

        def supplier_fail():
            called.append("supplier")
            raise RuntimeError("supplier write failed")

        try:
            await execute_prepared_checkout(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-CB-SUPPLIER-FAIL",
                user=User(),
                cart=cart(),
                actor="regression",
                manager_notify=manager_ok,
                supplier_orders=supplier_fail,
                warehouse_reservation=lambda: called.append("reserve"),
                finalize_session=lambda: called.append("finalize"),
            )
        except CheckoutPostCommitError as exc:
            require(exc.action == "supplier_orders", "wrong supplier failure action")
            require(
                exc.completed_actions == ("manager_notification",),
                "manager completion not preserved",
            )
        else:
            raise AssertionError("supplier failure was not surfaced")
        require(
            called == ["manager", "supplier"],
            f"later hooks ran after supplier failure: {called}",
        )
        require(
            order_state(db_path, "E-CB-SUPPLIER-FAIL")
            == {"orders": 1, "items": 2, "ledger": 1},
            "supplier failure damaged committed core",
        )

        # Reservation result ok=False is a post-commit failure; finalize must not run.
        called = []
        try:
            await execute_prepared_checkout(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-CB-RESERVE-FAIL",
                user=User(),
                cart=cart(),
                actor="regression",
                manager_notify=lambda: called.append("manager"),
                supplier_orders=lambda: called.append("supplier"),
                warehouse_reservation=lambda: (
                    called.append("reserve")
                    or {"ok": False, "reason": "insufficient"}
                ),
                finalize_session=lambda: called.append("finalize"),
            )
        except CheckoutPostCommitError as exc:
            require(exc.action == "warehouse_reservation", "wrong reserve failure")
            require(
                exc.completed_actions
                == ("manager_notification", "supplier_orders"),
                "reservation failure completion state wrong",
            )
        else:
            raise AssertionError("reservation failure was not surfaced")
        require(
            called == ["manager", "supplier", "reserve"],
            f"finalize ran after reservation failure: {called}",
        )

        # Auto-quote failure occurs before session finalization.
        called = []

        def quote_fail():
            called.append("quote")
            raise RuntimeError("quote promotion failed")

        try:
            await execute_prepared_checkout(
                db_path=db_path,
                save_order_fn=save_order_to_history,
                finance_adapter=adapter,
                order_id="E-CB-QUOTE-FAIL",
                user=User(),
                cart=cart(),
                actor="regression",
                manager_notify=lambda: called.append("manager"),
                supplier_orders=lambda: called.append("supplier"),
                warehouse_reservation=lambda: (
                    called.append("reserve") or {"ok": True}
                ),
                web_handoff_link=lambda: True,
                auto_quote_promotion=quote_fail,
                finalize_session=lambda: called.append("finalize"),
            )
        except CheckoutPostCommitError as exc:
            require(exc.action == "auto_quote_promotion", "wrong quote failure")
        else:
            raise AssertionError("quote failure was not surfaced")
        require("finalize" not in called, "session finalized after quote failure")

    after_sha = hashlib.sha256(ENGINE_SOURCE.read_bytes()).hexdigest()
    require(after_sha == before_sha, "FinanceEngine changed during regression")
    print("PASS checkout callback adapter orchestration regression")
    print("post-commit order: manager -> supplier -> reserve -> web -> quote -> finalize")
    print("core failure: zero post-commit side effects")
    print("post-commit failures preserve committed order+finance and do not finalize")
    print("FinanceEngine SHA256:", after_sha)


def run():
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
