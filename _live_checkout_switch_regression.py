#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Exact switch-diff regression for live checkout_confirm preparation."""

from __future__ import annotations

import hashlib
from pathlib import Path
import sqlite3
import tempfile

from client_finance_schema import ensure_client_finance_schema


ROOT = Path(__file__).resolve().parent
BOT = ROOT / "extremizer_bot.py"

LEGACY_CHECKOUT_BLOB_SHA = "0f2b705b3645fae6d0e0b9aa28ea458e5e475662"
SHADOW_CHECKOUT_BLOB_SHA = "fb4bbff2d302ca072dfc9fd149b7520fcf5dec07"

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


def run():
    source = BOT.read_text(encoding="utf-8")

    # 1. Live handler still routes through manufacturer_callback only.
    require(
        "CallbackQueryHandler(manufacturer_callback)" in source,
        "Telegram callback registration changed",
    )
    require(
        "CallbackQueryHandler(_shadow_checkout_confirm" not in source,
        "shadow function registered directly as handler",
    )

    # 2. Exactly one definition + one live switch call.
    require(
        source.count("_shadow_checkout_confirm(") == 2,
        "shadow reference count must be definition + one live switch",
    )

    # 3. Extract switched live block.
    live_start = source.index('    if data == "checkout_confirm":')
    live_end = source.index('\n    if data == "change_mfg":', live_start)
    live_block = source[live_start:live_end]
    require(
        live_block.startswith(SWITCH_PREFIX),
        "live checkout switch prefix changed",
    )

    # 4. Remove only the inserted switch and prove the complete old body is
    # byte-for-byte identical to the pre-switch checkpoint.
    legacy_reconstructed = (
        '    if data == "checkout_confirm":\n'
        + live_block[len(SWITCH_PREFIX):]
    )
    require(
        git_blob_sha(legacy_reconstructed) == LEGACY_CHECKOUT_BLOB_SHA,
        "legacy checkout body/UI/preflight text changed",
    )

    # 5. Prove the already-tested shadow implementation itself was not edited
    # while preparing the switch.
    shadow_start = source.index("async def _shadow_checkout_confirm(")
    shadow_end = source.index(
        "\nasync def manufacturer_callback",
        shadow_start,
    )
    shadow_block = source[shadow_start:shadow_end]
    require(
        git_blob_sha(shadow_block) == SHADOW_CHECKOUT_BLOB_SHA,
        "shadow checkout UI/preflight/success text changed",
    )

    # 6. Startup schema ensure must happen immediately after orders init,
    # before handlers are configured/registered.
    startup = (
        "    init_orders_db()\n"
        "    ensure_client_finance_schema(ORDERS_DB_FILE)\n"
        "    warehouse_admin.configure(\n"
    )
    require(startup in source, "client finance schema is not ensured at startup")

    # 7. Required switch dependencies must be explicit.
    require(
        "from client_finance_adapter import ClientFinanceAdapter\n" in source,
        "ClientFinanceAdapter import missing",
    )
    require(
        "from client_finance_schema import ensure_client_finance_schema\n"
        in source,
        "finance schema import missing",
    )
    require(
        "from oemixibot_finance import FinanceEngine\n" in source,
        "FinanceEngine import missing",
    )

    # 8. Schema migration works on a clean disposable DB and is idempotent.
    with tempfile.TemporaryDirectory(prefix="client-finance-schema-") as td:
        db = Path(td) / "orders.db"
        ensure_client_finance_schema(db)
        ensure_client_finance_schema(db)

        with sqlite3.connect(db) as conn:
            cols = {
                row[1]
                for row in conn.execute(
                    "PRAGMA table_info(finance_ledger)"
                ).fetchall()
            }
            indexes = {
                row[1]
                for row in conn.execute(
                    "PRAGMA index_list(finance_ledger)"
                ).fetchall()
            }

        required_cols = {
            "id",
            "buyer_type",
            "buyer_id",
            "wallet_currency",
            "event_type",
            "amount",
            "description",
            "reference_type",
            "reference_id",
            "order_id",
            "item_slice_id",
            "arrival_id",
            "actor",
            "reason",
            "idempotency_key",
            "created_at",
            "payment_method",
            "source_amount",
            "source_currency",
            "fx_code",
            "fx_rate",
        }
        require(required_cols <= cols, "finance_ledger schema incomplete")
        require(
            "idx_finance_ledger_wallet" in indexes,
            "wallet index missing",
        )
        require(
            "idx_finance_ledger_order" in indexes,
            "order index missing",
        )

    print("PASS exact live checkout switch-diff regression")
    print("legacy checkout body/UI/preflight: byte-identical")
    print("shadow checkout UI/preflight/success: byte-identical")
    print("live switch: one early return to proven shadow path")
    print("finance_ledger startup schema: disposable idempotent PASS")


if __name__ == "__main__":
    run()
