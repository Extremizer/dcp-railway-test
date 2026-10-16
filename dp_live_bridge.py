# -*- coding: utf-8 -*-
"""Shared SQLite bridge for cache-miss live DP verification.

Railway creates a short-lived request in the shared production DB.
A local trusted agent claims it, verifies DP through the already-authorized
DCP Chrome session, and posts only the verified result back to Railway.
"""

from __future__ import annotations

import os
import math
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

import dp_live_health
import dp_live_admission


def _request_key(manufacturer: str, oem: str) -> tuple[str, str]:
    aliases = {
        "arcticcat": "Arctic Cat", "skidoo": "Ski-Doo", "canam": "Can-Am",
        "seadoo": "Sea-Doo", "cfmoto": "CFMoto", "honda": "Honda",
        "yamaha": "Yamaha", "kawasaki": "Kawasaki", "suzuki": "Suzuki",
        "polaris": "Polaris", "ktm": "KTM", "indian": "Indian",
    }
    raw = str(manufacturer or "").strip()
    canonical = aliases.get(re.sub(r"[\s_-]", "", raw).lower(), "")
    if raw.upper() in {"__AUTO__", "AUTO", "UNKNOWN"}:
        return "", ""
    normalized_oem = re.sub(r"\s+", "", str(oem or "")).upper()
    if not canonical or not re.fullmatch(r"[A-Z0-9-]{4,40}", normalized_oem):
        return "", ""
    return canonical, normalized_oem


def _valid_dp(price: object, source: object) -> bool:
    return (
        isinstance(price, (int, float)) and not isinstance(price, bool)
        and math.isfinite(price) and price > 0
        and str(source or "").startswith("verified_local_dcp:")
        and not any(x in str(source).lower() for x in ("msrp", "retail", "rrp"))
    )


def _result(row: tuple) -> dict:
    result = dict(zip(
        ("request_id", "status", "dealer_price_usd", "source", "current_oem",
         "name", "error_code", "manufacturer", "cache_committed_at"), row
    ))
    result["server_cache_committed"] = bool(result.get("cache_committed_at"))
    return result


def _expire_pending(conn: sqlite3.Connection, now: str) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat(timespec="seconds")
    # Never free an in-flight slot while a browser operation may still run.
    # An orphaned claim requires explicit recovery, not a second live lookup.
    conn.execute(
        """UPDATE dp_live_requests SET status='finished',finished_at=?,
                  result_status='TIMEOUT',error_code='stale_request'
           WHERE status='pending' AND requested_at<?""", (now, cutoff)
    )


def _quarantine_claims(conn: sqlite3.Connection, now: str) -> bool:
    """Lease expiry proves uncertainty, never that the browser operation stopped."""
    rows = conn.execute(
        """SELECT request_id,claim_generation,claim_token,lease_expires_at
           FROM dp_live_requests WHERE status='claimed'""",
    ).fetchall()
    observed = dp_live_health._parse_datetime(now)
    for request_id, generation, token, expires_at in rows:
        deadline = dp_live_health._parse_datetime(expires_at)
        if token and deadline and deadline > observed:
            continue
        conn.execute(
            "UPDATE dp_live_requests SET status='orphaned',orphaned_at=? WHERE request_id=? AND status='claimed'",
            (now, request_id),
        )
        conn.execute(
            """INSERT INTO dp_live_recovery_events(request_id,claim_generation,event_type,actor,created_at)
               VALUES(?,?,'quarantined','server',?)""", (request_id, generation, now),
        )
    return bool(conn.execute("SELECT 1 FROM dp_live_requests WHERE status='orphaned' LIMIT 1").fetchone())


def _breaker_status(conn: sqlite3.Connection) -> str | None:
    blocked, blocked_at = conn.execute(
        "SELECT blocked_status,blocked_at FROM dp_live_coordination WHERE singleton_id=1"
    ).fetchone()
    if not blocked:
        return None
    health = conn.execute(
        "SELECT status,checked_at FROM dp_live_health WHERE singleton_id=1"
    ).fetchone()
    observed = dp_live_health._parse_datetime(health[1]) if health else None
    blocked_time = dp_live_health._parse_datetime(blocked_at)
    if health and health[0] == "READY" and observed and blocked_time and observed > blocked_time:
        conn.execute("UPDATE dp_live_coordination SET blocked_status=NULL,blocked_at=NULL WHERE singleton_id=1")
        return None
    return str(blocked)


