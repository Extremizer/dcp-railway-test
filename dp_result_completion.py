"""Fenced DCP result completion; one connection, one commit, no network."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import math
import secrets
import hashlib
import json
import sqlite3

import dp_live_bridge as bridge
import dp_live_health
import dcp_category_authority


class CompletionError(Exception):
    def __init__(self, status_code: int, code: str):
        self.status_code, self.code = status_code, code
        super().__init__(code)


MANUFACTURERS = {"Arctic Cat", "Can-Am", "CFMoto", "Honda", "Indian", "Kawasaki",
                 "KTM", "Polaris", "Sea-Doo", "Ski-Doo", "Suzuki", "Yamaha"}
STATUSES = {"FOUND", "NOT_FOUND", "NO_PRICE", "CLOUDFLARE", "AUTH_REQUIRED", "TECHNICAL_ERROR"}


def _identity(manufacturer, oem):
    m, part = bridge._request_key(manufacturer, oem)
    if m not in MANUFACTURERS or not part:
        raise CompletionError(400, "invalid_result_identity")
    return m, part


def _dealer_source(source, manufacturer, oem):
    value = str(source or "").strip()
    allowed = {f"live_dom:#add_{oem} .c2 .dbl", f"server_html:#add_{oem} .c2 .dbl"}
    if manufacturer == "Ski-Doo":
        allowed.add("catalog_product:exact_sku_price")
    if value not in allowed:
        raise CompletionError(400, "unverified_dealer_source")
    return "verified_local_dcp:" + value


def _write_cache(conn, manufacturer, oem, price, source, now):
    conn.execute("""CREATE TABLE IF NOT EXISTS dealer_price_cache (
        manufacturer TEXT NOT NULL,oem TEXT NOT NULL,dealer_price_usd REAL NOT NULL,
        source TEXT,first_seen_at TEXT NOT NULL,last_verified_at TEXT NOT NULL,
        last_used_at TEXT,use_count INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(manufacturer,oem))""")
    conn.execute("""INSERT INTO dealer_price_cache
        (manufacturer,oem,dealer_price_usd,source,first_seen_at,last_verified_at,last_used_at,use_count)
        VALUES(?,?,?,?,?,?,NULL,0) ON CONFLICT(manufacturer,oem) DO UPDATE SET
        dealer_price_usd=excluded.dealer_price_usd,source=excluded.source,
        last_verified_at=excluded.last_verified_at""", (manufacturer,oem,price,source,now,now))


def _write_identity(conn, manufacturer, oem, current_oem, name, source, now):
    conn.execute("""CREATE TABLE IF NOT EXISTS probnik_oem_identity (
        manufacturer TEXT NOT NULL,oem TEXT NOT NULL,current_oem TEXT NOT NULL,name TEXT,
        verified INTEGER NOT NULL DEFAULT 0,source_kind TEXT,source_ref TEXT,
        first_seen_at TEXT NOT NULL,last_verified_at TEXT NOT NULL,PRIMARY KEY(manufacturer,oem))""")
    conn.execute("""INSERT INTO probnik_oem_identity
        (manufacturer,oem,current_oem,name,verified,source_kind,source_ref,first_seen_at,last_verified_at)
        VALUES(?,?,?,?,1,'verified_local_dcp',?,?,?) ON CONFLICT(manufacturer,oem) DO UPDATE SET
        current_oem=excluded.current_oem,name=excluded.name,verified=1,
        source_kind=excluded.source_kind,source_ref=excluded.source_ref,
        last_verified_at=excluded.last_verified_at""", (manufacturer,oem,current_oem,name,source,now,now))


def complete_result(db_file, payload: dict) -> dict:
    """Caller verifies the body-bound signature BEFORE invoking this function.

    No schema initialization or independent core upsert/commit occurs here.
    Legacy producers lacking ownership proof cannot complete a request.
    """
    with closing(sqlite3.connect(str(db_file), timeout=10, isolation_level=None)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute("SELECT * FROM dp_live_requests WHERE request_id=?",
                               (str(payload.get("request_id") or ""),)).fetchone()
            if row is None:
                raise CompletionError(404, "request_not_found")
            if row["status"] != "claimed":
                raise CompletionError(409, "request_not_claimed")
            token = payload.get("claim_token")
            generation = payload.get("claim_generation")
            if (type(payload.get("protocol_version")) is not int or payload.get("protocol_version") != 2 or not isinstance(token, str)
                or not row["claim_token"] or not secrets.compare_digest(token, row["claim_token"])
                or type(generation) is not int or generation != row["claim_generation"]
                or payload.get("producer_id") != row["claim_owner"]):
                raise CompletionError(409, "claim_fence_mismatch")
            now_dt = datetime.now(timezone.utc)
            deadline = dp_live_health._parse_datetime(row["lease_expires_at"])
            if deadline is None or now_dt >= deadline:
                raise CompletionError(409, "claim_lease_expired")
            status = str(payload.get("status") or "").strip().upper()
            if status not in STATUSES:
                raise CompletionError(400, "invalid_status")
            requested_manufacturer, requested_oem = bridge._request_key(row["manufacturer"], row["oem"])
            manufacturer = None
            price = source = current_oem = name = None
            now = now_dt.isoformat(timespec="seconds")
            if status == "FOUND":
                health = conn.execute("SELECT status FROM dp_live_health WHERE singleton_id=1").fetchone()
                if (health and health[0] in {"CLOUDFLARE", "AUTH_REQUIRED"}) or bridge._breaker_status(conn):
                    raise CompletionError(409, "dcp_session_blocked")
                manufacturer, query_oem = _identity(payload.get("manufacturer"), payload.get("query_oem"))
                if query_oem != requested_oem or manufacturer != requested_manufacturer:
                    raise CompletionError(400, "result_identity_mismatch")
                _, current_oem = _identity(manufacturer, payload.get("current_oem") or requested_oem)
                _, matched_sku = _identity(manufacturer, payload.get("matched_sku"))
                if manufacturer != "Polaris" and (matched_sku != requested_oem or current_oem != requested_oem):
                    raise CompletionError(400, "result_sku_mismatch")
                value = payload.get("dealer_price_usd")
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise CompletionError(400, "invalid_dp")
                if payload.get("currency") != "USD":
                    raise CompletionError(400, "invalid_dp_currency")
                price = float(value)
                if manufacturer == "Polaris":
                    from polaris_dealer_cart_completion_contract import SOURCE
                    if payload.get("source") != SOURCE:
                        raise CompletionError(400, "unverified_dealer_source")
                    source = SOURCE
                else:
                    source = _dealer_source(payload.get("source"), manufacturer, requested_oem)
                name = str(payload.get("name") or "")[:300] or None
                authority = dcp_category_authority.resolve(conn, requested_manufacturer, requested_oem)
                if (authority is None or any(row[k] != authority[k] for k in ("item_category","category_source_kind","category_source_ref","category_verified_at"))):
                    raise CompletionError(409, "CATEGORY_UNVERIFIED")
                if manufacturer != "Polaris":
                    raise CompletionError(409, "CATEGORY_UNVERIFIED")
                from polaris_dealer_cart_completion_contract import verify_polaris_cart_proof,CartProofError
                if authority.get("resolved_oem") != current_oem:
                    raise CompletionError(409, "CATEGORY_UNVERIFIED")
                try:
                    verify_polaris_cart_proof(payload,requested_oem=requested_oem,resolved_oem=current_oem,category_evidence=authority)
                except CartProofError:
                    raise CompletionError(409, "DEALER_CART_UNVERIFIED") from None
                proof=payload["dealer_cart_proof"]
                # Keep the cache source canonical. Request/resolved identity and
                # immutable proof correlation live in the existing recovery journal.
                evidence_digest=hashlib.sha256(json.dumps(
                    proof,sort_keys=True,
                    separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
                authority_digest=hashlib.sha256(json.dumps(
                    authority,sort_keys=True,separators=(",",":"),ensure_ascii=True).encode()).hexdigest()
                conn.execute("""INSERT INTO dp_live_recovery_events
                    (request_id,claim_generation,event_type,actor,evidence_sha256,plan_sha256,created_at)
                    VALUES(?,?,'polaris_cart_verified_found',?,?,?,?)""",
                    (row["request_id"],generation,payload["producer_id"],
                     evidence_digest,authority_digest,now))

                _write_cache(conn, manufacturer, requested_oem, price, source, now)
                if current_oem != requested_oem:
                    _write_cache(conn, manufacturer, current_oem, price, source, now)
                # Identity discovery by manufacturer guessing is prohibited.
            changed = conn.execute("""UPDATE dp_live_requests SET status='finished',finished_at=?,
                result_status=?,dealer_price_usd=?,result_source=?,current_oem=?,item_name=?,
                error_code=?,result_manufacturer=?,cache_committed_at=? WHERE request_id=? AND status='claimed'
                AND claim_token=? AND claim_generation=? AND claim_owner=?""",
                (now,status,price,source,current_oem,name,str(payload.get("error_code") or "")[:120] or None,
                 manufacturer,now if status == "FOUND" else None,row["request_id"],token,generation,payload["producer_id"])).rowcount
            if changed != 1:
                raise CompletionError(409, "claim_fence_mismatch")
            if status in {"CLOUDFLARE", "AUTH_REQUIRED"}:
                conn.execute("UPDATE dp_live_coordination SET blocked_status=?,blocked_at=? WHERE singleton_id=1",
                             (status,now_dt.isoformat()))
            conn.commit()
            return {"ok": True, "status": status, "server_cache_committed": status == "FOUND"}
        except Exception:
            conn.rollback()
            raise
