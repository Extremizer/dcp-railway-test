"""Server-side global producer ownership for the existing signed-v2 DCP broker.

Fail-closed by default; never auto-take over an expired producer.
Callers must hold the SAME SQLite write transaction for ownership checks,
claim-binding and result completion. This does not replace claim fencing.
"""
from __future__ import annotations
import hashlib
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone, timedelta

def enabled():
    return os.getenv("EXTREMIZER_DP_CLOUD_OWNERSHIP_ENABLED", "") == "1"

def _utc():
    return datetime.now(timezone.utc)

def _time(t):
    return t.isoformat(timespec="seconds")

def _hash(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()

def init(conn: sqlite3.Connection):
    conn.execute("""CREATE TABLE IF NOT EXISTS dp_cloud_owner (
        singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
        producer_id TEXT NOT NULL, generation INTEGER NOT NULL,
        token_sha256 TEXT NOT NULL, state TEXT NOT NULL,
        acquired_at TEXT NOT NULL, expires_at TEXT NOT NULL,
        released_at TEXT, CHECK(state IN ('HELD','RELEASED')))""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dp_cloud_claim_epochs (
        request_id TEXT PRIMARY KEY, claim_generation INTEGER NOT NULL,
        producer_id TEXT NOT NULL, owner_generation INTEGER NOT NULL,
        owner_token_sha256 TEXT NOT NULL)""")

def _valid(producer_id,token):
    return (isinstance(producer_id,str) and re.fullmatch(r"cloud-[a-z0-9-]{10,60}",producer_id)
            and isinstance(token,str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}",token))

def _row(conn):
    return conn.execute("""SELECT producer_id,generation,token_sha256,state,expires_at
                            FROM dp_cloud_owner WHERE singleton_id=1""").fetchone()

def _blocked_work(conn):
    return bool(conn.execute("""SELECT 1 FROM dp_live_requests
        WHERE status IN ('claimed','orphaned') LIMIT 1""").fetchone())

def acquire(conn, producer_id: str, *, now=None):
    """One new owner only after RELEASED and *no* in-flight/orphaned work.

    Must be called inside BEGIN IMMEDIATE and committed by caller.
    """
    if not enabled():
        return {"status":"DISABLED"}
    if not isinstance(producer_id,str) or not re.fullmatch(r"cloud-[a-z0-9-]{10,60}",producer_id):
        return {"status":"INVALID"}
    init(conn)
    if _blocked_work(conn):
        return {"status":"WORK_PENDING"}
    row = _row(conn)
    if row and row[3] != "RELEASED":
        # Expiry means STOP/OPERATOR RECONCILIATION, NEVER take-over.
        return {"status":"OWNED_OR_QUARANTINED"}
    generation = int(row[1])+1 if row else 1
    token = secrets.token_urlsafe(36)
    stamp = now or _utc()
    conn.execute("""INSERT INTO dp_cloud_owner
        (singleton_id,producer_id,generation,token_sha256,state,acquired_at,expires_at,released_at)
        VALUES(1,?,?,?,'HELD',?,?,NULL)
        ON CONFLICT(singleton_id) DO UPDATE SET
        producer_id=excluded.producer_id,generation=excluded.generation,
        token_sha256=excluded.token_sha256,state='HELD',acquired_at=excluded.acquired_at,
        expires_at=excluded.expires_at,released_at=NULL""",
        (producer_id,generation,_hash(token),_time(stamp),_time(stamp+timedelta(seconds=180))))
    return {"status":"ACQUIRED","owner_epoch":token,"generation":generation,"lease_seconds":180}

def allowed(conn, producer_id, token, *, now=None):
    if not enabled():
        return False
    if not _valid(producer_id,token):
        return False
    row = _row(conn)
    stamp=now or _utc()
    try:
        return bool(row and row[0]==producer_id and row[3]=="HELD" and
                    secrets.compare_digest(row[2],_hash(token)) and
                    datetime.fromisoformat(row[4])>stamp)
    except (ValueError,TypeError):
        return False

def renew(conn, producer_id, token, *, now=None):
    if not allowed(conn,producer_id,token,now=now):
        return {"status":"REJECTED"}
    stamp=now or _utc()
    changed=conn.execute("""UPDATE dp_cloud_owner SET expires_at=?
        WHERE singleton_id=1 AND producer_id=? AND state='HELD' AND token_sha256=?""",
        (_time(stamp+timedelta(seconds=180)),producer_id,_hash(token))).rowcount
    return {"status":"RENEWED","lease_seconds":180} if changed==1 else {"status":"REJECTED"}

def release(conn, producer_id, token, *, now=None):
    if not allowed(conn,producer_id,token,now=now):
        return {"status":"REJECTED"}
    if _blocked_work(conn):
        return {"status":"WORK_PENDING"}
    changed=conn.execute("""UPDATE dp_cloud_owner SET state='RELEASED',released_at=?
       WHERE singleton_id=1 AND state='HELD' AND producer_id=? AND token_sha256=?""",
        (_time(now or _utc()),producer_id,_hash(token))).rowcount
    return {"status":"RELEASED"} if changed==1 else {"status":"REJECTED"}

def bind_claim(conn, request_id, claim_generation, producer_id, token):
    """Bind exact row generation to global owner, inside claim transaction."""
    if not allowed(conn,producer_id,token):
        raise ValueError("owner_not_current")
    row=_row(conn)
    init(conn)
    conn.execute("""INSERT INTO dp_cloud_claim_epochs
        (request_id,claim_generation,producer_id,owner_generation,owner_token_sha256)
        VALUES(?,?,?,?,?)""",(request_id,claim_generation,producer_id,row[1],row[2]))

def completion_allowed(conn, request_id, claim_generation, producer_id, token):
    """Refuse stale completions, even if a per-request token still matches."""
    if not allowed(conn,producer_id,token):
        return False
    row=_row(conn)
    b=conn.execute("""SELECT claim_generation,producer_id,owner_generation,owner_token_sha256
                      FROM dp_cloud_claim_epochs WHERE request_id=?""",(request_id,)).fetchone()
    return bool(b and b[0]==claim_generation and b[1]==producer_id
                and b[2]==row[1] and secrets.compare_digest(b[3],row[2]))
