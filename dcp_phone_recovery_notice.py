"""Owner-only Telegram WebApp invitations for manual DCP session recovery.

Never opens broker admission or executes DCP requests. Network access only
through the explicitly injected Telegram send method, tested offline.
"""
from __future__ import annotations
import json
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

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
    except RecoveryNoticeError:
        raise
    except Exception as exc:
        # No URL, token, chat ID, headers or response body in logs/exceptions.
        raise RecoveryNoticeError("telegram_delivery_failed") from None
