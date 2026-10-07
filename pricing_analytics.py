#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared CRM-style demand analytics for Extremizer pricing bots.

One row = one real OEM request.
No technical clickstream is stored here.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REQUESTS_TABLE = "pricing_crm_requests"
LEGACY_EVENTS_TABLE = "pricing_analytics_events"  # deprecated; read-only legacy


def _db_path(value: str | Path) -> str:
    return str(Path(value))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _clean_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def init_analytics(db_file: str | Path) -> None:
    """Create the compact CRM table and indexes.

    Existing legacy event data is intentionally left untouched.
    """
    with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {REQUESTS_TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                source_bot TEXT NOT NULL,
                source_kind TEXT NOT NULL,
                source_chat_id INTEGER,
                source_chat_title TEXT,
                telegram_message_id INTEGER,

                telegram_user_id INTEGER,
                username TEXT,
                first_name TEXT,
                last_name TEXT,

                oem TEXT NOT NULL,
                manufacturer TEXT,

                result_status TEXT,
                price_status TEXT,
                display_price_amount REAL,
                display_price_currency TEXT,

                stock_rf_status TEXT,
                stock_rf_json TEXT,

                cart_added INTEGER NOT NULL DEFAULT 0,
                cart_added_at TEXT,
                order_id TEXT,
                ordered_at TEXT
            )"""
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUESTS_TABLE}_created "
            f"ON {REQUESTS_TABLE}(created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUESTS_TABLE}_user "
            f"ON {REQUESTS_TABLE}(telegram_user_id,created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUESTS_TABLE}_oem "
            f"ON {REQUESTS_TABLE}(oem,created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUESTS_TABLE}_source "
            f"ON {REQUESTS_TABLE}(source_bot,source_kind,created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUESTS_TABLE}_chat "
            f"ON {REQUESTS_TABLE}(source_chat_id,created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{REQUESTS_TABLE}_order "
            f"ON {REQUESTS_TABLE}(order_id)"
        )


def begin_request(
    db_file: str | Path,
    *,
    source_bot: str,
    source_kind: str,
    oem: str,
    manufacturer: str | None = None,
    telegram_user_id: int | None = None,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    source_chat_id: int | None = None,
    source_chat_title: str | None = None,
    telegram_message_id: int | None = None,
) -> int | None:
    """Insert one CRM row for one OEM request and return its id.

    Analytics failure is non-blocking: None is returned and bot flow continues.
    """
    now = _now()
    try:
        with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
            cursor = conn.execute(
                f"""INSERT INTO {REQUESTS_TABLE}(
                    created_at,updated_at,
                    source_bot,source_kind,
                    source_chat_id,source_chat_title,telegram_message_id,
                    telegram_user_id,username,first_name,last_name,
                    oem,manufacturer
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    now,
                    now,
                    str(source_bot),
                    str(source_kind),
                    int(source_chat_id) if source_chat_id is not None else None,
                    _clean_text(source_chat_title),
                    int(telegram_message_id) if telegram_message_id is not None else None,
                    int(telegram_user_id) if telegram_user_id is not None else None,
                    (str(username).lstrip("@") if username else None),
                    _clean_text(first_name),
                    _clean_text(last_name),
                    str(oem),
                    _clean_text(manufacturer),
                ),
            )
            return int(cursor.lastrowid)
    except sqlite3.Error:
        return None


def begin_request_from_update(
    db_file: str | Path,
    *,
    source_bot: str,
    source_kind: str,
    update: Any,
    oem: str,
    manufacturer: str | None = None,
    source_chat_id: int | None = None,
    source_chat_title: str | None = None,
) -> int | None:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    message = getattr(update, "effective_message", None)
    return begin_request(
        db_file,
        source_bot=source_bot,
        source_kind=source_kind,
        oem=oem,
        manufacturer=manufacturer,
        telegram_user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        first_name=getattr(user, "first_name", None),
        last_name=getattr(user, "last_name", None),
        source_chat_id=(
            source_chat_id
            if source_chat_id is not None
            else getattr(chat, "id", None)
        ),
        source_chat_title=(
            source_chat_title
            if source_chat_title is not None
            else getattr(chat, "title", None)
        ),
        telegram_message_id=getattr(message, "message_id", None),
    )


