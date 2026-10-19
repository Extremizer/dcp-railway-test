"""OFF-BY-DEFAULT mobile manual recovery service.

This is intentionally NOT a DCP producer and DOES NOT enable broker admission.
Run in an isolated instance after independent security review; it has no
automatic DCP navigation/challenge-solving code.
"""
from __future__ import annotations

import base64
import os
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from dcp_phone_recovery_core import RecoveryGrants, RecoveryDenied, BrowserUnavailable, RemoteBrowser

app = FastAPI(title="EXTREMIZER DCP Recovery",docs_url=None,redoc_url=None,openapi_url=None)
app.state.enabled = False
app.state.grants = None
app.state.browser = None


class Exchange(BaseModel):
    ticket: str = Field(min_length=32,max_length=128)
    init_data: str = Field(min_length=1,max_length=8192)


class Tap(BaseModel):
    x: float
    y: float


class Text(BaseModel):
    text: str = Field(min_length=1,max_length=150)


class Scroll(BaseModel):
    dy: float


@app.on_event("startup")
def load_settings():
    if os.environ.get("DCP_PHONE_RECOVERY_ENABLED") != "1":
        app.state.enabled=False
        return
    try:
        token=os.environ["DCP_RECOVERY_TELEGRAM_BOT_TOKEN"]
        owner=int(os.environ["DCP_RECOVERY_OWNER_ID"])
        app.state.grants=RecoveryGrants(token,owner)
        app.state.browser=RemoteBrowser()
        app.state.enabled=True
    except (KeyError,ValueError,RecoveryDenied):
        app.state.enabled=False
        app.state.grants=None
        app.state.browser=None


@app.middleware("http")
async def security_headers(request,call_next):
    response=await call_next(request)
    response.headers["Cache-Control"]="no-store"
    response.headers["Referrer-Policy"]="no-referrer"
    response.headers["X-Content-Type-Options"]="nosniff"
    response.headers["X-Frame-Options"]="DENY"
    response.headers["Content-Security-Policy"]=(
        "default-src 'none'; script-src 'self' https://telegram.org; "
        "style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "frame-ancestors 'none'; form-action 'none'; base-uri 'none'"
    )
    return response


@app.get("/health")
def health():
    return {"service":"phone-recovery","ready":bool(app.state.enabled),
            "producer_running":False,"admission_opened":False}


def _enabled():
    if not app.state.enabled or app.state.grants is None or app.state.browser is None:
        raise HTTPException(status_code=503,detail="recovery_disabled")


def _authorized(header):
    _enabled()
    if not isinstance(header,str) or not header.startswith("Bearer ") or not app.state.grants.authorized(header[7:]):
        raise HTTPException(status_code=401,detail="owner_authorization_required")
    return header[7:]


@app.get("/recovery",response_class=HTMLResponse)
def recovery_page():
    _enabled()
    from pathlib import Path
    return HTMLResponse(Path(__file__).with_name("dcp_phone_recovery.html").read_text(encoding="utf-8"))


@app.get("/recovery.css")
def recovery_style():
    _enabled()
    from pathlib import Path
    return Response(Path(__file__).with_name("dcp_phone_recovery.css").read_text(encoding="utf-8"),
                    media_type="text/css")


@app.get("/recovery.js")
def recovery_script():
    _enabled()
    from pathlib import Path
    return Response(Path(__file__).with_name("dcp_phone_recovery.js").read_text(encoding="utf-8"),
                    media_type="application/javascript")


@app.post("/recovery/exchange")
def exchange(payload:Exchange):
    _enabled()
    try:
        bearer=app.state.grants.exchange(payload.ticket,payload.init_data)
        return {"bearer":bearer,"expires_in_seconds":720}
    except RecoveryDenied:
        raise HTTPException(status_code=401,detail="owner_authorization_required")


@app.get("/recovery/status")
def status(authorization:str|None=Header(default=None)):
    _authorized(authorization)
    return {"dcp_status":app.state.browser.status(),"producer_running":False,"admission_opened":False}


@app.get("/recovery/frame")
def frame(authorization:str|None=Header(default=None)):
    _authorized(authorization)
    try:
        result=app.state.browser.screenshot()
        data=base64.b64decode(result["data"],validate=True)
        return Response(data,media_type="image/jpeg",headers={"X-Viewport-Width":str(result["viewport"]["width"]),
                                                              "X-Viewport-Height":str(result["viewport"]["height"])})
    except (BrowserUnavailable,ValueError):
        raise HTTPException(status_code=503,detail="browser_unavailable")


@app.post("/recovery/tap")
def tap(payload:Tap,authorization:str|None=Header(default=None)):
    _authorized(authorization)
    try:
        app.state.browser.tap(payload.x,payload.y)
        return {"ok":True}
    except (RecoveryDenied,BrowserUnavailable):
        raise HTTPException(status_code=400,detail="invalid_browser_action")


@app.post("/recovery/text")
def type_text(payload:Text,authorization:str|None=Header(default=None)):
    _authorized(authorization)
    try:
        app.state.browser.type_text(payload.text)
        return {"ok":True}
    except (RecoveryDenied,BrowserUnavailable):
        raise HTTPException(status_code=400,detail="invalid_browser_action")


@app.post("/recovery/scroll")
def scroll(payload:Scroll,authorization:str|None=Header(default=None)):
    _authorized(authorization)
    try:
        app.state.browser.scroll(payload.dy)
        return {"ok":True}
    except (RecoveryDenied,BrowserUnavailable):
        raise HTTPException(status_code=400,detail="invalid_browser_action")


@app.post("/recovery/close")
def close(authorization:str|None=Header(default=None)):
    token=_authorized(authorization)
    app.state.grants.revoke(token)
    return {"ok":True}
