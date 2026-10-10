#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact production-port regression for the finance checkout switch."""

from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import client_checkout_callback_adapter as callback_adapter


ROOT = Path(__file__).resolve().parent
BOT = ROOT / "extremizer_bot.py"
PROBNIK = ROOT / "probnik_app.py"
WEB_APP = ROOT / "web_app.py"

PROD_LEGACY_CHECKOUT_BLOB = "0ebb62fdbf6cef25c915fa16da4687e80b1410d7"
PROD_PROBNIK_BLOB = "87e316e0af75b0c3447214044d57fc1debd67bf3"
# CURRENT 79a47e23 includes the reviewed PR #20 Apply/CSRF changes.
PROD_WEB_APP_BLOB = "b30b75569c3ed409924cf8939193e95034d7903a"

SWITCH_PREFIX = """    if data == "checkout_confirm":
        return await _shadow_checkout_confirm(
            update,
            context,
            finance_adapter=ClientFinanceAdapter(
                FinanceEngine(str(ORDERS_DB_FILE))
            ),
        )

"""


def require(value, message):
    if not value:
        raise AssertionError(message)


def git_blob_sha(text: str) -> str:
    data = text.encode("utf-8")
    payload = f"blob {len(data)}\0".encode("ascii") + data
    return hashlib.sha1(payload).hexdigest()


def file_git_blob(path: Path) -> str:
    return git_blob_sha(path.read_text(encoding="utf-8"))


async def adapter_order_regression():
    original = callback_adapter.persist_client_checkout_atomic
    events = []

    def fake_core(**kwargs):
        events.append("core")
        return {
            "order_id": kwargs["order_id"],
            "client_id": str(kwargs["user"].id),
            "finance_event_id": 77,
        }

    class User:
        id = 7005635854

    callback_adapter.persist_client_checkout_atomic = fake_core
    try:
        result = await callback_adapter.execute_prepared_checkout(
            db_path="unused.db",
            save_order_fn=lambda *a, **k: None,
            finance_adapter=object(),
            order_id="E-PROD-PORT",
            user=User(),
            cart={"x": {"qty": 1}},
            manager_notify=lambda: events.append("manager"),
            pricing_analytics=lambda: events.append("analytics"),
            supplier_orders=lambda: events.append("supplier"),
            warehouse_reservation=lambda: (
                events.append("reserve") or {"ok": True}
            ),
            web_handoff_link=lambda: (events.append("web") or True),
            auto_quote_promotion=lambda: (events.append("quote") or "QUOTE"),
            finalize_session=lambda: events.append("finalize"),
        )
    finally:
        callback_adapter.persist_client_checkout_atomic = original

    require(
        events
        == [
            "core",
            "manager",
            "analytics",
            "supplier",
            "reserve",
            "web",
            "quote",
            "finalize",
        ],
        f"wrong post-commit order: {events}",
    )
    require(result.finance_event_id == 77, "finance event lost")


def run():
    source = BOT.read_text(encoding="utf-8")

    # Production files unrelated to finance switch must stay exactly at the
    # post-hotfix production checkpoint.
    require(file_git_blob(PROBNIK) == PROD_PROBNIK_BLOB, "probnik_app.py changed")
    require(file_git_blob(WEB_APP) == PROD_WEB_APP_BLOB, "web_app.py changed")

    # Handler registration is unchanged; the live branch alone delegates.
    require(
        "CallbackQueryHandler(manufacturer_callback)" in source,
        "callback handler registration changed",
    )
    require(
        "CallbackQueryHandler(_shadow_checkout_confirm" not in source,
        "shadow callback registered directly",
    )

    live_start = source.index('    if data == "checkout_confirm":')
    live_end = source.index('\n    if data == "change_mfg":', live_start)
    live = source[live_start:live_end]
    require(live.startswith(SWITCH_PREFIX), "minimal early switch missing")

    reconstructed = (
        '    if data == "checkout_confirm":\n'
        + live[len(SWITCH_PREFIX):]
    )
    require(
        git_blob_sha(reconstructed) == PROD_LEGACY_CHECKOUT_BLOB,
        "production legacy checkout body changed",
    )

    # Current production analytics must survive the port and run through the
    # post-commit adapter before supplier-side work.
    shadow_start = source.index("async def _shadow_checkout_confirm(")
    shadow_end = source.index(
        "\nasync def manufacturer_callback",
        shadow_start,
    )
    shadow = source[shadow_start:shadow_end]
    require(
        shadow.count("pricing_analytics.mark_latest_matching_order(") == 1,
        "production pricing analytics hook missing/duplicated",
    )
    require(
        "pricing_analytics=pricing_analytics_hook" in shadow,
        "analytics hook is not passed to adapter",
    )

    # Startup migration order is deliberate: order schema first, finance
    # ledger second, existing pricing analytics initialization preserved.
    startup = (
        "    init_orders_db()\n"
        "    ensure_client_finance_schema(ORDERS_DB_FILE)\n"
        "    pricing_analytics.init_analytics(ORDERS_DB_FILE)\n"
    )
    require(startup in source, "startup migration/analytics order changed")

    # Shared transaction + line-level route must be present.
    save_start = source.index("def save_order_to_history(")
    save_end = source.index("\n\nMANUFACTURERS = [", save_start)
    save = source[save_start:save_end]
    require("conn: sqlite3.Connection | None = None" in save, "shared conn missing")
    require("PaymentRoute(requested_payment_route).value" in save, "route validation missing")
    require("payment_route," in save, "payment route snapshot missing")

    init_start = source.index("def init_orders_db() -> None:")
    init_end = source.index("\ndef ", init_start + 20)
    init = source[init_start:init_end]
    require(
        '("payment_route", "TEXT NOT NULL DEFAULT \'extremizer_balance\'")'
        in init,
        "payment_route migration missing",
    )

    require(
        callback_adapter.CHECKOUT_PHASES["after_commit_critical"][:3]
        == (
            "manager_notification",
            "pricing_analytics",
            "supplier_orders",
        ),
        "production post-commit critical ordering changed",
    )

    asyncio.run(adapter_order_regression())

    print("PASS production finance switch port regression")
    print("legacy production checkout body: byte-identical")
    print("probnik_app.py hotfix: byte-identical")
    print("web_app.py: byte-identical")
    print("pricing analytics preserved after core commit")
    print("shared transaction + payment_route migration present")


if __name__ == "__main__":
    run()