def complete_request(
    db_file: str | Path,
    request_id: int | None,
    *,
    manufacturer: str | None = None,
    result_status: str | None = None,
    price_status: str | None = None,
    display_price_amount: int | float | None = None,
    display_price_currency: str | None = None,
    stock_rf_status: str | None = None,
    stock_rf: list[dict[str, Any]] | None = None,
) -> bool:
    """Update the same OEM row with what the user actually received."""
    if request_id is None:
        return False

    stock_payload = (
        json.dumps(stock_rf, ensure_ascii=False, separators=(",", ":"))
        if stock_rf is not None
        else None
    )
    try:
        with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
            conn.execute(
                f"""UPDATE {REQUESTS_TABLE}
                       SET updated_at=?,
                           manufacturer=COALESCE(?,manufacturer),
                           result_status=?,
                           price_status=?,
                           display_price_amount=?,
                           display_price_currency=?,
                           stock_rf_status=?,
                           stock_rf_json=?
                     WHERE id=?""",
                (
                    _now(),
                    _clean_text(manufacturer),
                    _clean_text(result_status),
                    _clean_text(price_status),
                    (
                        float(display_price_amount)
                        if display_price_amount is not None
                        else None
                    ),
                    _clean_text(display_price_currency),
                    _clean_text(stock_rf_status),
                    stock_payload,
                    int(request_id),
                ),
            )
        return True
    except sqlite3.Error:
        return False


def mark_cart(
    db_file: str | Path,
    request_id: int | None,
) -> bool:
    if request_id is None:
        return False
    try:
        with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
            conn.execute(
                f"""UPDATE {REQUESTS_TABLE}
                       SET cart_added=1,
                           cart_added_at=COALESCE(cart_added_at,?),
                           updated_at=?
                     WHERE id=?""",
                (_now(), _now(), int(request_id)),
            )
        return True
    except sqlite3.Error:
        return False


def mark_order(
    db_file: str | Path,
    request_id: int | None,
    order_id: str,
) -> bool:
    if request_id is None or not str(order_id or "").strip():
        return False
    try:
        with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
            conn.execute(
                f"""UPDATE {REQUESTS_TABLE}
                       SET order_id=?,
                           ordered_at=COALESCE(ordered_at,?),
                           updated_at=?
                     WHERE id=?""",
                (str(order_id).strip(), _now(), _now(), int(request_id)),
            )
        return True
    except sqlite3.Error:
        return False


def mark_latest_matching_cart(
    db_file: str | Path,
    *,
    source_bot: str,
    telegram_user_id: int,
    oem: str,
) -> bool:
    """Mark the latest matching OEM request as added to cart."""
    try:
        with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
            row = conn.execute(
                f"""SELECT id
                      FROM {REQUESTS_TABLE}
                     WHERE source_bot=?
                       AND telegram_user_id=?
                       AND oem=?
                     ORDER BY id DESC
                     LIMIT 1""",
                (str(source_bot), int(telegram_user_id), str(oem)),
            ).fetchone()
        if not row:
            return False
        return mark_cart(db_file, int(row[0]))
    except sqlite3.Error:
        return False


def mark_latest_matching_order(
    db_file: str | Path,
    *,
    source_bot: str,
    telegram_user_id: int,
    oem: str,
    order_id: str,
) -> bool:
    """Fallback linker for flows that do not carry request_id into checkout."""
    try:
        with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
            row = conn.execute(
                f"""SELECT id
                      FROM {REQUESTS_TABLE}
                     WHERE source_bot=?
                       AND telegram_user_id=?
                       AND oem=?
                     ORDER BY id DESC
                     LIMIT 1""",
                (str(source_bot), int(telegram_user_id), str(oem)),
            ).fetchone()
        if not row:
            return False
        return mark_order(db_file, int(row[0]), order_id)
    except sqlite3.Error:
        return False
