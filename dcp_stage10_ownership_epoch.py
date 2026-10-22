"""Isolated Stage 10 server-side ownership gate (disabled unless explicitly invoked).

Shared SQLite authority, not a second broker. Never infers process cessation from
lease expiry. Admission of a different owner needs explicit revocation and
independent cessation evidence. Designed for test-only wiring first.
"""
import secrets
import sqlite3
from datetime import datetime, timezone

class OwnershipDenied(Exception):
    pass

def _init(db):
    db.execute("""CREATE TABLE IF NOT EXISTS dp_producer_ownership (
        singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
        epoch INTEGER NOT NULL, owner TEXT, nonce TEXT,
        state TEXT NOT NULL CHECK(state IN ('EMPTY','ACTIVE','QUARANTINED')),
        updated_at TEXT NOT NULL)""")
    db.execute("""INSERT OR IGNORE INTO dp_producer_ownership
        VALUES (1,0,NULL,NULL,'EMPTY',?)""",(datetime.now(timezone.utc).isoformat(),))

def _transaction(db_file, fn):
    with sqlite3.connect(str(db_file), timeout=10, isolation_level=None) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            _init(db)
            result=fn(db)
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise

def acquire(db_file, owner):
    if not isinstance(owner,str) or not owner or len(owner)>64:
        raise OwnershipDenied("invalid_owner")
    def op(db):
        epoch, existing, state=db.execute(
            "SELECT epoch,owner,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
        if state!="EMPTY":
            raise OwnershipDenied("ownership_requires_explicit_reconciliation")
        epoch+=1
        nonce=secrets.token_hex(32)
        db.execute("""UPDATE dp_producer_ownership SET epoch=?,owner=?,nonce=?,
            state='ACTIVE',updated_at=? WHERE singleton_id=1""",
            (epoch,owner,nonce,datetime.now(timezone.utc).isoformat()))
        return {"epoch":epoch,"owner":owner,"nonce":nonce}
    return _transaction(db_file,op)

def validate(db, proof):
    if not isinstance(proof,dict) or type(proof.get("epoch")) is not int:
        return False
    row=db.execute("SELECT epoch,owner,nonce,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
    return bool(row and row[3]=="ACTIVE" and row[:3]==(
        proof.get("epoch"),proof.get("owner"),proof.get("nonce")))

def revoke(db_file, *, expected_epoch, cessation_confirmed=False, evidence_ref=None):
    """Never use expiration as proof of old host cessation."""
    if not cessation_confirmed or not isinstance(evidence_ref,str) or len(evidence_ref)<16:
        raise OwnershipDenied("independent_cessation_evidence_required")
    def op(db):
        epoch, state=db.execute(
            "SELECT epoch,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
        if epoch!=expected_epoch or state not in ("ACTIVE","QUARANTINED"):
            raise OwnershipDenied("stale_epoch")
        if db.execute("SELECT 1 FROM dp_live_requests WHERE status IN ('claimed','orphaned') LIMIT 1").fetchone():
            raise OwnershipDenied("unreconciled_claims")
        db.execute("""UPDATE dp_producer_ownership SET state='EMPTY',owner=NULL,nonce=NULL,
            updated_at=? WHERE singleton_id=1""",(datetime.now(timezone.utc).isoformat(),))
    return _transaction(db_file,op)

def quarantine(db_file, epoch):
    def op(db):
        row=db.execute("SELECT epoch,state FROM dp_producer_ownership WHERE singleton_id=1").fetchone()
        if row[0]!=epoch or row[1]!="ACTIVE":
            raise OwnershipDenied("stale_epoch")
        db.execute("UPDATE dp_producer_ownership SET state='QUARANTINED' WHERE singleton_id=1")
    return _transaction(db_file,op)