def _blocked_health(conn: sqlite3.Connection) -> str | None:
    if not _health_enforced():
        return None
    status, checked_at = conn.execute(
        "SELECT status,checked_at FROM dp_live_health WHERE singleton_id=1"
    ).fetchone()
    checked = dp_live_health._parse_datetime(checked_at)
    if not checked or (datetime.now(timezone.utc)-checked).total_seconds() > 5400:
        return "TECHNICAL_ERROR"
    return None if status == "READY" else str(status)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _health_enforced() -> bool:
    return str(
        os.getenv("EXTREMIZER_DP_HEALTH_ENFORCE", "0") or "0"
    ).strip().lower() in {"1", "true", "yes", "on"}


def init(db_file: Path | str) -> None:
    dp_live_health.init(db_file)
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
        for column, definition in {
            "claim_token": "TEXT", "claim_owner": "TEXT",
            "claim_generation": "INTEGER NOT NULL DEFAULT 0", "lease_expires_at": "TEXT",
            "orphaned_at": "TEXT", "cache_committed_at": "TEXT",
        }.items():
            if column not in cols:
                conn.execute(f"ALTER TABLE dp_live_requests ADD COLUMN {column} {definition}")
        conn.execute("""CREATE TABLE IF NOT EXISTS dp_live_recovery_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL,
            claim_generation INTEGER NOT NULL, event_type TEXT NOT NULL,
            actor TEXT NOT NULL, evidence_sha256 TEXT, plan_sha256 TEXT,
            created_at TEXT NOT NULL)""")
        conn.execute(
            """CREATE INDEX IF NOT EXISTS idx_dp_live_requests_status
               ON dp_live_requests(status, requested_at)"""
        )
        conn.execute("""CREATE TABLE IF NOT EXISTS dp_live_coordination (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
            blocked_status TEXT, blocked_at TEXT)""")
        conn.execute("INSERT OR IGNORE INTO dp_live_coordination(singleton_id) VALUES(1)")
        dp_live_admission.initialize(conn)
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
    manufacturer, oem = _request_key(manufacturer, oem)
    if not manufacturer or not oem:
        return {"status": "INVALID"}

    if _health_enforced():
        health = dp_live_health.get_health(db_file)
        if not health.get("live_allowed"):
            status = str(
                health.get("effective_status")
                or health.get("status")
                or "TECHNICAL_ERROR"
            ).upper()
            return {
                "status": status,
                "error_code": "dcp_health_" + status.lower(),
                "health": health,
            }

    request_id = None
    now = _now()
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        # Lookup and insert must share the write lock across all bot processes.
        conn.execute("BEGIN IMMEDIATE")
        if not dp_live_admission.is_open(conn):
            conn.commit()
            return {"status": "ADMISSION_CLOSED", "error_code": "dp_live_admission_closed"}
        _expire_pending(conn, now)
        if _quarantine_claims(conn, now):
            conn.commit()
            return {"status": "TECHNICAL_ERROR", "error_code": "claim_recovery_required"}
        blocked = _breaker_status(conn) or _blocked_health(conn)
        if blocked:
            conn.commit()
            return {"status": blocked, "error_code": "dcp_circuit_open"}
        recent = conn.execute(
            """SELECT request_id,result_status,dealer_price_usd,result_source,
                      current_oem,item_name,error_code,result_manufacturer,cache_committed_at
               FROM dp_live_requests WHERE manufacturer=? AND oem=?
                 AND status='finished' AND result_status='FOUND' AND finished_at>=?
               ORDER BY finished_at DESC LIMIT 1""",
            (manufacturer, oem, (datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(timespec="seconds")),
        ).fetchone()
        if recent and _valid_dp(recent[2], recent[3]):
            conn.commit()
            return _result(recent)
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
                          current_oem,item_name,error_code,result_manufacturer,cache_committed_at
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
                "cache_committed_at": row[8],
                "server_cache_committed": bool(row[8]),
            }
        time.sleep(0.20)

    return {"request_id": request_id, "status": "TIMEOUT"}


