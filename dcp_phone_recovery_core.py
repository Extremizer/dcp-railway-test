"""Mobile recovery authorization and constrained CDP control for EXTREMIZER.

This module has no supplier search, challenge solver, or broker write path.
A human owner remotely operates the *same* persistent browser that the cloud
producer will use. CDP stays bound to 127.0.0.1; only fixed safe operations
are exposed after Telegram WebApp signed identity + one-use ticket validation.
"""
from __future__ import annotations

import base64
from dataclasses import dataclass
import hashlib
import hmac
import json
import math
import secrets
import threading
import time
import urllib.parse
import urllib.request

DCP_HOST = "www.dealercostparts.com"
CDP_DISCOVERY = "http://127.0.0.1:9222/json"


class RecoveryDenied(Exception):
    """Fixed diagnostic category; never attach credentials or request contents."""
    def __init__(self, message="invalid_authentication", *, diagnostic="auth_invalid"):
        super().__init__(message)
        self.diagnostic = diagnostic


class BrowserUnavailable(Exception):
    pass


def validate_telegram_init_data(data: str, bot_token: str, owner_id: int,
                                *, now: int | None = None, max_age: int = 300) -> dict:
    """Official Telegram WebApp HMAC over ALL received fields except hash.

    Reject duplicate fields and forged/replayed/stale/wrong-owner identities.
    Never trust a frontend-provided Telegram user ID.
    """
    if not isinstance(data, str) or len(data) > 8192 or not bot_token or type(owner_id) is not int:
        raise RecoveryDenied("invalid_authentication", diagnostic="initdata_invalid_shape")
    try:
        pairs = urllib.parse.parse_qsl(data, keep_blank_values=True, strict_parsing=True)
        fields = dict(pairs)
        if len(pairs) != len(fields) or not fields.get("hash") or len(fields["hash"]) != 64:
            raise RecoveryDenied("invalid_authentication", diagnostic="initdata_fields_invalid")
        received_hash = fields.pop("hash")
        check = "\\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
        expected = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(received_hash.lower(), expected):
            raise RecoveryDenied("invalid_authentication", diagnostic="initdata_signature_invalid")
        t = int(fields["auth_date"])
        current = int(time.time()) if now is None else now
        if t > current + 30:
            raise RecoveryDenied("invalid_authentication", diagnostic="initdata_future")
        if t < current - max_age:
            raise RecoveryDenied("invalid_authentication", diagnostic="initdata_stale")
        user = json.loads(fields["user"])
        if not isinstance(user, dict) or type(user.get("id")) is not int or user["id"] != owner_id:
            raise RecoveryDenied("invalid_authentication", diagnostic="initdata_owner_mismatch")
        return {"user_id":owner_id, "auth_date":t}
    except RecoveryDenied:
        raise
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise RecoveryDenied("invalid_authentication", diagnostic="initdata_malformed") from exc

