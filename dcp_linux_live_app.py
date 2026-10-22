"""Owner-gated manual DCP entry + read-only identity + signed owner acquisition.

No job polling, no dealer FOUND, no cart mutation or broker result completion.
Never connect to DCP until owner authorizes a separate LIVE GO and taps open.
"""
from __future__ import annotations
import base64
import json
import os
import time
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request,urlopen
from fastapi import Header,HTTPException
from dcp_phone_recovery_app import app, _authorized
import dcp_linux_cdp_identity as cdp
from dcp_linux_admission import AdmissionError, safe_profile, persist_journal
from dp_cloud_producer_client import CloudV2Producer

app.state.linux_lock=None
app.state.broker_admitted=False
app.state.identity_status="NOT_CHECKED"
app.state.owner_acquired_at=None

@app.on_event("startup")
def init_linux_admission():
    # On any persistent journal anomaly, the owner UI may still be needed for
    # recovery; never acquire broker claims or grant dealer-ready status.
    state=os.getenv("DCP_CLOUD_STATE_DIR","")
    try:
        app.state.linux_lock=safe_profile(state)
        app.state.journal_ok=True
    except (AdmissionError, OSError):
        app.state.journal_ok=False

def _source_verified():
    built=Path("/app/dcp_build_source_head")
    expected=os.getenv("DCP_EXPECTED_SOURCE_HEAD","")
    return built.is_file() and len(expected)==40 and built.read_text().strip()==expected

def _broker_fresh():
    acquired=getattr(app.state,"owner_acquired_at",None)
    return (bool(app.state.broker_admitted) and acquired is not None
            and 0 <= time.monotonic()-acquired < 120)

def _manual_go():
    if not _source_verified():
        raise HTTPException(403,"source_integrity_not_verified")
    if os.getenv("DCP_LIVE_NAVIGATION_GO")!="1":
        raise HTTPException(403,"live_navigation_go_missing")

def _send_cdp(tab, method, params):
    import websocket
    address=tab.get("webSocketDebuggerUrl","")
    w=urlsplit(address)
    if w.scheme!="ws" or w.hostname not in ("127.0.0.1","localhost") or w.port!=9222:
        raise AdmissionError("cdp_not_loopback")
    ws=websocket.create_connection(address,timeout=6,suppress_origin=True)
    try:
        ws.send(json.dumps({"id":349,"method":method,"params":params}))
        for _ in range(20):
            response=json.loads(ws.recv())
            if response.get("id")==349:
                if "error" in response:raise AdmissionError("cdp_navigation_failed")
                return response.get("result",{})
        raise AdmissionError("cdp_no_reply")
    finally:ws.close()

@app.post("/recovery/open-dealer")
def owner_open_dealer(authorization:str|None=Header(default=None)):
    _authorized(authorization)
    _manual_go()
    # One owner-controlled action: fixed approved DCP URL, no arbitrary target.
    try:
        choices=[t for t in cdp.tabs() if t.get("type")=="page"]
        blanks=[t for t in choices if t.get("url")=="about:blank"]
        if len(choices)!=1 or len(blanks)!=1:
            raise AdmissionError("unexpected_open_browser_context")
        _send_cdp(blanks[0],"Page.navigate",{"url":"https://www.dealercostparts.com/"})
        return {"status":"OWNER_DCP_NAVIGATION_STARTED","dealer_authenticated":False}
    except (AdmissionError,cdp.CdpUnverified,OSError,ValueError):
        raise HTTPException(409,"manual_browser_context_unverified")

def _identity():
    expected=os.getenv("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT","")
    result=cdp.exact_dealer_identity(expected)
    app.state.identity_status=result.get("status","DEALER_IDENTITY_UNVERIFIED")
    return result

@app.get("/admission/status")
def admission_status(authorization:str|None=Header(default=None)):
    _authorized(authorization)
    result=_identity()
    return {"browser_available":cdp.preflight()!="BROWSER_DOWN",
            "dealer_authenticated":result.get("dealer_authenticated") is True,
            "dealer_status":result.get("status"),
            "journal_healthy":bool(getattr(app.state,"journal_ok",False)),
            "broker_admitted":_broker_fresh(),
            "claims_enabled":False,"dealer_price_publish_enabled":False}

def _signed_broker():
    url=os.getenv("DCP_STAGING_BROKER_ORIGIN","")
    split=urlsplit(url)
    # Explicit staging-only remote, strict HTTPS, never production/real supplier.
    if (split.scheme!="https" or not split.hostname or
        split.path not in ("","/") or split.query or split.fragment or
        split.username or split.password or split.hostname in
        {"www.dealercostparts.com","dcp-railway-test-production.up.railway.app"}):
        raise AdmissionError("BROKER_ORIGIN_NOT_APPROVED")
    allowed=os.getenv("DCP_STAGING_BROKER_EXPECTED_HOST","")
    if not allowed or split.hostname!=allowed:raise AdmissionError("BROKER_HOST_UNVERIFIED")
    key_text=os.getenv("DCP_STAGING_SIGNING_KEY_B64","")
    try:
        key=base64.b64decode(key_text,validate=True)
    except (ValueError,TypeError):
        raise AdmissionError("BROKER_SIGNING_KEY_INVALID")
    if len(key)!=32:raise AdmissionError("BROKER_SIGNING_KEY_INVALID")
    producer=os.getenv("DCP_STAGING_PRODUCER_ID","")
    def send(method,path,body,headers):
        if path!="/internal/dp-cloud/owner/acquire" or method!="POST":
            raise AdmissionError("BROKER_ENDPOINT_DENIED")
        request=Request(url.rstrip("/")+path,data=body,headers=headers,method=method)
        with urlopen(request,timeout=8) as reply:
            if reply.status!=200:raise AdmissionError("BROKER_ADMISSION_REJECTED")
            payload=reply.read(8193)
            if len(payload)>8192:raise AdmissionError("BROKER_RESPONSE_TOO_LARGE")
            return json.loads(payload)
    return CloudV2Producer(producer,key,send)

@app.post("/admission/acquire")
def admission_acquire(authorization:str|None=Header(default=None)):
    _authorized(authorization)
    _manual_go()
    if app.state.broker_admitted or not getattr(app.state,"journal_ok",False) or app.state.linux_lock is None:
        raise HTTPException(409,"journal_or_fencing_not_admitted")
    identity=_identity()
    if identity.get("dealer_context_verified") is not True or identity.get("dealer_authenticated") is not True:
        raise HTTPException(403,"exact_dealer_identity_not_verified")
    path=os.getenv("DCP_CLOUD_STATE_DIR","")
    try:
        # Persist uncertainty before any broker call, so crash cannot silently resume.
        persist_journal(path,{"state":"ADMISSION_PENDING","claim":None,"status":"QUARANTINED"})
        app.state.journal_ok=False
        client=_signed_broker()
        proof=client.acquire()
        if proof.get("status")!="ACQUIRED" or not proof.get("owner_epoch"):
            raise AdmissionError("BROKER_ADMISSION_REJECTED")
        persist_journal(path,{"state":"OWNER_HELD","claim":None,"status":"NO_CLAIMS",
                              "owner_generation":proof.get("generation")})
        app.state.broker_admitted=True
        app.state.owner_acquired_at=time.monotonic()
        # Token deliberately remains process-local only; no claims are issued.
        app.state.broker_client=client
        return {"status":"BROKER_ADMITTED_NO_CLAIMS","claims_enabled":False,
                "dealer_price_publish_enabled":False}
    except Exception:
        raise HTTPException(409,"broker_admission_unverified")
