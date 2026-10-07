#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared pricing analytics for Probnik and the main pricing bot."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


TABLE = "pricing_analytics_events"


def _db_path(value: str | Path) -> str:
    return str(Path(value))


def init_analytics(db_file: str | Path) -> None:
    with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
        conn.execute(
            f"""CREATE TABLE IF NOT EXISTS {TABLE} (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                source_bot TEXT NOT NULL,
                event_type TEXT NOT NULL,
                telegram_user_id INTEGER,
                username TEXT,
                source_chat_id INTEGER,
                source_chat_type TEXT,
                oem TEXT,
                manufacturer TEXT,
                result_status TEXT,
                metadata_json TEXT
            )"""
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_created_at "
            f"ON {TABLE}(created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_source_event "
            f"ON {TABLE}(source_bot,event_type,created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_chat "
            f"ON {TABLE}(source_chat_id,created_at)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS idx_{TABLE}_oem "
            f"ON {TABLE}(oem,created_at)"
        )


def record_event(
    db_file: str | Path,
    *,
    source_bot: str,
    event_type: str,
    telegram_user_id: int | None = None,
    username: str | None = None,
    source_chat_id: int | None = None,
    source_chat_type: str | None = None,
    oem: str | None = None,
    manufacturer: str | None = None,
    result_status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = (
        json.dumps(metadata, ensure_ascii=False, separators=(",", ":"))
        if metadata
        else None
    )
    with sqlite3.connect(_db_path(db_file), timeout=10) as conn:
        conn.execute(
            f"""INSERT INTO {TABLE}(
                created_at,source_bot,event_type,
                telegram_user_id,username,
                source_chat_id,source_chat_type,
                oem,manufacturer,result_status,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                now,
                str(source_bot),
                str(event_type),
                int(telegram_user_id) if telegram_user_id is not None else None,
                (str(username).lstrip("@") if username else None),
                int(source_chat_id) if source_chat_id is not None else None,
                str(source_chat_type) if source_chat_type else None,
                str(oem) if oem else None,
                str(manufacturer) if manufacturer else None,
                str(result_status) if result_status else None,
                payload,
            ),
        )


def record_update_event(
    db_file: str | Path,
    *,
    source_bot: str,
    event_type: str,
    update: Any,
    source_chat_id: int | None = None,
    oem: str | None = None,
    manufacturer: str | None = None,
    result_status: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    record_event(
        db_file,
        source_bot=source_bot,
        event_type=event_type,
        telegram_user_id=getattr(user, "id", None),
        username=getattr(user, "username", None),
        source_chat_id=(
            source_chat_id
            if source_chat_id is not None
            else getattr(chat, "id", None)
        ),
        source_chat_type=getattr(chat, "type", None),
        oem=oem,
        manufacturer=manufacturer,
        result_status=result_status,
        metadata=metadata,
    )
