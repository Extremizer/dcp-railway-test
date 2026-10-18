"""Signed producer evidence admission. Server verifies provenance independently.

Opt-in only: router not mounted by default; verifier callable supplied by
trusted server runtime. Never trusts producer's 'verified' flag.
"""
from __future__ import annotations
import hashlib
import hmac
import json
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from fastapi import APIRouter, Header, HTTPException, Request
from oemixi_category_bootstrap import bootstrap_category

PATH = "/internal/oemixi/catalog-enrollment/v1"
VERSION = "oemixi-catalog-enrollment-v1"
MAX_BODY = 4096

def _key(secret):
    if not isinstance(secret,str) or len(secret)<32:
        raise ValueError("enrollment_secret_required")
    return hashlib.sha256(b"extremizer-enrollment-v1\0"+secret.encode()).digest()

def sign(secret, ts, nonce, body):
    msg=(VERSION+"\nPOST\n"+PATH+"\n"+str(ts)+"\n"+nonce+"\n"
         +hashlib.sha256(body).hexdigest()).encode()
    return hmac.new(_key(secret),msg,hashlib.sha256).hexdigest()

def canonical_payload(manufacturer,oem):
    return json.dumps({"manufacturer":manufacturer,"oem":oem},
                      sort_keys=True,separators=(",",":")).encode()

def make_headers(secret,body,*,timestamp=None,nonce=None):
    ts=str(int(time.time()) if timestamp is None else timestamp)
    n=nonce or secrets.token_hex(16)
    return {"X-Enrollment-Ts":ts,"X-Enrollment-Nonce":n,
            "X-Enrollment-Signature":sign(secret,ts,n,body)}

def initialize_nonce_store(nonce_db):
    """Explicit preflight/migration step, never invoked by HTTP requests."""
    with sqlite3.connect(nonce_db, timeout=3) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS oemixi_enrollment_nonces
                  (nonce TEXT PRIMARY KEY,received INTEGER NOT NULL)""")
        c.commit()


def create_router(*,db_file,secret,verifier,nonce_db=None):
    """Explicit trusted-server registration, never an OEMixi consumer route."""
    if not callable(verifier):
        raise ValueError("trusted_catalog_verifier_required")
    _key(secret)
    if not nonce_db or str(nonce_db)==str(db_file):
        raise ValueError("dedicated_persistent_nonce_db_required")
    with sqlite3.connect(nonce_db, timeout=3) as c:
        if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='oemixi_enrollment_nonces'").fetchone():
            raise ValueError("nonce_schema_preflight_required")
    router=APIRouter()
    used=nonce_db

    @router.post(PATH)
    async def enroll(request:Request,
                     x_enrollment_ts:str=Header(default=""),
                     x_enrollment_nonce:str=Header(default=""),
                     x_enrollment_signature:str=Header(default="")):
        body=await request.body()
        if len(body)>MAX_BODY or not body:
            raise HTTPException(400,"invalid_body")
        try:
            ts=int(x_enrollment_ts)
            if abs(time.time()-ts)>60 or not (16<=len(x_enrollment_nonce)<=128):
                raise ValueError("expired")
            expected=sign(secret,x_enrollment_ts,x_enrollment_nonce,body)
            if not hmac.compare_digest(expected,x_enrollment_signature):
                raise ValueError("signature")
            data=json.loads(body)
            if (not isinstance(data,dict) or set(data)!={"manufacturer","oem"}
                or not isinstance(data["manufacturer"],str)
                or not isinstance(data["oem"],str)
                or canonical_payload(data["manufacturer"],data["oem"])!=body):
                raise ValueError("payload")
        except (ValueError,TypeError,UnicodeError):
            raise HTTPException(403,"invalid_enrollment_auth")
        with sqlite3.connect(used,timeout=3) as c:
            c.execute("BEGIN IMMEDIATE")
            c.execute("DELETE FROM oemixi_enrollment_nonces WHERE received<?",(ts-180,))
            try:
                c.execute("INSERT INTO oemixi_enrollment_nonces VALUES(?,?)",
                          (x_enrollment_nonce,int(time.time())))
            except sqlite3.IntegrityError:
                raise HTTPException(409,"replay_denied")
            c.commit()
        result=bootstrap_category(db_file,data["manufacturer"],data["oem"],
                                  trusted_catalog_verifier=verifier)
        if result.get("status")!="CATEGORY_VERIFIED":
            return {"status":result.get("status","CATEGORY_UNVERIFIED")}
        return {"status":"CATEGORY_VERIFIED","manufacturer":result["manufacturer"],
                "oem":result["oem"],"authority":result["authority"]}
    return router
