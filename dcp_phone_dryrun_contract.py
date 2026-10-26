"""MOCK_ONLY PHONE-FIRST dry-run contract.

This module reuses the existing signed-v2 producer protocol and owner fencing
for a staging-only safe request. It never calls DCP, never opens admission, and
never writes dealer price cache.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import dp_cloud_producer_ownership as owner


SAFE_MANUFACTURER = "SAFE-MFR"
SAFE_OEM = "SAFE-OEM-000"
SAFE_STATUS = "DRY_RUN_OK"


class DryRunError(Exception):
    def __init__(self, status_code: int, code: str):
        self.status_code = status_code
        self.code = code
        super().__init__(code)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def init(db_file: str | Path) -> None:
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS dp_live_requests (
            request_id TEXT PRIMARY KEY,
            status TEXT NOT NULL
        )""")
        owner.init(conn)
        conn.execute("""CREATE TABLE IF NOT EXISTS dcp_phone_dry_run_requests (
            request_id TEXT PRIMARY KEY,
            manufacturer TEXT NOT NULL,
            oem TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_at TEXT NOT NULL,
            claimed_at TEXT,
            finished_at TEXT,
            claim_token TEXT,
            claim_owner TEXT,
            claim_generation INTEGER NOT NULL DEFAULT 0,
            result_status TEXT,
            result_json TEXT,
            CHECK(status IN ('pending','claimed','finished'))
        )""")
        conn.commit()


def enqueue(db_file: str | Path, manufacturer: str, oem: str) -> dict:
    if str(manufacturer or "").strip() != SAFE_MANUFACTURER or str(oem or "").strip() != SAFE_OEM:
        raise DryRunError(400, "safe_fixture_required")
    init(db_file)
    request_id = "dryrun-safe-mfr-safe-oem-000"
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT status,result_status,result_json FROM dcp_phone_dry_run_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        if row and row[0] in {"pending", "claimed"}:
            conn.commit()
            return {"request_id": request_id, "status": row[0].upper()}
        if row and row[0] == "finished":
            conn.execute("DELETE FROM dcp_phone_dry_run_requests WHERE request_id=?", (request_id,))
        conn.execute("""INSERT INTO dcp_phone_dry_run_requests(
            request_id,manufacturer,oem,status,requested_at
        ) VALUES(?,?,?,'pending',?)""", (request_id, SAFE_MANUFACTURER, SAFE_OEM, _now()))
        conn.commit()
    return {"request_id": request_id, "status": "QUEUED"}



def enqueue_signed_once(db_file: str | Path, manufacturer: str, oem: str) -> dict:
    """One HMAC-authorized SAFE fixture, never requeue a finished run.

    This is for isolated staging when Telegram delivery is unavailable.
    The API handler verifies signed-v2 before calling this function.
    """
    if manufacturer != SAFE_MANUFACTURER or oem != SAFE_OEM:
        raise DryRunError(400, "safe_fixture_required")
    init(db_file)
    request_id = "dryrun-safe-mfr-safe-oem-000"
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        inserted = conn.execute("""INSERT OR IGNORE INTO dcp_phone_dry_run_requests(
            request_id,manufacturer,oem,status,requested_at
        ) VALUES(?,?,?,'pending',?)""",
            (request_id, SAFE_MANUFACTURER, SAFE_OEM, _now())).rowcount
        row = conn.execute(
            "SELECT status FROM dcp_phone_dry_run_requests WHERE request_id=?",
            (request_id,),
        ).fetchone()
        conn.commit()
    return {
        "request_id": request_id,
        "status": "QUEUED" if inserted == 1 else row[0].upper(),
        "created": inserted == 1,
    }


def get_result(db_file: str | Path, request_id: str) -> dict:
    init(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        row = conn.execute(
            """SELECT request_id,manufacturer,oem,status,result_status,result_json
               FROM dcp_phone_dry_run_requests WHERE request_id=?""",
            (str(request_id or ""),),
        ).fetchone()
    if not row:
        raise DryRunError(404, "request_not_found")
    result = json.loads(row[5]) if row[5] else None
    return {
        "request_id": row[0],
        "manufacturer": row[1],
        "oem": row[2],
        "status": row[4] or row[3].upper(),
        "result": result,
    }


def claim_next(db_file: str | Path, *, producer_id: str, owner_epoch: str | None) -> dict | None:
    init(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not owner.allowed(conn, producer_id, owner_epoch):
            conn.commit()
            return None
        row = conn.execute(
            """SELECT request_id,manufacturer,oem,claim_generation
               FROM dcp_phone_dry_run_requests
               WHERE status='pending' ORDER BY requested_at ASC LIMIT 1"""
        ).fetchone()
        if not row:
            conn.commit()
            return None
        token = "dryrun-" + row[0]
        generation = int(row[3]) + 1
        changed = conn.execute(
            """UPDATE dcp_phone_dry_run_requests
               SET status='claimed',claimed_at=?,claim_token=?,claim_owner=?,
                   claim_generation=?
               WHERE request_id=? AND status='pending'""",
            (_now(), token, producer_id, generation, row[0]),
        ).rowcount
        if changed == 1:
            owner.bind_claim(conn, row[0], generation, producer_id, owner_epoch)
        conn.commit()
    if changed != 1:
        return None
    return {
        "request_id": row[0],
        "manufacturer": row[1],
        "oem": row[2],
        "claim_token": token,
        "producer_id": producer_id,
        "claim_generation": generation,
        "protocol_version": 2,
    }


def complete(db_file: str | Path, payload: dict) -> dict:
    init(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """SELECT request_id,manufacturer,oem,status,claim_token,claim_owner,claim_generation
               FROM dcp_phone_dry_run_requests WHERE request_id=?""",
            (str(payload.get("request_id") or ""),),
        ).fetchone()
        if not row:
            raise DryRunError(404, "request_not_found")
        if row[3] != "claimed":
            raise DryRunError(409, "request_not_claimed")
        if (
            payload.get("protocol_version") != 2
            or payload.get("producer_id") != row[5]
            or payload.get("claim_token") != row[4]
            or payload.get("claim_generation") != row[6]
        ):
            raise DryRunError(409, "claim_fence_mismatch")
        if not owner.completion_allowed(conn, row[0], row[6], row[5], payload.get("owner_epoch")):
            raise DryRunError(409, "owner_epoch_mismatch")
        if (
            payload.get("status") != SAFE_STATUS
            or payload.get("manufacturer") != SAFE_MANUFACTURER
            or payload.get("oem") != SAFE_OEM
        ):
            raise DryRunError(400, "invalid_dry_run_result")
        result = {
            "status": SAFE_STATUS,
            "manufacturer": SAFE_MANUFACTURER,
            "oem": SAFE_OEM,
            "source": "vps-dry-run-no-dcp",
            "dcp_enabled": False,
            "admission": False,
        }
        conn.execute(
            """UPDATE dcp_phone_dry_run_requests
               SET status='finished',finished_at=?,result_status=?,result_json=?
               WHERE request_id=? AND status='claimed'""",
            (_now(), SAFE_STATUS, json.dumps(result, separators=(",", ":")), row[0]),
        )
        conn.commit()
    return {"ok": True, "status": SAFE_STATUS, "server_cache_committed": False}
