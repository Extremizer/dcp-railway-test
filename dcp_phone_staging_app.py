"""Strictly offline PHONE-FIRST staging UI. No DCP hosts, account or broker.

Chromium is launched by the isolated container and visits only a local mock
HTML page. Browser commands are delivered to loopback CDP, behind signed
Telegram WebApp authorization and one-use invitations.
"""
from __future__ import annotations
import base64
import os
import json
from pathlib import Path
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel
import dp_sync_protocol
import dp_cloud_producer_ownership as cloud_owner
import dcp_phone_dryrun_contract as dryrun
from dcp_phone_recovery_core import RecoveryGrants, RecoveryDenied, BrowserUnavailable, RemoteBrowser
from dcp_phone_recovery_notice import RecoveryNoticeCoordinator,send_recovery_message

def signing_key(scoped_token: str) -> bytes:
    import hashlib
    if not isinstance(scoped_token, str) or len(scoped_token.strip()) < 32:
        raise ValueError("dedicated_dp_signing_token_required")
    return hashlib.sha256(b"extremizer-dp-sync-v1\0" + scoped_token.encode()).digest()

class MockBrowser(RemoteBrowser):
    def _call(self,method,params=None):
        # Mock host is always a fixed, local file on the isolated staging image.
        import urllib.request,urllib.parse,websocket,threading
        with urllib.request.urlopen(self.endpoint,timeout=3) as r:
            pages=json.load(r)
        matches=[p for p in pages if p.get("type")=="page" and
           str(p.get("url","")).startswith("file:///app/dcp_phone_staging_mock.html")]
        if len(matches)!=1:raise BrowserUnavailable("mock_tab_required")
        target=urllib.parse.urlsplit(matches[0]["webSocketDebuggerUrl"])
        if target.hostname not in ("127.0.0.1","localhost") or target.port!=9222 or target.scheme!="ws":
            raise BrowserUnavailable("cdp_not_loopback")
        with self._lock:
            w=websocket.create_connection(matches[0]["webSocketDebuggerUrl"],timeout=5,suppress_origin=True)
            try:
                if method=="status":return {"host":"mock","sign_out":False,"challenged":False}
                if method not in ("Page.captureScreenshot","Input.dispatchMouseEvent","Input.insertText","Input.dispatchMouseEvent.scroll"):
                    raise BrowserUnavailable("command_not_allowed")
                if method=="Input.dispatchMouseEvent.scroll":method="Input.dispatchMouseEvent"
                w.send(json.dumps({"id":12,"method":method,"params":params or {}}))
                while True:
                    v=json.loads(w.recv())
                    if v.get("id")==12:break
                if "error" in v:raise BrowserUnavailable("mock_command_failed")
                return {"data":v.get("result",{}).get("data"),"viewport":{"width":1280,"height":800}}
            finally:w.close()
    def status(self):
        try:return "MOCK_ONLY" if self._call("status") else "BROWSER_DOWN"
        except Exception:return "BROWSER_DOWN"

app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
app.state.grants=None
app.state.browser=None
app.state.coordinator=None
app.state.invite_refs={}
app.state.dry_run_db=None
app.state.dry_run_key=None

def _staging_webapp_reference(url: str, references: dict[str, str]) -> str:
    """Use a non-reversible selector in the URL; the ticket stays server-side.

    Telegram owns the Mini App fragment, so a ticket placed after '#' is
    lost when Telegram injects tgWebAppData. Never put the ticket itself
    in a query string, browser history, request path or server logs.
    """
    import hashlib
    import re
    from urllib.parse import urlsplit, urlunsplit
    from dcp_phone_recovery_notice import RecoveryNoticeError

    parsed = urlsplit(url)
    ticket = parsed.fragment
    if (parsed.scheme != "https" or not parsed.hostname
            or parsed.username or parsed.password or parsed.port not in (None, 443)
            or parsed.path != "/recovery" or parsed.query
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", ticket)):
        raise RecoveryNoticeError("invalid_staging_webapp_reference")
    selector = hashlib.sha256(ticket.encode("ascii")).hexdigest()
    references[selector] = ticket
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path,
                       "ref=" + selector, ""))


