# -*- coding: utf-8 -*-
"""Shared SQLite bridge for cache-miss live DP verification.

Railway creates a short-lived request in the shared production DB.
A local trusted agent claims it, verifies DP through the already-authorized
DCP Chrome session, and posts only the verified result back to Railway.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(db_file: Path | str) -> None:
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS dp_live_requests (
                request_id TEXT PRIMARY KEY,
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                status TEXT NOT NULL,
                requested_at TEXT NOT NULL,
                claimed_at TEXT,
                finished_at TEXT,
                result_status TEXT,
                dealer_price_usd REAL,
                result_source TEXT,
                current_oem TEXT,
                item_name TEXT,
                error_code TEXT
            )"""
        )
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(dp_live_requests)")}
        if "result_manufacturer" not in cols:
            conn.execute("ALTER TABLE dp_live_requests ADD COLUMN result_manufacturer TEXT")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_dp_live_requests_status
               ON dp_live_requests(status, requested_at)"""
        )
        conn.commit()


def request_live_dp(
    db_file: Path | str,
    manufacturer: str,
    oem: str,
    *,
    wait_seconds: float = 10.0,
) -> dict:
    """Create/reuse one pending request and wait briefly for the local agent."""
    init(db_file)
    manufacturer = str(manufacturer or "").strip()
    oem = str(oem or "").strip()
    if not manufacturer or not oem:
        return {"status": "INVALID"}

    request_id = None
    now = _now()
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(seconds=30)
        ).isoformat(timespec="seconds")
        conn.execute(
            """UPDATE dp_live_requests
                  SET status='finished', finished_at=?, result_status='TIMEOUT',
                      error_code='stale_request'
                WHERE status IN ('pending','claimed') AND requested_at<?""",
            (now, cutoff),
        )
        conn.commit()
        row = conn.execute(
            """SELECT request_id
                 FROM dp_live_requests
                WHERE manufacturer=? AND oem=? AND status IN ('pending','claimed')
                ORDER BY requested_at DESC LIMIT 1""",
            (manufacturer, oem),
        ).fetchone()
        if row:
            request_id = str(row[0])
        else:
            request_id = uuid.uuid4().hex
            conn.execute(
                """INSERT INTO dp_live_requests(
                       request_id,manufacturer,oem,status,requested_at
                   ) VALUES(?,?,?,'pending',?)""",
                (request_id, manufacturer, oem, now),
            )
            conn.commit()

    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    while time.monotonic() < deadline:
        with sqlite3.connect(str(db_file), timeout=10) as conn:
            row = conn.execute(
                """SELECT status,result_status,dealer_price_usd,result_source,
                          current_oem,item_name,error_code,result_manufacturer
                     FROM dp_live_requests WHERE request_id=?""",
                (request_id,),
            ).fetchone()
        if row and str(row[0]) == "finished":
            return {
                "request_id": request_id,
                "status": str(row[1] or "TECHNICAL_ERROR"),
                "dealer_price_usd": row[2],
                "source": row[3],
                "current_oem": row[4],
                "name": row[5],
                "error_code": row[6],
                "manufacturer": row[7],
            }
        time.sleep(0.20)

    return {"request_id": request_id, "status": "TIMEOUT"}


def claim_next(db_file: Path | str) -> dict | None:
    init(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT request_id,manufacturer,oem
                 FROM dp_live_requests
                WHERE status='pending'
                ORDER BY requested_at ASC LIMIT 1"""
        ).fetchone()
        if not row:
            conn.commit()
            return None
        changed = conn.execute(
            """UPDATE dp_live_requests
                  SET status='claimed', claimed_at=?
                WHERE request_id=? AND status='pending'""",
            (_now(), str(row[0])),
        ).rowcount
        conn.commit()
        if changed != 1:
            return None
        return {
            "request_id": str(row[0]),
            "manufacturer": str(row[1]),
            "oem": str(row[2]),
        }


def finish(
    db_file: Path | str,
    request_id: str,
    *,
    result_status: str,
    dealer_price_usd: float | None = None,
    source: str | None = None,
    current_oem: str | None = None,
    name: str | None = None,
    error_code: str | None = None,
    manufacturer: str | None = None,
) -> bool:
    init(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        changed = conn.execute(
            """UPDATE dp_live_requests
                  SET status='finished',
                      finished_at=?,
                      result_status=?,
                      dealer_price_usd=?,
                      result_source=?,
                      current_oem=?,
                      item_name=?,
                      error_code=?,
                      result_manufacturer=?
                WHERE request_id=? AND status IN ('pending','claimed')""",
            (
                _now(),
                str(result_status or "TECHNICAL_ERROR").upper(),
                dealer_price_usd,
                source,
                current_oem,
                name,
                error_code,
                manufacturer,
                str(request_id),
            ),
        ).rowcount
        conn.commit()
        return changed == 1
