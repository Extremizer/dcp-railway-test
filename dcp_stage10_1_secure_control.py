"""Stage 10.1 isolated authenticated ownership control, not mounted in production.

Trust boundaries: mock server with independent owner/RC keys. Request bytes,
method, path and timestamp are authenticated. The RC attestation is single-use,
epoch-scoped, bounded in time and explicitly asserts externally verified host
cessation. Lease expiry, agent health and producer statements never suffice.
"""
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
import dcp_stage10_ownership_epoch as epoch

class Rejected(Exception):
    pass

def canonical(data):
    return json.dumps(data, sort_keys=True, separators=(",",":"), allow_nan=False).encode()

def signature(key, method, path, ts, body):
    message=b"stage10.1-ownership-v1\n"+method.encode()+b"\n"+path.encode()+b"\n"+str(ts).encode()+b"\n"+hashlib.sha256(body).digest()
    return hmac.new(key,message,hashlib.sha256).hexdigest()

def verify(key, method, path, ts, body, sig, *, now=None):
    now=int(time.time()) if now is None else int(now)
    if type(ts) is not int or abs(now-ts)>60 or not isinstance(sig,str):
        raise Rejected("invalid_or_expired_signature")
    if not hmac.compare_digest(signature(key,method,path,ts,body),sig):
        raise Rejected("signature_mismatch")

def _used(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS dp_owner_attestations (
        token_hash TEXT PRIMARY KEY, epoch INTEGER NOT NULL, created_at TEXT NOT NULL)""")

def control(db, *, path, body, ts, sig, operator_key, now=None):
    """One mock-only control endpoint, no credential/secret in response."""
    if path!="/mock/ownership/revoke" or not isinstance(body,bytes):
        raise Rejected("route_not_allowed")
    verify(operator_key,"POST",path,ts,body,sig,now=now)
    try:
        obj=json.loads(body)
    except (ValueError,UnicodeError):
        raise Rejected("invalid_attestation")
    fields={"authority","scope","expected_epoch","owner","ceased_host","evidence_digest","nonce","issued_at","expires_at"}
    if not isinstance(obj,dict) or set(obj)!=fields:
        raise Rejected("invalid_attestation_shape")
    t=int(time.time()) if now is None else int(now)
    if (obj["authority"]!="release_control" or obj["scope"]!="producer.revoke"
        or type(obj["expected_epoch"]) is not int or not isinstance(obj["owner"],str)
        or obj["ceased_host"]!=obj["owner"]
        or not isinstance(obj["nonce"],str) or len(obj["nonce"])<32
        or not isinstance(obj["evidence_digest"],str) or len(obj["evidence_digest"])!=64
        or type(obj["issued_at"]) is not int or type(obj["expires_at"]) is not int
        or not obj["issued_at"]<=t<obj["expires_at"]
        or obj["expires_at"]-obj["issued_at"]>120):
        raise Rejected("unverifiable_cessation_attestation")
    # The signed statement is only admissible alongside an independent,
    # server-resident evidence receipt. The producer cannot mint receipts.
    digest=hashlib.sha256(body).hexdigest()
    with sqlite3.connect(str(db),timeout=10,isolation_level=None) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            epoch._init(conn);_used(conn)
            conn.execute("""CREATE TABLE IF NOT EXISTS dp_owner_cessation_receipts (
                evidence_digest TEXT PRIMARY KEY, owner TEXT NOT NULL,
                epoch INTEGER NOT NULL, verified_at INTEGER NOT NULL,
                authority TEXT NOT NULL, consumed_at INTEGER)""")
            state=conn.execute("SELECT epoch,owner,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
            receipt=conn.execute("""SELECT owner,epoch,verified_at,authority,consumed_at
                FROM dp_owner_cessation_receipts WHERE evidence_digest=?""",(obj["evidence_digest"],)).fetchone()
            if (state[:2]!=(obj["expected_epoch"],obj["owner"])
                or state[2] not in ("ACTIVE","QUARANTINED")
                or receipt is None or receipt[0:2]!=(obj["owner"],obj["expected_epoch"])
                or receipt[3]!="independent_host_controller" or receipt[4] is not None
                or receipt[2]>t or t-receipt[2]>120
                or conn.execute("SELECT 1 FROM dp_owner_attestations WHERE token_hash=?",(digest,)).fetchone()):
                raise Rejected("cessation_not_independently_proven")
            if conn.execute("SELECT 1 FROM dp_live_requests WHERE status IN ('claimed','orphaned') LIMIT 1").fetchone():
                raise Rejected("unreconciled_claim")
            conn.execute("INSERT INTO dp_owner_attestations VALUES(?,?,?)",(digest,obj["expected_epoch"],datetime.now(timezone.utc).isoformat()))
            conn.execute("UPDATE dp_owner_cessation_receipts SET consumed_at=? WHERE evidence_digest=?",(t,obj["evidence_digest"]))
            conn.execute("""UPDATE dp_producer_ownership SET state='EMPTY',owner=NULL,nonce=NULL,updated_at=?
                WHERE singleton_id=1""",(datetime.now(timezone.utc).isoformat(),))
            conn.commit()
            return {"revoked":True,"epoch":obj["expected_epoch"]}
        except BaseException:
            conn.rollback()
            raise

def register_verified_cessation(db, *, owner, epoch_value, evidence_digest, verified_at):
    """Test harness injection representing *independent* controller evidence.
    No HTTP endpoint exists to let a producer invoke this capability."""
    with sqlite3.connect(str(db)) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS dp_owner_cessation_receipts (
            evidence_digest TEXT PRIMARY KEY, owner TEXT NOT NULL,
            epoch INTEGER NOT NULL, verified_at INTEGER NOT NULL,
            authority TEXT NOT NULL, consumed_at INTEGER)""")
        c.execute("INSERT INTO dp_owner_cessation_receipts VALUES(?,?,?,?,?,NULL)",
                  (evidence_digest,owner,epoch_value,verified_at,"independent_host_controller"))