def _telegram_getme_readonly(token: str, *, opener=None) -> str:
    """GET /getMe without sending messages; returns only whitelisted diagnostic codes.

    Never expose exception text, API response data, token or Telegram identity.
    """
    import urllib.request
    from urllib.error import HTTPError, URLError
    from http.client import HTTPException
    import ssl
    import socket
    if not isinstance(token, str) or ":" not in token:
        return "NOT_CONFIGURED"
    # Avoid constructing a URL from malformed secrets. Do not strip or log it.
    # Preserve the original value so staging never silently changes credentials.
    if any(c.isspace() for c in token) or any(ord(c) < 32 or ord(c) == 127 for c in token):
        return "INVALID_TOKEN_WHITESPACE_OR_CONTROL"
    if not token.isascii() or token.count(":") != 1 or not token.split(":", 1)[0].isdigit():
        return "INVALID_TOKEN_STRUCTURE"
    request = urllib.request.Request(
        "https://api.telegram.org/bot" + token + "/getMe", method="GET"
    )
    try:
        with (opener or urllib.request.urlopen)(request, timeout=8) as response:
            data = json.loads(response.read(4096))
        if (isinstance(data, dict) and data.get("ok") is True
                and isinstance(data.get("result"), dict)
                and data["result"].get("is_bot") is True
                and str(data["result"].get("username", "")).casefold() == "extremizerdcpbot"):
            return "OK"
        return "UNCONFIRMED_RESPONSE"
    except HTTPError as exc:
        return "HTTP_" + str(exc.code) if exc.code in (400, 401, 403, 404, 429, 500, 502, 503, 504) else "HTTP_OTHER"
    except URLError as exc:
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return "DNS_" + type(reason).__name__
        if isinstance(reason, ssl.SSLError):
            return "TLS_" + type(reason).__name__
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "TIMEOUT"
        safe = type(reason).__name__
        return "URL_" + safe if safe in ("ConnectionResetError", "ConnectionRefusedError", "RemoteDisconnected", "ConnectionAbortedError", "BrokenPipeError", "OSError") else "URL_OTHER"
    except (socket.timeout, TimeoutError):
        return "TIMEOUT"
    except ssl.SSLError:
        return "TLS"
    except HTTPException as exc:
        # Whitelisted stdlib exception types only; never log the exception message.
        # The latter can contain a URL with the bot token.
        name = type(exc).__name__
        safe_names = (
            "HTTPException", "InvalidURL", "UnknownProtocol",
            "UnknownTransferEncoding", "UnimplementedFileMode", "IncompleteRead",
            "ImproperConnectionState", "CannotSendRequest", "CannotSendHeader",
            "ResponseNotReady", "BadStatusLine", "LineTooLong",
            "RemoteDisconnected",
        )
        return "HTTP_CLIENT_" + name if type(exc).__module__ == "http.client" and name in safe_names else "HTTP_CLIENT_OTHER"
    except Exception as exc:
        name = type(exc).__name__
        return "EXCEPTION_" + name if name in ("TypeError", "ValueError", "JSONDecodeError", "UnicodeDecodeError", "ConnectionResetError", "ConnectionRefusedError", "ConnectionAbortedError", "BrokenPipeError", "OSError", "RuntimeError") else "EXCEPTION_OTHER"

