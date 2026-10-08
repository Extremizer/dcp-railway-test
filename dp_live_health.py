# -*- coding: utf-8 -*-
"""Shared DCP live-session health state.

The trusted local Windows agent is the source of truth for DCP session health.
It mirrors health to the shared Railway SQLite DB so every cloud consumer sees
one state. Local consumers may read the same state from a JSON file produced by
that agent.

This module contains no browser automation and never attempts to bypass
Cloudflare.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

VALID_STATUSES = {
    "READY",
    "CLOUDFLARE",
    "AUTH_REQUIRED",
    "BROWSER_DOWN",
    "TECHNICAL_ERROR",
    "UNKNOWN",
}

HUMAN_REQUIRED_STATUSES = {"CLOUDFLARE", "AUTH_REQUIRED"}
BLOCKING_STATUSES = {
    "CLOUDFLARE",
    "AUTH_REQUIRED",
    "BROWSER_DOWN",
    "TECHNICAL_ERROR",
    "UNKNOWN",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _normalize_status(value: object) -> str:
    status = str(value or "UNKNOWN").strip().upper()
    return status if status in VALID_STATUSES else "TECHNICAL_ERROR"


def _parse_datetime(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def init(db_file: Path | str) -> None:
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dp_live_health (
                   singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
                   status TEXT NOT NULL,
                   detail TEXT,
                   source TEXT,
                   checked_at TEXT NOT NULL,
                   changed_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dp_live_health_events (
                   id INTEGER PRIMARY KEY AUTOINCREMENT,
                   previous_status TEXT,
                   status TEXT NOT NULL,
                   detail TEXT,
                   source TEXT,
                   checked_at TEXT NOT NULL
               )"""
        )
        row = conn.execute(
            "SELECT singleton_id FROM dp_live_health WHERE singleton_id=1"
        ).fetchone()
        if not row:
            now = _now()
            conn.execute(
                """INSERT INTO dp_live_health(
                       singleton_id,status,detail,source,checked_at,changed_at
                   ) VALUES(1,'UNKNOWN','health_not_reported','system',?,?)""",
                (now, now),
            )
        conn.commit()


def set_health(
    db_file: Path | str,
    status: str,
    *,
    detail: str | None = None,
    source: str | None = "local_agent",
    checked_at: str | None = None,
) -> dict[str, Any]:
    """Persist one health observation and record status transitions."""
    init(db_file)
    normalized = _normalize_status(status)
    observed_at = _parse_datetime(checked_at) or datetime.now(timezone.utc)
    observed_text = observed_at.isoformat(timespec="seconds")

    with sqlite3.connect(str(db_file), timeout=10) as conn:
        row = conn.execute(
            """SELECT status,detail,source,checked_at,changed_at
                 FROM dp_live_health WHERE singleton_id=1"""
        ).fetchone()
        previous_status = _normalize_status(row[0] if row else "UNKNOWN")
        changed = previous_status != normalized
        changed_at = observed_text if changed else str(row[4] if row else observed_text)

        conn.execute(
            """UPDATE dp_live_health
                  SET status=?, detail=?, source=?, checked_at=?, changed_at=?
                WHERE singleton_id=1""",
            (
                normalized,
                str(detail or "").strip() or None,
                str(source or "").strip() or None,
                observed_text,
                changed_at,
            ),
        )
        if changed:
            conn.execute(
                """INSERT INTO dp_live_health_events(
                       previous_status,status,detail,source,checked_at
                   ) VALUES(?,?,?,?,?)""",
                (
                    previous_status,
                    normalized,
                    str(detail or "").strip() or None,
                    str(source or "").strip() or None,
                    observed_text,
                ),
            )
        conn.commit()

    return {
        "status": normalized,
        "previous_status": previous_status,
        "changed": changed,
        "detail": str(detail or "").strip() or None,
        "source": str(source or "").strip() or None,
        "checked_at": observed_text,
        "changed_at": changed_at,
    }


def get_health(
    db_file: Path | str,
    *,
    stale_after_seconds: float = 5400.0,
) -> dict[str, Any]:
    """Return persisted health plus an effective fail-safe status.

    A stale READY observation is treated as TECHNICAL_ERROR by the guard. This
    prevents all bots from creating new live work after the local agent stops
    reporting, while still exposing the raw status for diagnostics.
    """
    init(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        row = conn.execute(
            """SELECT status,detail,source,checked_at,changed_at
                 FROM dp_live_health WHERE singleton_id=1"""
        ).fetchone()

    if not row:
        return {
            "status": "UNKNOWN",
            "effective_status": "TECHNICAL_ERROR",
            "detail": "health_row_missing",
            "source": "system",
            "checked_at": None,
            "changed_at": None,
            "stale": True,
            "live_allowed": False,
        }

    status = _normalize_status(row[0])
    checked = _parse_datetime(row[3])
    stale = True
    if checked is not None:
        age = max(
            0.0,
            (datetime.now(timezone.utc) - checked).total_seconds(),
        )
        stale = stale_after_seconds > 0 and age > float(stale_after_seconds)

    effective = "TECHNICAL_ERROR" if stale else status
    detail = str(row[1] or "").strip() or None
    if stale:
        detail = "health_stale" if not detail else f"{detail};health_stale"

    return {
        "status": status,
        "effective_status": effective,
        "detail": detail,
        "source": str(row[2] or "").strip() or None,
        "checked_at": row[3],
        "changed_at": row[4],
        "stale": stale,
        "live_allowed": effective == "READY",
    }


def read_local_health(
    file_path: Path | str,
    *,
    stale_after_seconds: float = 5400.0,
) -> dict[str, Any]:
    """Read the local agent's JSON health mirror for local bot consumers."""
    path = Path(file_path)
    if not path.exists():
        return {
            "status": "UNKNOWN",
            "effective_status": "TECHNICAL_ERROR",
            "detail": "local_health_file_missing",
            "checked_at": None,
            "stale": True,
            "live_allowed": False,
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "status": "TECHNICAL_ERROR",
            "effective_status": "TECHNICAL_ERROR",
            "detail": "local_health_file_invalid",
            "checked_at": None,
            "stale": True,
            "live_allowed": False,
        }

    status = _normalize_status(
        payload.get("dcp_status")
        or payload.get("status")
        or "UNKNOWN"
    )
    checked_at = (
        payload.get("dcp_checked_at")
        or payload.get("checked_at")
        or payload.get("updated_at")
    )
    checked = _parse_datetime(checked_at)
    stale = True
    if checked is not None:
        age = max(
            0.0,
            (datetime.now(timezone.utc) - checked).total_seconds(),
        )
        stale = stale_after_seconds > 0 and age > float(stale_after_seconds)

    effective = "TECHNICAL_ERROR" if stale else status
    detail = str(
        payload.get("dcp_detail")
        or payload.get("detail")
        or ""
    ).strip() or None
    if stale:
        detail = "health_stale" if not detail else f"{detail};health_stale"

    return {
        "status": status,
        "effective_status": effective,
        "detail": detail,
        "checked_at": checked_at,
        "stale": stale,
        "live_allowed": effective == "READY",
    }
