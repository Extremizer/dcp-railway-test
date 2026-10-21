"""Read-only TELEGRAM PHONE-FIRST bot/recipient checks (NO sendMessage)."""
from __future__ import annotations
import json
import os
import re
import socket
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


def result_code(error):
    # Never print exception text: Telegram API exception URLs embed bot token.
    if isinstance(error, HTTPError):
        return "HTTP_" + str(error.code) if error.code in (400, 401, 403, 404, 409, 429, 500, 502, 503, 504) else "HTTP_OTHER"
    if isinstance(error, URLError):
        reason = error.reason
        if isinstance(reason, socket.gaierror):
            return "DNS"
        if isinstance(reason, ssl.SSLError):
            return "TLS"
        if isinstance(reason, (TimeoutError, socket.timeout)):
            return "TIMEOUT"
        return "NETWORK_" + type(reason).__name__ if type(reason).__name__ in (
            "ConnectionResetError", "ConnectionRefusedError", "OSError") else "NETWORK_OTHER"
    if isinstance(error, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(error, ssl.SSLError):
        return "TLS"
    return "UNEXPECTED"


def api_get(token, endpoint, params=None):
    url = "https://api.telegram.org/bot" + token + "/" + endpoint
    if params:
        url += "?" + urlencode(params)
    try:
        request = Request(url, method="GET")
        with urlopen(request, timeout=10) as response:
            payload = json.loads(response.read(8192))
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            return "UNCONFIRMED", None
        return "OK", payload.get("result")
    except Exception as exc:
        return result_code(exc), None


def main():
    token = os.getenv("DCP_PHONE_DIAG_BOT_TOKEN", "")
    owner_id = os.getenv("DCP_PHONE_DIAG_OWNER_ID", "")
    if not token or not re.fullmatch(r"[0-9]{4,20}:[^\s]{8,200}", token):
        print("BOT_AUTH: SECRET_INVALID_OR_MISSING", flush=True)
        return 2
    status, bot = api_get(token, "getMe")
    if status != "OK" or not isinstance(bot, dict) or bot.get("is_bot") is not True:
        print("BOT_AUTH: " + (status if status != "OK" else "INVALID_RESULT"), flush=True)
        return 2
    print("BOT_AUTH: OK", flush=True)
    # Username is a public Telegram handle, unlike the bot token or chat ID.
    # Whitelist characters to keep logs free from untrusted control text.
    username = bot.get("username", "")
    if isinstance(username, str) and re.fullmatch(r"[A-Za-z0-9_]{5,32}", username):
        print("BOT_PUBLIC_USERNAME: @" + username, flush=True)
        print("BOT_EXPECTED_USERNAME_MATCH: " +
              ("YES" if username.casefold() == "extremizerdcp" else "NO"), flush=True)
    else:
        print("BOT_PUBLIC_USERNAME: INVALID_OR_MISSING", flush=True)
        print("BOT_EXPECTED_USERNAME_MATCH: NO", flush=True)

    webhook_status, webhook = api_get(token, "getWebhookInfo")
    if webhook_status == "OK" and isinstance(webhook, dict):
        # Print presence only. Never print webhook URL, error text or identifiers.
        print("WEBHOOK_STATE: " + ("SET" if webhook.get("url") else "NOT_SET"), flush=True)
    else:
        print("WEBHOOK_STATE: " + (webhook_status if webhook_status != "OK" else "UNCONFIRMED"), flush=True)

    if not re.fullmatch(r"[1-9][0-9]{0,18}", owner_id):
        print("PRIVATE_RECIPIENT: OWNER_SECRET_NOT_CONFIGURED", flush=True)
        print("TELEGRAM_SENDMESSAGE: DISABLED", flush=True)
        return 3

    chat_status, chat = api_get(token, "getChat", {"chat_id": owner_id})
    if chat_status == "OK" and isinstance(chat, dict):
        valid = str(chat.get("id")) == owner_id and chat.get("type") == "private"
        print("PRIVATE_RECIPIENT: " + ("CONFIRMED" if valid else "MISMATCH"), flush=True)
    else:
        print("PRIVATE_RECIPIENT: " + (chat_status if chat_status != "OK" else "UNCONFIRMED"), flush=True)
        valid = False
    print("TELEGRAM_SENDMESSAGE: DISABLED", flush=True)
    print("RAILWAY_VOLUME: UNTOUCHED", flush=True)
    return 0 if valid and webhook_status == "OK" else 4


if __name__ == "__main__":
    raise SystemExit(main())
