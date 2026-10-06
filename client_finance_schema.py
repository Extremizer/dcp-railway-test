#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Client finance schema migration for the shared orders SQLite database."""

from __future__ import annotations

import sqlite3


def ensure_client_finance_schema(db_path) -> None:
    """Create only the ledger table required by the proven FinanceEngine API."""

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS finance_ledger(
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
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_finance_ledger_wallet "
            "ON finance_ledger(buyer_type,buyer_id,wallet_currency,id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_finance_ledger_order "
            "ON finance_ledger(order_id,id)"
        )


__all__ = ["ensure_client_finance_schema"]