@app.on_event("startup")
def configure():
    if os.getenv("DCP_PHONE_STAGING_MOCK_ONLY")!="1":return
    bot=os.getenv("DCP_RECOVERY_TELEGRAM_BOT_TOKEN","")
    owner=os.getenv("DCP_RECOVERY_OWNER_ID","")
    if not bot or not owner.isdigit():return
    app.state.grants=RecoveryGrants(bot,int(owner))
    app.state.invite_refs={}
    app.state.browser=MockBrowser()
    if os.getenv("DCP_PHONE_STAGING_DRY_RUN") == "1":
        token = os.getenv("DP_SYNC_TOKEN", "")
        state = Path(os.getenv("DCP_CLOUD_STATE_DIR", "/data"))
        if token and state.is_dir() and not state.is_symlink():
            app.state.dry_run_db = state / "phone-dry-run.sqlite"
            app.state.dry_run_key = signing_key(token)
            dryrun.init(app.state.dry_run_db)
    url=os.getenv("DCP_RECOVERY_PUBLIC_URL","")
    if url.startswith("https://"):
        from dcp_phone_recovery_notice import recovery_url
        # Strict HTTPS/hostname validation before enabling delivery.
        if recovery_url(url,"MockOnlyValidation") != url.rstrip("/")+"/recovery#MockOnlyValidation":
            return
        def send_mock_notice(payload):
            # A staging notice must never imply DCP is connected or logged in.
            button=payload["reply_markup"]["inline_keyboard"][0][0]
            staging={
                **payload,
                "text":"EXTREMIZER DCP — ТЕСТ PHONE-FIRST (MOCK_ONLY). "
                       "DCP отключён. Проверьте безопасное управление "
                       "тестовым Chromium с телефона.",
                "reply_markup":{"inline_keyboard":[[{
                    "text":"Открыть тестовый Chromium",
                    "web_app":{"url":_staging_webapp_reference(
                        button["web_app"]["url"], app.state.invite_refs)}
                }]]}
            }
            # Fail closed if Railway's token is not for the exact staging bot.
            # Never log the Telegram token, username, invite or owner identity.
            from dcp_phone_recovery_notice import RecoveryNoticeError
            if _telegram_getme_readonly(bot) != "OK":
                raise RecoveryNoticeError("telegram_identity_unverified")
            send_recovery_message(token=bot,payload=staging)
        app.state.coordinator=RecoveryNoticeCoordinator(
            app.state.grants,int(owner),url,send_mock_notice)
        # Read-only bot identity check; does not send, create invitations or touch volume ledger.
        import threading
        threading.Thread(
            target=lambda: print("DCP_TELEGRAM_GETME_PREFLIGHT",
                                 _telegram_getme_readonly(bot), flush=True),
            daemon=True, name="dcp-staging-telegram-getme"
        ).start()
        request_id=os.getenv("DCP_STAGING_TEST_NOTICE_ID","")
        if request_id:
            from dcp_phone_staging_notice_once import send_once
            import threading
            def deliver_mock_notice():
                # Never issue a token or message if the local mock browser
                # is not visible in this exact container.
                status=app.state.browser.status()
                try:
                    verdict=send_once(coordinator=app.state.coordinator,
                        state_dir=os.getenv("DCP_CLOUD_STATE_DIR","/data"),
                        request_id=request_id,browser_status=status)
                except Exception:
                    verdict="BLOCKED"
                print("DCP_STAGING_NOTICE_RESULT",verdict,flush=True)
            threading.Thread(target=deliver_mock_notice,daemon=True,
                name="dcp-staging-once").start()

@app.middleware("http")
async def secure(request,call_next):
    r=await call_next(request)
    r.headers.update({"Cache-Control":"no-store","Referrer-Policy":"no-referrer",
       "X-Content-Type-Options":"nosniff","X-Frame-Options":"DENY",
       "Content-Security-Policy":"default-src 'none';script-src 'self' https://telegram.org;style-src 'self';img-src 'self' data: blob:;connect-src 'self';frame-ancestors 'none'"})
    return r

@app.get("/health")
def health():
    return {"mode":"MOCK_ONLY","ready":app.state.browser is not None,"dcp_enabled":False,
            "admission":False,"dry_run_enabled":app.state.dry_run_db is not None}

def auth(header):
    if not app.state.grants or not app.state.browser:raise HTTPException(503,detail="staging_not_configured")
    if not header or not header.startswith("Bearer ") or not app.state.grants.authorized(header[7:]):
        raise HTTPException(401,detail="unauthorized")
    return header[7:]

async def signed_producer(request: Request, path: str, method: str,
                          ts: str | None, sig: str | None,
                          version: str | None, producer: str | None) -> bytes:
    if app.state.dry_run_db is None or app.state.dry_run_key is None:
        raise HTTPException(503,detail={"code":"dry_run_disabled"})
    if version != "2":
        raise HTTPException(401,detail={"code":"signed_v2_required"})
    body = await request.body()
    try:
        dp_sync_protocol.verify(app.state.dry_run_key, method, path, ts, producer, body, sig)
    except dp_sync_protocol.ProtocolError as exc:
        raise HTTPException(401,detail={"code":str(exc)}) from None
    return body

@app.get("/recovery")
def page():
    return HTMLResponse(Path(__file__).with_name("dcp_phone_recovery.html").read_text())

@app.get("/recovery.js")
def script():
    return Response(Path(__file__).with_name("dcp_phone_recovery.js").read_text(),media_type="application/javascript")