def claim_next(db_file: Path | str, *, producer_id: str = "isolated-test") -> dict | None:
    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}", producer_id):
        raise ValueError("invalid_producer_id")
    init(db_file)
    if _health_enforced():
        health = dp_live_health.get_health(db_file)
        if not health.get("live_allowed"):
            return None
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _quarantine_claims(conn, _now()):
            conn.commit()
            return None
        if not dp_live_admission.is_open(conn):
            conn.commit()
            return None
        _expire_pending(conn, _now())
        if _breaker_status(conn) or _blocked_health(conn) or conn.execute(
            "SELECT 1 FROM dp_live_requests WHERE status='claimed' LIMIT 1"
        ).fetchone():
            conn.commit()
            return None
        # Also serialize different OEMs; all agents share this admission limit.
        try:
            gap = float(os.getenv("EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS", "5"))
        except ValueError:
            gap = 5.0
        if not math.isfinite(gap) or gap < 0:
            gap = 5.0
        last = conn.execute("SELECT MAX(claimed_at) FROM dp_live_requests").fetchone()[0]
        if last and (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() < gap:
            conn.commit()
            return None
        row = conn.execute(
            """SELECT request_id,manufacturer,oem,claim_generation
                 FROM dp_live_requests
                WHERE status='pending'
                ORDER BY requested_at ASC LIMIT 1"""
        ).fetchone()
        if not row:
            conn.commit()
            return None
        now = datetime.now(timezone.utc)
        claim_token = uuid.uuid4().hex
        lease_expires_at = (now + timedelta(seconds=600)).isoformat(timespec="seconds")
        changed = conn.execute(
            """UPDATE dp_live_requests
                  SET status='claimed', claimed_at=?,claim_token=?,claim_owner=?,
                      claim_generation=claim_generation+1,lease_expires_at=?
                WHERE request_id=? AND status='pending'""",
            (now.isoformat(timespec="seconds"), claim_token, producer_id, lease_expires_at, str(row[0])),
        ).rowcount
        conn.commit()
        if changed != 1:
            return None
        return {
            "request_id": str(row[0]),
            "manufacturer": str(row[1]),
            "oem": str(row[2]),
            "claim_token": claim_token,
            "producer_id": producer_id,
            "claim_generation": int(row[3]) + 1,
            "lease_expires_at": lease_expires_at,
            "protocol_version": 2,
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
    claim_token: str | None = None,
    claim_generation: int | None = None,
    producer_id: str | None = None,
) -> bool:
    init(db_file)
    status = str(result_status or "TECHNICAL_ERROR").upper()
    if status == "FOUND" and not _valid_dp(dealer_price_usd, source):
        status, dealer_price_usd, error_code = "NO_PRICE", None, "invalid_verified_dp"
    if status != "FOUND":
        dealer_price_usd = None
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
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
                WHERE request_id=? AND
                  ((status='pending' AND claim_token IS NULL) OR
                   (status='claimed' AND claim_token=? AND claim_generation=?
                    AND claim_owner=? AND lease_expires_at>?))""",
            (
                _now(),
                status,
                dealer_price_usd,
                source,
                current_oem,
                name,
                error_code,
                manufacturer,
                str(request_id),
                claim_token, claim_generation, producer_id, datetime.now(timezone.utc).isoformat(),
            ),
        ).rowcount
        if changed and status in {"CLOUDFLARE", "AUTH_REQUIRED"}:
            conn.execute(
                "UPDATE dp_live_coordination SET blocked_status=?,blocked_at=? WHERE singleton_id=1",
                (status, datetime.now(timezone.utc).isoformat()),
            )
        conn.commit()
        return changed == 1
