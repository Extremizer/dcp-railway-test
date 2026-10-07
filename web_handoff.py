#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""One-time WEB -> Telegram cart handoff for Extremizer Pro."""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

MAX_HANDOFF_ITEMS = 20
DEFAULT_TTL_MINUTES = 60


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def init_web_handoff_db(db_file: Path | str) -> None:
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS web_cart_handoffs (
                token TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                used_at TEXT,
                telegram_user_id INTEGER,
                order_id TEXT
            )
            """
        )
        columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(web_cart_handoffs)"
            ).fetchall()
        }
        for column, column_type in (
            ("telegram_user_id", "INTEGER"),
            ("order_id", "TEXT"),
        ):
            if column not in columns:
                conn.execute(
                    f"ALTER TABLE web_cart_handoffs ADD COLUMN {column} {column_type}"
                )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_cart_handoffs_expires
            ON web_cart_handoffs(expires_at)
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_web_cart_handoffs_order_id
            ON web_cart_handoffs(order_id)
            """
        )
        conn.commit()


def create_handoff(
    items: list[dict[str, Any]],
    db_file: Path | str,
    ttl_minutes: int = DEFAULT_TTL_MINUTES,
) -> str:
    if not items or len(items) > MAX_HANDOFF_ITEMS:
        raise ValueError("WEB cart must contain from 1 to 20 items.")
    if ttl_minutes <= 0:
        raise ValueError("WEB handoff TTL must be positive.")

    clean_items: list[dict[str, Any]] = []
    for item in items:
        manufacturer = str(item.get("manufacturer") or "").strip()
        oem = str(item.get("oem") or "").strip()
        requested_oem = str(item.get("requested_oem") or oem).strip()
        qty = int(item.get("qty") or 1)
        offer_source = str(item.get("offer_source") or "usa").strip().lower()
        if not oem or qty < 1 or qty > 99:
            raise ValueError("Invalid WEB cart item.")
        if offer_source == "usa" and not manufacturer:
            raise ValueError("USA WEB cart item requires manufacturer.")
        if offer_source not in {"usa", "warehouse"}:
            raise ValueError("Invalid WEB offer source.")
        warehouse_id = item.get("warehouse_id")
        if offer_source == "warehouse":
            if warehouse_id is None:
                raise ValueError("Warehouse offer requires warehouse_id.")
            warehouse_id = int(warehouse_id)
        else:
            warehouse_id = None

        clean_items.append({
            "manufacturer": manufacturer,
            "oem": oem,
            "requested_oem": requested_oem or oem,
            "offer_source": offer_source,
            "warehouse_id": warehouse_id,
            "warehouse_public_name": (
                str(item.get("warehouse_public_name") or "").strip() or None
            ),
            "price_snapshot_rub": item.get("price_snapshot_rub"),
            "available_snapshot": item.get("available_snapshot"),
            "qty": qty,
        })

    init_web_handoff_db(db_file)
    now = _now_dt()
    expires_at = (
        now + timedelta(minutes=int(ttl_minutes))
    ).isoformat(timespec="seconds")
    payload = json.dumps(
        {"version": 2, "items": clean_items},
        ensure_ascii=False,
        separators=(",", ":"),
    )

    with sqlite3.connect(db_file) as conn:
        for _ in range(10):
            token = secrets.token_urlsafe(16)
            try:
                conn.execute(
                    """
                    INSERT INTO web_cart_handoffs (
                        token, created_at, expires_at, payload_json, used_at
                    ) VALUES (?, ?, ?, ?, NULL)
                    """,
                    (token, now.isoformat(timespec="seconds"), expires_at, payload),
                )
                conn.commit()
                return token
            except sqlite3.IntegrityError:
                continue

    raise RuntimeError("Could not allocate WEB handoff token.")


def get_handoff(
    token: str,
    db_file: Path | str,
) -> dict[str, Any] | None:
    token = str(token or "").strip()
    if not token:
        return None
    init_web_handoff_db(db_file)
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            """
            SELECT payload_json, expires_at, used_at
            FROM web_cart_handoffs
            WHERE token = ?
            """,
            (token,),
        ).fetchone()
    if not row or row[2]:
        return None
    try:
        expires_at = datetime.fromisoformat(str(row[1]))
    except (TypeError, ValueError):
        return None
    if expires_at <= _now_dt():
        return None
    try:
        payload = json.loads(row[0])
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def mark_handoff_used(
    token: str,
    db_file: Path | str,
) -> bool:
    init_web_handoff_db(db_file)
    with sqlite3.connect(db_file) as conn:
        changed = conn.execute(
            """
            UPDATE web_cart_handoffs
            SET used_at = ?
            WHERE token = ? AND used_at IS NULL AND expires_at > ?
            """,
            (_now(), str(token or "").strip(), _now()),
        ).rowcount
        conn.commit()
    return changed == 1


def attach_handoff_user(
    token: str,
    telegram_user_id: int,
    db_file: Path | str,
) -> bool:
    init_web_handoff_db(db_file)
    with sqlite3.connect(db_file) as conn:
        changed = conn.execute(
            """
            UPDATE web_cart_handoffs
            SET telegram_user_id = ?
            WHERE token = ? AND used_at IS NOT NULL
            """,
            (int(telegram_user_id), str(token or "").strip()),
        ).rowcount
        conn.commit()
    return changed == 1


def link_handoff_order(
    token: str,
    order_id: str,
    telegram_user_id: int,
    db_file: Path | str,
) -> bool:
    init_web_handoff_db(db_file)
    with sqlite3.connect(db_file) as conn:
        changed = conn.execute(
            """
            UPDATE web_cart_handoffs
            SET order_id = ?, telegram_user_id = ?
            WHERE token = ? AND used_at IS NOT NULL
            """,
            (
                str(order_id or "").strip(),
                int(telegram_user_id),
                str(token or "").strip(),
            ),
        ).rowcount
        conn.commit()
    return changed == 1