@app.get("/recovery.css")
def css():
    return Response(Path(__file__).with_name("dcp_phone_recovery.css").read_text(),media_type="text/css")

class Exchange(BaseModel):
    ticket:str = ""
    ticket_ref:str = ""
    init_data:str

@app.post("/recovery/exchange")
def exchange(p:Exchange):
    if not app.state.grants:raise HTTPException(503,detail="not_configured")
    try:
        if p.ticket_ref:
            # A selector is never an invitation. Resolve to the original
            # server-side ticket, then run the UNCHANGED signature/TTL/one-use check.
            import re
            if p.ticket or not re.fullmatch(r"[0-9a-f]{64}", p.ticket_ref):
                raise RecoveryDenied("invalid_invitation", diagnostic_code="INVITATION_FORMAT")
            ticket = app.state.invite_refs.get(p.ticket_ref)
            if ticket is None:
                raise RecoveryDenied("invalid_invitation", diagnostic_code="INVITATION_MISSING_OR_USED")
        else:
            ticket = p.ticket
        bearer = app.state.grants.exchange(ticket, p.init_data)
        return {"bearer":bearer,"expires_in_seconds":720}
    except RecoveryDenied as exc:
        # Fixed server-side code only. Never log identity, token, invitation or initData.
        allowed = frozenset({
            "INITDATA_FORMAT", "INITDATA_SIGNATURE", "INITDATA_EXPIRED",
            "INITDATA_FUTURE", "INITDATA_OWNER", "INVITATION_FORMAT",
            "INVITATION_EXPIRED", "INVITATION_MISSING_OR_USED",
        })
        code = exc.diagnostic_code if exc.diagnostic_code in allowed else "AUTH_DENIED"
        print("DCP_STAGING_EXCHANGE_DENIED", code, flush=True)
        raise HTTPException(401, detail="unauthorized") from None

class DryRunRequest(BaseModel):
    manufacturer:str
    oem:str

@app.post("/recovery/dry-run")
def recovery_dry_run(p:DryRunRequest, authorization:str|None=Header(None)):
    auth(authorization)
    if app.state.dry_run_db is None:
        raise HTTPException(503,detail={"code":"dry_run_disabled"})
    try:
        return dryrun.enqueue(app.state.dry_run_db,p.manufacturer,p.oem)
    except dryrun.DryRunError as exc:
        raise HTTPException(exc.status_code,detail={"code":exc.code}) from None

@app.get("/recovery/dry-run/{request_id}")
def recovery_dry_run_result(request_id:str, authorization:str|None=Header(None)):
    auth(authorization)
    if app.state.dry_run_db is None:
        raise HTTPException(503,detail={"code":"dry_run_disabled"})
    try:
        return dryrun.get_result(app.state.dry_run_db,request_id)
    except dryrun.DryRunError as exc:
        raise HTTPException(exc.status_code,detail={"code":exc.code}) from None

@app.post("/internal/dp-cloud/dry-run/queue-safe")
async def dry_run_queue_safe(request: Request,
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
    x_dp_protocol: str | None = Header(default=None),
    x_dp_producer_id: str | None = Header(default=None)):
    """Signed-v2 only, isolated MOCK_ONLY fixture; no Telegram or live agent."""
    path = "/internal/dp-cloud/dry-run/queue-safe"
    body = await signed_producer(
        request, path, "POST", x_dp_sync_ts, x_dp_sync_sig,
        x_dp_protocol, x_dp_producer_id,
    )
    try:
        payload = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, detail={"code": "invalid_dry_run_payload"}) from None
    if not isinstance(payload, dict) or payload != {
        "manufacturer": "SAFE-MFR", "oem": "SAFE-OEM-000",
    }:
        raise HTTPException(400, detail={"code": "safe_fixture_required"})
    return dryrun.enqueue_signed_once(
        app.state.dry_run_db, payload["manufacturer"], payload["oem"],
    )