class RecoveryGrants:
    """Process-local one-use invitations and short-lived bearer sessions.

    Single-worker requirement is intentional: no shared keys/cookies in SQLite.
    No browser operation may be performed after grant or session expiry.
    """
    def __init__(self, bot_token: str, owner_id: int, *, ttl: int = 600):
        if not bot_token or type(owner_id) is not int or owner_id <= 0:
            raise RecoveryDenied("owner_configuration_missing")
        self.bot_token = bot_token
        self.owner_id = owner_id
        self.ttl = ttl
        self._tickets: dict[str,float] = {}
        self._sessions: dict[str,float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _fingerprint(value: str) -> str:
        return hashlib.sha256(value.encode()).hexdigest()

    def invite(self, *, now: float | None = None) -> str:
        current = time.time() if now is None else now
        ticket = secrets.token_urlsafe(32)
        with self._lock:
            self._tickets = {k:v for k,v in self._tickets.items() if v > current}
            self._tickets[self._fingerprint(ticket)] = current + self.ttl
        return ticket

    def exchange(self, ticket: str, init_data: str, *, now: float | None = None) -> str:
        current = time.time() if now is None else now
        if not isinstance(ticket,str) or len(ticket) > 128 or len(ticket) < 32:
            raise RecoveryDenied("invalid_invitation", diagnostic="invitation_invalid_shape")
        # Telegram identity is verified before consuming the ticket.
        validate_telegram_init_data(init_data,self.bot_token,self.owner_id,now=int(current))
        ticket_hash = self._fingerprint(ticket)
        with self._lock:
            expiry = self._tickets.pop(ticket_hash,None)
            if expiry is None or expiry <= current:
                raise RecoveryDenied("invalid_invitation", diagnostic="invitation_missing_or_expired")
            session = secrets.token_urlsafe(32)
            self._sessions[self._fingerprint(session)] = current + 12*60
            return session

    def authorized(self, bearer: str, *, now: float | None = None) -> bool:
        current = time.time() if now is None else now
        if not isinstance(bearer,str) or len(bearer) > 128 or len(bearer) < 32:
            return False
        h = self._fingerprint(bearer)
        with self._lock:
            expiry = self._sessions.get(h,0)
            if expiry <= current:
                self._sessions.pop(h,None)
                return False
            return True

    def revoke(self, bearer: str) -> None:
        with self._lock:
            self._sessions.pop(self._fingerprint(bearer),None)


def dcp_status_from_observation(observation: dict) -> str:
    """Local browser status only; never treat a challenge as authenticated."""
    if not isinstance(observation,dict) or observation.get("host") != DCP_HOST:
        return "BROWSER_DOWN"
    if observation.get("challenged"):
        return "CLOUDFLARE"
    if not observation.get("sign_out"):
        return "AUTH_REQUIRED"
    return "READY"


class RemoteBrowser:
    """A very small allowlisted CDP surface; no generic CDP/JS endpoint."""
    def __init__(self, endpoint: str=CDP_DISCOVERY):
        if endpoint != CDP_DISCOVERY:
            raise BrowserUnavailable("only_loopback_cdp_allowed")
        self.endpoint = endpoint
        self._lock = threading.Lock()

    def _call(self, method: str, params: dict | None = None):
        import websocket
        with urllib.request.urlopen(self.endpoint,timeout=3) as response:
            tabs = json.load(response)
        candidates = [t for t in tabs if t.get("type") == "page" and
                      urllib.parse.urlsplit(str(t.get("url") or "")).hostname == DCP_HOST]
        if len(candidates) != 1:
            raise BrowserUnavailable("exact_dcp_tab_required")
        socket_url = candidates[0].get("webSocketDebuggerUrl","")
        parsed = urllib.parse.urlsplit(socket_url)
        if parsed.scheme != "ws" or parsed.hostname not in ("127.0.0.1","localhost") or parsed.port != 9222:
            raise BrowserUnavailable("cdp_not_loopback")
        with self._lock:
            ws = websocket.create_connection(socket_url,timeout=8,suppress_origin=True)
            try:
                # Recheck current context at the instant of action, not just stale tab metadata.
                ws.send(json.dumps({"id":11,"method":"Runtime.evaluate","params":{
                    "returnByValue":True,"expression":
                    "(()=>({host:location.hostname,challenged:['just a moment','verify you are human','checking your browser','performing security verification'].some(x=>(document.body?.innerText||'').toLowerCase().includes(x)),sign_out:/sign out/i.test(document.body?.innerText||''),width:innerWidth,height:innerHeight}))()"}}))
                while True:
                    reply = json.loads(ws.recv())
                    if reply.get("id") == 11:
                        break
                observation = reply.get("result",{}).get("result",{}).get("value")
                if not isinstance(observation,dict) or observation.get("host") != DCP_HOST:
                    raise BrowserUnavailable("browser_context_changed")
                if method == "status":
                    return observation
                if method not in ("Page.captureScreenshot","Input.dispatchMouseEvent",
                                  "Input.insertText","Input.dispatchMouseEvent.scroll"):
                    raise BrowserUnavailable("unsupported_operation")
                if method == "Input.dispatchMouseEvent.scroll":
                    method = "Input.dispatchMouseEvent"
                ws.send(json.dumps({"id":12,"method":method,"params":params or {}}))
                while True:
                    reply = json.loads(ws.recv())
                    if reply.get("id") == 12:
                        break
                if "error" in reply:
                    raise BrowserUnavailable("browser_operation_failed")
                return {"data":reply.get("result",{}).get("data"),"viewport":{
                    "width":observation.get("width"),"height":observation.get("height")}}
            finally:
                ws.close()

    def status(self) -> str:
        try:
            return dcp_status_from_observation(self._call("status"))
        except (BrowserUnavailable, OSError, ValueError, TimeoutError):
            return "BROWSER_DOWN"

    def screenshot(self) -> dict:
        result = self._call("Page.captureScreenshot",{"format":"jpeg","quality":72,"fromSurface":True})
        if not result.get("data"):
            raise BrowserUnavailable("empty_frame")
        if len(result["data"]) > 2_000_000:
            raise BrowserUnavailable("frame_too_large")
        return result

    def tap(self, x: float, y: float) -> None:
        if (type(x) not in (int,float) or type(y) not in (int,float) or not math.isfinite(x)
                or not math.isfinite(y) or x < 0 or y < 0 or x > 3000 or y > 3000):
            raise RecoveryDenied("invalid_coordinates")
        p={"x":x,"y":y,"button":"left","clickCount":1}
        self._call("Input.dispatchMouseEvent",{**p,"type":"mousePressed"})
        self._call("Input.dispatchMouseEvent",{**p,"type":"mouseReleased"})

    def type_text(self, text: str) -> None:
        # Password or MFA text is transient; never logged, journalled or cached.
        if not isinstance(text,str) or not (1 <= len(text) <= 150) or any(ord(c) < 32 for c in text):
            raise RecoveryDenied("invalid_text")
        self._call("Input.insertText",{"text":text})

    def scroll(self, delta: float) -> None:
        if type(delta) not in (int,float) or not math.isfinite(delta) or abs(delta) > 1000:
            raise RecoveryDenied("invalid_scroll")
        self._call("Input.dispatchMouseEvent.scroll",{"type":"mouseWheel","x":200,"y":350,
                                                       "deltaX":0,"deltaY":delta})
