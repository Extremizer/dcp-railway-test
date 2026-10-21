"""Owner-only Telegram WebApp invitations for manual DCP session recovery.

Never opens broker admission or executes DCP requests. Network access only
through the explicitly injected Telegram send method, tested offline.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError
import ssl
import socket

class RecoveryNoticeError(RuntimeError):
    pass

def recovery_url(base_url: str, ticket: str) -> str:
    from urllib.parse import quote
    p=urlsplit(base_url)
    if (p.scheme!="https" or not p.hostname or p.username or p.password
            or p.query or p.fragment or p.path not in ("","/recovery")
            or p.port not in (None,443) or not ticket or len(ticket)>128
            or not ticket.replace("-","").replace("_","").isalnum()):
        raise RecoveryNoticeError("invalid_recovery_url")
    return "https://"+p.netloc+"/recovery#"+quote(ticket,safe="")

def message_payload(owner_id: int, url: str, status: str) -> dict:
    if type(owner_id) is not int or owner_id<=0:
        raise RecoveryNoticeError("invalid_owner")
    if status not in ("AUTH_REQUIRED","CLOUDFLARE"):
        raise RecoveryNoticeError("no_recovery_action_required")
    return {"chat_id":owner_id,
            "text":"EXTREMIZER DCP: нужна ручная проверка сессии. "
                   "Откройте защищённый браузер с телефона. "
                   "Ссылка одноразовая; цены остаются недоступны до проверки.",
            "reply_markup":{"inline_keyboard":[[{
                "text":"Открыть DCP-сессию",
                "web_app":{"url":url}
            }]]},
            "disable_notification":False}

def send_recovery_message(*, token: str, payload: dict, transport=None) -> None:
    if not isinstance(token,str) or not token or ":" not in token:
        raise RecoveryNoticeError("telegram_bot_not_configured")
    data=json.dumps(payload,separators=(",",":"),ensure_ascii=False).encode()
    req=Request("https://api.telegram.org/bot"+token+"/sendMessage",
                data=data,headers={"Content-Type":"application/json"},method="POST")
    sender=transport or urlopen
    try:
        with sender(req,timeout=8) as response:
            result=json.loads(response.read())
        if not isinstance(result,dict) or result.get("ok") is not True:
            raise RecoveryNoticeError("telegram_delivery_unconfirmed")
    except HTTPError as exc:
        # Deliberately inspect only known error classes; never log error
        # bodies, bot tokens, URLs, tickets, chat IDs, or Telegram content.
        code=int(exc.code)
        try:
            reason=json.loads(exc.read(2048)).get("description","").lower()
        except (ValueError,TypeError,AttributeError):
            reason=""
        if "chat not found" in reason:
            safe="chat_not_found"
        elif "button_type_invalid" in reason or "button type invalid" in reason:
            safe="webapp_button_invalid"
        elif "web app" in reason or "web_app" in reason or "url" in reason:
            safe="webapp_url_invalid"
        elif "bot was blocked" in reason:
            safe="bot_blocked"
        elif "forbidden" in reason:
            safe="forbidden"
        elif code==401:
            safe="bot_token_rejected"
        else:
            safe="other"
        raise RecoveryNoticeError(f"telegram_rejected_{code}_{safe}") from None
    except RecoveryNoticeError:
        raise
    except (TimeoutError, socket.timeout):
        raise RecoveryNoticeError("telegram_transport_timeout") from None
    except ssl.SSLError:
        raise RecoveryNoticeError("telegram_transport_tls") from None
    except URLError as exc:
        reason=exc.reason
        if isinstance(reason, socket.gaierror):
            category="dns"
        elif isinstance(reason, (TimeoutError,socket.timeout)):
            category="timeout"
        elif isinstance(reason, ssl.SSLError):
            category="tls"
        elif isinstance(reason, ConnectionRefusedError):
            category="refused"
        elif isinstance(reason, OSError):
            category="socket"
        else:
            category="other"
        raise RecoveryNoticeError("telegram_transport_"+category) from None
    except Exception:
        # No URL, token, chat ID, headers or response body in logs/exceptions.
        raise RecoveryNoticeError("telegram_transport_unknown") from None

class RecoveryNoticeCoordinator:
    """Throttle notifications and mint tickets only for blocked session states."""
    def __init__(self, grants, owner_id, base_url, send, *, cooldown_seconds=1800):
        self.grants=grants
        self.owner_id=owner_id
        self.base_url=base_url
        self.send=send
        self.cooldown_seconds=cooldown_seconds
        self._last_at=None
        self._last_status=None

    def notify(self,status,*,now=None):
        import time
        stamp=time.monotonic() if now is None else now
        if status not in ("AUTH_REQUIRED","CLOUDFLARE"):
            return False
        if self._last_at is not None and stamp-self._last_at < self.cooldown_seconds:
            return False
        ticket=self.grants.invite()
        payload=message_payload(self.owner_id,recovery_url(self.base_url,ticket),status)
        self.send(payload)
        self._last_at=stamp
        self._last_status=status
        return True
