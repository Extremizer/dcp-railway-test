"""Stage10.1 mock-only authenticated owner acquire and status dispatcher.

No production HTTP route. Separate role keys never taken from producer claims.
Acquisition requires pre-registered approved owner identity and empty ownership.
"""
import json
import hmac
import hashlib
import re
import sqlite3
from dcp_stage10_1_secure_control import verify, Rejected
import dcp_stage10_ownership_epoch as epoch

def dispatch(db, *, method, path, body, timestamp, signature,
             producer_key, operator_key):
    if path not in ("/mock/ownership/acquire","/mock/ownership/status"):
        raise Rejected("not_mock_ownership_route")
    if method not in ("POST","GET"):
        raise Rejected("method_denied")
    if path.endswith("/acquire"):
        try:
            identity=json.loads(body).get("owner")
        except (ValueError,AttributeError,TypeError):
            raise Rejected("invalid_producer_identity")
        if not isinstance(identity,str):
            raise Rejected("invalid_producer_identity")
        key=hmac.new(producer_key,b"stage10.1-host-key"+identity.encode(),hashlib.sha256).digest()
    else:
        key=operator_key
    verify(key,method,path,timestamp,body,signature)
    if path.endswith("/status"):
        if method!="GET" or body:
            raise Rejected("invalid_status_method")
        with sqlite3.connect(str(db)) as conn:
            epoch._init(conn)
            row=conn.execute("SELECT epoch,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
            return {"epoch":row[0],"state":row[1]}  # never leak owner nonce
    if method!="POST":
        raise Rejected("invalid_acquire_method")
    try:
        data=json.loads(body)
    except (UnicodeDecodeError, ValueError, TypeError):
        raise Rejected("bad_acquire_body")
    if not isinstance(data,dict) or set(data)!={"owner","request_nonce"}:
        raise Rejected("invalid_acquire_contract")
    owner,nonce=data["owner"],data["request_nonce"]
    if (not isinstance(owner,str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,64}",owner)
        or not isinstance(nonce,str) or not re.fullmatch(r"[a-f0-9]{64}",nonce)):
        raise Rejected("invalid_identity_nonce")
    with sqlite3.connect(str(db),timeout=10,isolation_level=None) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            epoch._init(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS dp_owner_mock_allowlist (
                owner TEXT PRIMARY KEY, authorized INTEGER NOT NULL CHECK(authorized=1))""")
            conn.execute("""CREATE TABLE IF NOT EXISTS dp_owner_acquire_replay (
                request_nonce TEXT PRIMARY KEY, owner TEXT NOT NULL)""")
            if not conn.execute("SELECT 1 FROM dp_owner_mock_allowlist WHERE owner=?",(owner,)).fetchone():
                raise Rejected("unauthorized_owner")
            if conn.execute("SELECT 1 FROM dp_owner_acquire_replay WHERE request_nonce=?",(nonce,)).fetchone():
                raise Rejected("replayed_acquire")
            current=conn.execute("SELECT epoch,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
            if current[1]!="EMPTY":
                raise Rejected("owner_already_active")
            import secrets
            issued=secrets.token_hex(32)
            next_epoch=current[0]+1
            conn.execute("INSERT INTO dp_owner_acquire_replay VALUES(?,?)",(nonce,owner))
            conn.execute("""UPDATE dp_producer_ownership SET epoch=?,owner=?,nonce=?,state='ACTIVE',
                updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE singleton_id=1""",
                (next_epoch,owner,issued))
            conn.commit()
            return {"owner":owner,"epoch":next_epoch,"nonce":issued}
        except BaseException:
            conn.rollback()
            raise

def grant_mock_owner(db,owner):
    """Fixture setup only: cannot be called by any HTTP route."""
    with sqlite3.connect(str(db)) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS dp_owner_mock_allowlist (
            owner TEXT PRIMARY KEY, authorized INTEGER NOT NULL CHECK(authorized=1))""")
        conn.execute("INSERT OR REPLACE INTO dp_owner_mock_allowlist VALUES(?,1)",(owner,))
