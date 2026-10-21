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
from dcp_phone_recovery_core import RecoveryGrants, RecoveryDenied, BrowserUnavailable, RemoteBrowser
from dcp_phone_recovery_notice import RecoveryNoticeCoordinator,send_recovery_message

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
        name = type(exc).__name__
        return "HTTP_CLIENT_" + name if name in ("RemoteDisconnected", "BadStatusLine", "IncompleteRead", "ResponseNotReady") else "HTTP_CLIENT_OTHER"
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
    app.state.browser=MockBrowser()
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
                    "web_app":{"url":button["web_app"]["url"]}
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
       "Content-Security-Policy":"default-src 'none';script-src 'self' https://telegram.org;style-src 'self';img-src 'self' data:;connect-src 'self';frame-ancestors 'none'"})
    return r

@app.get("/health")
def health():
    return {"mode":"MOCK_ONLY","ready":app.state.browser is not None,"dcp_enabled":False,"admission":False}

def auth(header):
    if not app.state.grants or not app.state.browser:raise HTTPException(503,detail="staging_not_configured")
    if not header or not header.startswith("Bearer ") or not app.state.grants.authorized(header[7:]):
        raise HTTPException(401,detail="unauthorized")
    return header[7:]

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
    ticket:str
    init_data:str

@app.post("/recovery/exchange")
def exchange(p:Exchange):
    if not app.state.grants:raise HTTPException(503,detail="not_configured")
    try:return {"bearer":app.state.grants.exchange(p.ticket,p.init_data),"expires_in_seconds":720}
    except RecoveryDenied:raise HTTPException(401,detail="unauthorized")

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
