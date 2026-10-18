"""Separate signed producer evidence channel, NOT a Telegram/OEMixi request endpoint.

Producer obtains evidence from authorized exact DCP read-only verifier.
Server trusts a dedicated producer key, revalidates binding and stores only a
short-lived exact observation in dedicated preinitialized SQLite storage.
"""
from __future__ import annotations
import hashlib,hmac,json,secrets,sqlite3,time
from datetime import datetime,timezone
from fastapi import APIRouter,Header,HTTPException,Request

PATH="/internal/oemixi/catalog-evidence/v1"
VERSION="oemixi-catalog-evidence-v1"

def signature(secret,ts,nonce,body):
    if not isinstance(secret,str) or len(secret)<32:raise ValueError("producer_evidence_secret_required")
    msg=(VERSION+"\nPOST\n"+PATH+"\n"+str(ts)+"\n"+nonce+"\n"+hashlib.sha256(body).hexdigest()).encode()
    key=hashlib.sha256(b"extremizer-dcp-evidence-producer-v1\0"+secret.encode()).digest()
    return hmac.new(key,msg,hashlib.sha256).hexdigest()

def encode_evidence(proof):
    return json.dumps(proof,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()

def headers(secret,body,*,ts=None,nonce=None):
    ts=str(int(time.time()) if ts is None else ts)
    nonce=nonce or secrets.token_hex(16)
    return {"X-Evidence-Ts":ts,"X-Evidence-Nonce":nonce,
            "X-Evidence-Signature":signature(secret,ts,nonce,body)}

def initialize_evidence_store(db):
    with sqlite3.connect(db,timeout=3) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS oemixi_catalog_evidence (
            manufacturer TEXT NOT NULL,oem TEXT NOT NULL,source_ref TEXT NOT NULL,
            observed_at TEXT NOT NULL,payload_json TEXT NOT NULL,
            PRIMARY KEY(manufacturer,oem))""")
        c.execute("""CREATE TABLE IF NOT EXISTS oemixi_evidence_nonces (
            nonce TEXT PRIMARY KEY,received INTEGER NOT NULL)""")
        c.commit()

def validate_observation(proof,now=None):
    if not isinstance(proof,dict) or set(proof)!={
       "verified","manufacturer","requested_oem","current_oem","catalog",
       "name","msrp_usd","source_kind","source_ref","verified_at"}:
        return False
    if proof["verified"] is not True or proof["manufacturer"]!="Polaris":
        return False
    import re,math
    oem=proof["requested_oem"]
    if not isinstance(oem,str) or not re.fullmatch(r"[A-Z0-9-]{4,40}",oem):
        return False
    if proof["current_oem"]!=oem or proof["catalog"]!="Parts":
        return False
    if proof["source_kind"]!="live_public_form_metadata":
        return False
    from urllib.parse import urlsplit,parse_qs
    ref=str(proof["source_ref"] or "")
    u=urlsplit(ref)
    if u.scheme!="https" or u.hostname!="www.dealercostparts.com" or u.path!="/oemparts/partsearch/polaris":
        return False
    if parse_qs(u.query).get("partsearch")!=[oem]:
        return False
    try:
        v=float(proof["msrp_usd"])
        if not math.isfinite(v) or v<=0:return False
        dt=datetime.fromisoformat(str(proof["verified_at"]).replace("Z","+00:00"))
        age=(now or datetime.now(timezone.utc))-dt
        if dt.tzinfo is None or not (0<=age.total_seconds()<=120):return False
    except (ValueError,TypeError,OverflowError):
        return False
    return True

def create_evidence_router(*,db,producer_secret):
    signature(producer_secret,0,"0",b"")
    with sqlite3.connect(db) as c:
        names={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"oemixi_catalog_evidence","oemixi_evidence_nonces"}<=names:
        raise ValueError("evidence_schema_preflight_required")
    router=APIRouter()
    @router.post(PATH)
    async def accept(request:Request,x_evidence_ts:str=Header(default=""),
                     x_evidence_nonce:str=Header(default=""),
                     x_evidence_signature:str=Header(default="")):
        raw=await request.body()
        if not raw or len(raw)>4096:raise HTTPException(400,"invalid_size")
        try:
            ts=int(x_evidence_ts)
            if abs(time.time()-ts)>60 or not (16<=len(x_evidence_nonce)<=128):
                raise ValueError("stale")
            expected=signature(producer_secret,x_evidence_ts,x_evidence_nonce,raw)
            if not hmac.compare_digest(expected,x_evidence_signature):
                raise ValueError("bad_signature")
            proof=json.loads(raw)
            if encode_evidence(proof)!=raw or not validate_observation(proof):
                raise ValueError("invalid_evidence")
        except (ValueError,TypeError,UnicodeError):
            raise HTTPException(403,"evidence_unverified")
        with sqlite3.connect(db,timeout=3) as c:
            c.execute("BEGIN IMMEDIATE")
            try:
                c.execute("INSERT INTO oemixi_evidence_nonces VALUES(?,?)",
                          (x_evidence_nonce,int(time.time())))
            except sqlite3.IntegrityError:
                raise HTTPException(409,"evidence_replay")
            c.execute("""INSERT INTO oemixi_catalog_evidence VALUES(?,?,?,?,?)
                      ON CONFLICT(manufacturer,oem) DO UPDATE SET
                      source_ref=excluded.source_ref,observed_at=excluded.observed_at,
                      payload_json=excluded.payload_json""",
                      (proof["manufacturer"],proof["requested_oem"],proof["source_ref"],
                       proof["verified_at"],raw.decode()))
            c.commit()
        return {"status":"EVIDENCE_ACCEPTED"}
    return router

def trusted_evidence_reader(db):
    def read(manufacturer,oem):
        try:
            with sqlite3.connect(db,timeout=3) as c:
                row=c.execute("""SELECT payload_json FROM oemixi_catalog_evidence
                         WHERE manufacturer=? AND oem=?""",(manufacturer,oem)).fetchone()
            if row:
                payload=json.loads(row[0])
                if validate_observation(payload):
                    return payload
        except (sqlite3.Error,ValueError,TypeError):
            return None
        return None
    return read
