"""PHONE-FIRST / GitHub Actions / Telegram read-only connectivity check.

Hard guard: this module issues GET requests only. It does not import DCP runtime,
issue recovery tickets, POST sendMessage, or access Railway persistent storage.
Only sanitized categories are logged; never print Telegram API URL or exception
messages (they may embed a secret token).
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import sys
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

HOST = "api.telegram.org"
TIMEOUT_SECONDS = 10
KNOWN_HTTP = {400, 401, 403, 404, 409, 429, 500, 502, 503, 504}


def classify_error(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        code = int(exc.code)
        return f"HTTP_{code}" if code in KNOWN_HTTP else "HTTP_OTHER"
    if isinstance(exc, URLError):
        reason = exc.reason
        if isinstance(reason, socket.gaierror):
            return "DNS"
        if isinstance(reason, ssl.SSLError):
            return "TLS"
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return "TIMEOUT"
        safe_name = type(reason).__name__
        if safe_name in {"ConnectionResetError", "ConnectionRefusedError",
                         "ConnectionAbortedError", "BrokenPipeError",
                         "RemoteDisconnected", "OSError"}:
            return "URL_" + safe_name
        return "URL_OTHER"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "TIMEOUT"
    if isinstance(exc, ssl.SSLError):
        return "TLS"
    if isinstance(exc, HTTPException):
        safe_name = type(exc).__name__
        return "HTTP_CLIENT_" + safe_name if safe_name in {
            "RemoteDisconnected", "BadStatusLine", "IncompleteRead"} else "HTTP_CLIENT_OTHER"
    return "EXCEPTION_" + type(exc).__name__ if type(exc).__name__ in {
        "OSError", "ValueError", "JSONDecodeError", "UnicodeDecodeError"} else "EXCEPTION_OTHER"


def dns_preflight() -> str:
    try:
        addresses = socket.getaddrinfo(HOST, 443, proto=socket.IPPROTO_TCP)
        return "OK" if addresses else "EMPTY"
    except Exception as exc:
        return classify_error(exc)


def https_preflight() -> str:
    # No Telegram token is used for this request. HTTP 4xx is fine here:
    # receiving any HTTP status proves DNS, TCP and TLS reached the API.
    try:
        with urlopen(Request("https://" + HOST + "/", method="GET"),
                     timeout=TIMEOUT_SECONDS) as response:
            return "HTTP_" + str(response.status)
    except HTTPError as exc:
        return classify_error(exc)
    except Exception as exc:
        return classify_error(exc)


def getme_preflight(token: str) -> str:
    if not token:
        return "SECRET_NOT_CONFIGURED"
    if ":" not in token or len(token) > 256 or "\n" in token or "\r" in token:
        return "SECRET_INVALID_FORMAT"
    # A bot token appears in the request URL by Telegram API design. Never log it.
    request = Request("https://" + HOST + "/bot" + token + "/getMe", method="GET")
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            result = json.loads(response.read(4096))
        if isinstance(result, dict) and result.get("ok") is True and isinstance(
                result.get("result"), dict) and result["result"].get("is_bot") is True:
            return "OK"
        return "UNCONFIRMED"
    except Exception as exc:
        return classify_error(exc)


def main() -> int:
    dns = dns_preflight()
    https = https_preflight()
    token = os.getenv("DCP_PHONE_DIAG_BOT_TOKEN", "")
    getme = getme_preflight(token)
    lines = [
        "PHONE_FIRST_TRANSPORT_CHECK: READ_ONLY",
        "TELEGRAM_DNS: " + dns,
        "TELEGRAM_HTTPS: " + https,
        "TELEGRAM_GETME: " + getme,
        "TELEGRAM_SENDMESSAGE: DISABLED",
        "RAILWAY_VOLUME: UNTOUCHED",
        "DCP_ADMISSION: CLOSED",
    ]
    print("\n".join(lines), flush=True)
    summary = os.getenv("GITHUB_STEP_SUMMARY", "")
    if summary:
        try:
            with open(summary, "a", encoding="utf-8") as stream:
                stream.write("## PHONE-FIRST Telegram read-only diagnostics\n\n")
                stream.write("| Probe | Result |\n|---|---|\n")
                stream.write("| DNS | " + dns + " |\n")
                stream.write("| HTTPS | " + https + " |\n")
                stream.write("| getMe | " + getme + " |\n")
                stream.write("| sendMessage | DISABLED |\n")
                stream.write("| Railway volume | UNTOUCHED |\n")
        except OSError:
            pass
    # This is a connectivity diagnostic, not a message delivery verdict.
    # Missing secret deliberately fails so no one mistakes it for getMe PASS.
    if getme == "SECRET_NOT_CONFIGURED":
        return 2
    return 0 if getme == "OK" else 3


if __name__ == "__main__":
    sys.exit(main())