@app.post("/internal/dp-cloud/owner/{operation}")
async def dry_run_owner(operation: str, request: Request,
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
    x_dp_protocol: str | None = Header(default=None),
    x_dp_producer_id: str | None = Header(default=None)):
    path=f"/internal/dp-cloud/owner/{operation}"
    body=await signed_producer(request,path,"POST",x_dp_sync_ts,x_dp_sync_sig,x_dp_protocol,x_dp_producer_id)
    try:
        payload=json.loads(body)
    except (ValueError,UnicodeDecodeError):
        raise HTTPException(400,detail={"code":"invalid_owner_payload"}) from None
    if operation == "acquire" and payload != {}:
        raise HTTPException(400,detail={"code":"invalid_owner_payload"})
    if operation in {"renew","release"} and (set(payload)!={"owner_epoch"} or not isinstance(payload["owner_epoch"],str)):
        raise HTTPException(400,detail={"code":"invalid_owner_payload"})
    if operation not in {"acquire","renew","release"}:
        raise HTTPException(404,detail={"code":"operation_not_found"})
    import sqlite3
    with sqlite3.connect(str(app.state.dry_run_db),timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if operation == "acquire":
            outcome=cloud_owner.acquire(conn,x_dp_producer_id)
        elif operation == "renew":
            outcome=cloud_owner.renew(conn,x_dp_producer_id,payload["owner_epoch"])
        else:
            outcome=cloud_owner.release(conn,x_dp_producer_id,payload["owner_epoch"])
        conn.commit()
    if outcome["status"] not in {"ACQUIRED","RENEWED","RELEASED"}:
        raise HTTPException(409,detail={"code":outcome["status"].lower()})
    return outcome

@app.get("/internal/dp-cloud/next")
async def dry_run_next(request: Request,
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
    x_dp_protocol: str | None = Header(default=None),
    x_dp_producer_id: str | None = Header(default=None),
    x_dp_owner_epoch: str | None = Header(default=None)):
    await signed_producer(request,"/internal/dp-cloud/next","GET",x_dp_sync_ts,x_dp_sync_sig,x_dp_protocol,x_dp_producer_id)
    if not x_dp_owner_epoch:
        raise HTTPException(409,detail={"code":"owner_epoch_missing"})
    return {"request":dryrun.claim_next(app.state.dry_run_db,producer_id=x_dp_producer_id,owner_epoch=x_dp_owner_epoch)}

@app.post("/internal/dp-cloud/result")
async def dry_run_result(request: Request,
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
    x_dp_protocol: str | None = Header(default=None),
    x_dp_producer_id: str | None = Header(default=None)):
    body=await signed_producer(request,"/internal/dp-cloud/result","POST",x_dp_sync_ts,x_dp_sync_sig,x_dp_protocol,x_dp_producer_id)
    try:
        payload=json.loads(body)
    except (ValueError,UnicodeDecodeError):
        raise HTTPException(400,detail={"code":"invalid_result_payload"}) from None
    if payload.get("producer_id") != x_dp_producer_id:
        raise HTTPException(409,detail={"code":"claim_fence_mismatch"})
    try:
        return dryrun.complete(app.state.dry_run_db,payload)
    except dryrun.DryRunError as exc:
        raise HTTPException(exc.status_code,detail={"code":exc.code}) from None

@app.get("/recovery/status")
def status(authorization:str|None=Header(None)):
    auth(authorization)
    return {"dcp_status":app.state.browser.status(),"dcp_enabled":False}

@app.get("/recovery/frame")
def frame(authorization:str|None=Header(None)):
    auth(authorization)
    try:
        image=app.state.browser.screenshot()
        return Response(base64.b64decode(image["data"]),media_type="image/jpeg",
          headers={"X-Viewport-Width":"1280","X-Viewport-Height":"800"})
    except Exception:raise HTTPException(503,detail="mock_browser_unavailable")

class Tap(BaseModel):
    x:float
    y:float
@app.post("/recovery/tap")
def tap(p:Tap,authorization:str|None=Header(None)):
    auth(authorization)
    app.state.browser.tap(p.x,p.y)
    return {"ok":True}

class Typing(BaseModel):
    text:str
@app.post("/recovery/text")
def typing(p:Typing,authorization:str|None=Header(None)):
    auth(authorization)
    app.state.browser.type_text(p.text)
    return {"ok":True}

class Scroll(BaseModel):
    dy:float
@app.post("/recovery/scroll")
def scroll(p:Scroll,authorization:str|None=Header(None)):
    auth(authorization)
    app.state.browser.scroll(p.dy)
    return {"ok":True}

@app.post("/recovery/close")
def close(authorization:str|None=Header(None)):
    app.state.grants.revoke(auth(authorization))
    return {"ok":True}
