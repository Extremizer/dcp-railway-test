# -*- coding: utf-8 -*-
"""Trusted local live-DP agent.

DCP credentials/cookies stay inside local Chrome.
Only OEM + verification result travel to Railway over HTTPS.
Requests are signed with a one-minute HMAC derived from OEMIXIBOT_TOKEN;
the Telegram token itself is never sent as an HTTP credential.

The agent is also the source of truth for DCP live-session health:
READY / CLOUDFLARE / AUTH_REQUIRED / BROWSER_DOWN / TECHNICAL_ERROR.
It never attempts to bypass Cloudflare.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path
from datetime import datetime, timezone

import oemixibot_dcp_price
import dealercostparts_manufacturer_finder_v6_6 as finder

BASE_URL = "https://dcp-railway-test-production.up.railway.app"
POLL_SECONDS = float(os.getenv("DP_SYNC_POLL_SECONDS", "3.0") or "3.0")
HEALTH_READY_SECONDS = float(
    os.getenv("DP_SYNC_HEALTH_READY_SECONDS", "1800") or "1800"
)
HEALTH_BLOCKED_SECONDS = float(
    os.getenv("DP_SYNC_HEALTH_BLOCKED_SECONDS", "15") or "15"
)
STARTUP_GRACE_SECONDS = float(
    os.getenv("DP_SYNC_STARTUP_GRACE_SECONDS", "15") or "15"
)
STATUS_FILE = Path(__file__).with_name("dp_sync_agent_status.json")
_STATUS = "offline"
_MUTEX_HANDLE = None
_DCP_STATUS = "UNKNOWN"
_DCP_DETAIL = "not_checked"
_DCP_CHECKED_AT = None
_LAST_HEALTH_CHECK_MONO = 0.0

_DCP_HEALTH_STATUSES = {
    "READY",
    "CLOUDFLARE",
    "AUTH_REQUIRED",
    "BROWSER_DOWN",
    "TECHNICAL_ERROR",
}

_CF_MARKERS = (
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "checking your browser",
    "performing security verification",
    "security verification",
    "cloudflare",
    "подтвердите, что вы человек",
    "проверка безопасности",
    "выполнение проверки безопасности",
    "enable javascript and cookies to continue",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _write_status(status: str, *, detail: str | None = None) -> None:
    global _STATUS
    _STATUS = str(status or "offline")
    payload = {
        "status": _STATUS,
        "updated_at": _utc_now(),
        "pid": os.getpid(),
        "poll_seconds": POLL_SECONDS,
        "dcp_status": _DCP_STATUS,
        "dcp_detail": _DCP_DETAIL,
        "dcp_checked_at": _DCP_CHECKED_AT,
    }
    if detail:
        payload["detail"] = str(detail)[:200]
    tmp = STATUS_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, STATUS_FILE)


def _acquire_singleton() -> bool:
    global _MUTEX_HANDLE
    if os.name != "nt":
        return True
    import ctypes
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.CreateMutexW(None, False, r"Local\ExtremizerPro_DP_Sync_Agent")
    if not handle:
        raise OSError("CreateMutexW failed")
    ERROR_ALREADY_EXISTS = 183
    if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
        kernel32.CloseHandle(handle)
        return False
    _MUTEX_HANDLE = handle
    return True


def effective_status(*, stale_after_seconds: float = 15.0) -> dict:
    try:
        data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(data.get("updated_at") or ""))
        now = datetime.now(timezone.utc)
        if updated.tzinfo is None:
            updated = updated.replace(tzinfo=timezone.utc)
        age = max(0.0, (now - updated.astimezone(timezone.utc)).total_seconds())
        if age > stale_after_seconds:
            return {**data, "status": "offline", "age_seconds": round(age, 1)}
        return {**data, "age_seconds": round(age, 1)}
    except Exception:
        return {"status": "offline", "age_seconds": None}


def _dealer_token() -> str:
    value = os.getenv("OEMIXIBOT_TOKEN", "").strip()
    if value:
        return value
    if os.name == "nt":
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
                value, _ = winreg.QueryValueEx(key, "OEMIXIBOT_TOKEN")
                value = str(value or "").strip()
                if value:
                    return value
        except Exception:
            pass
    raise RuntimeError("OEMIXIBOT_TOKEN is not configured locally")


def _key() -> bytes:
    return hashlib.sha256(
        b"extremizer-dp-sync-v1\0" + _dealer_token().encode("utf-8")
    ).digest()


def _headers(path: str) -> dict[str, str]:
    ts = str(int(time.time()))
    message = f"v1\n{path}\n{ts}".encode("utf-8")
    sig = hmac.new(_key(), message, hashlib.sha256).hexdigest()
    return {
        "X-DP-Sync-Ts": ts,
        "X-DP-Sync-Sig": sig,
    }


def _request(path: str, *, method: str = "GET", payload: dict | None = None) -> dict:
    headers = _headers(path)
    body = None
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(
        BASE_URL + path,
        data=body,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(req, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _claim() -> dict | None:
    return _request("/internal/dp-sync/next").get("request")


def _finish(request_id: str, result: dict) -> None:
    status = str(result.get("status") or "TECHNICAL_ERROR").upper()
    payload = {
        "request_id": request_id,
        "status": status,
        "dealer_price_usd": result.get("dealer_price_usd"),
        "source": result.get("source"),
        "current_oem": result.get("current_oem"),
        "name": result.get("name"),
        "error_code": result.get("error_code") or (None if status == "FOUND" else status),
        "manufacturer": result.get("manufacturer"),
    }
    _request("/internal/dp-sync/result", method="POST", payload=payload)


def _agent_status_for_dcp(status: str) -> str:
    if status in {"CLOUDFLARE", "AUTH_REQUIRED"}:
        return "human_required"
    if status in {"BROWSER_DOWN", "TECHNICAL_ERROR"}:
        return "offline"
    return "online"


def _report_health(status: str, detail: str | None) -> None:
    payload = {
        "status": status,
        "detail": detail,
        "source": "windows_dp_sync_agent",
        "checked_at": _DCP_CHECKED_AT or _utc_now(),
    }
    try:
        _request("/internal/dp-sync/health", method="POST", payload=payload)
    except urllib.error.HTTPError as exc:
        # Safe staged rollout: older Railway code can return 404 until the
        # health endpoint is deployed. Existing DP polling must keep working.
        print("DP_SYNC_HEALTH_REPORT_HTTP", exc.code, flush=True)
    except Exception as exc:
        print("DP_SYNC_HEALTH_REPORT_ERROR", type(exc).__name__, flush=True)


def _set_dcp_health(
    status: str,
    *,
    detail: str | None = None,
    report: bool = True,
) -> None:
    global _DCP_STATUS, _DCP_DETAIL, _DCP_CHECKED_AT
    normalized = str(status or "TECHNICAL_ERROR").strip().upper()
    if normalized not in _DCP_HEALTH_STATUSES:
        normalized = "TECHNICAL_ERROR"
    _DCP_STATUS = normalized
    _DCP_DETAIL = str(detail or normalized).strip()[:200]
    _DCP_CHECKED_AT = _utc_now()
    _write_status(
        _agent_status_for_dcp(normalized),
        detail=f"dcp_{normalized.lower()}",
    )
    if report:
        _report_health(normalized, _DCP_DETAIL)


def _preflight_dcp() -> str:
    """Inspect the existing DCP Chrome session without starting a search."""
    global _LAST_HEALTH_CHECK_MONO
    _LAST_HEALTH_CHECK_MONO = time.monotonic()

    try:
        tabs = oemixibot_dcp_price._tabs()
    except Exception as exc:
        _set_dcp_health(
            "BROWSER_DOWN",
            detail=f"cdp_9222_{type(exc).__name__}",
        )
        return _DCP_STATUS

    pages = [x for x in tabs if x.get("type") == "page"]
    for tab in pages:
        tab_url = str(tab.get("url") or "")
        tab_title = str(tab.get("title") or "")
        marker_text = (tab_url + "\n" + tab_title).lower()
        if (
            "challenges.cloudflare.com" in marker_text
            or any(marker in marker_text for marker in _CF_MARKERS)
        ):
            _set_dcp_health("CLOUDFLARE", detail="cloudflare_tab_detected")
            return _DCP_STATUS

    dcp_pages = [
        x for x in pages
        if oemixibot_dcp_price.DCP_HOST in str(x.get("url") or "")
    ]
    if not dcp_pages:
        _set_dcp_health("TECHNICAL_ERROR", detail="dcp_tab_missing")
        return _DCP_STATUS

    dcp_pages.sort(
        key=lambda x: ("partsearch" not in str(x.get("url") or ""),)
    )
    tab = dcp_pages[0]
    expression = """(()=>{
      const body=(document.body?.innerText||'');
      const lower=body.toLowerCase();
      const challenged=[
        'just a moment','verify you are human','verifying you are human',
        'checking your browser','performing security verification',
        'security verification','cloudflare',
        'подтвердите, что вы человек','проверка безопасности',
        'выполнение проверки безопасности',
        'enable javascript and cookies to continue'
      ].some(x=>lower.includes(x));
      const authorized=/sign out/i.test(body);
      return {
        challenged,
        authorized,
        href:location.href,
        title:document.title||''
      };
    })()"""
    try:
        probe = oemixibot_dcp_price._runtime_evaluate(tab, expression)
    except Exception as exc:
        _set_dcp_health(
            "TECHNICAL_ERROR",
            detail=f"cdp_runtime_{type(exc).__name__}",
        )
        return _DCP_STATUS

    if probe.get("challenged"):
        _set_dcp_health("CLOUDFLARE", detail="cloudflare_dom_detected")
    elif not probe.get("authorized"):
        _set_dcp_health("AUTH_REQUIRED", detail="dcp_sign_out_not_detected")
    else:
        _set_dcp_health("READY", detail="authorized_session_ready")
    return _DCP_STATUS


def _health_check_due() -> bool:
    if _LAST_HEALTH_CHECK_MONO <= 0:
        return True
    interval = (
        HEALTH_READY_SECONDS
        if _DCP_STATUS == "READY"
        else HEALTH_BLOCKED_SECONDS
    )
    return (time.monotonic() - _LAST_HEALTH_CHECK_MONO) >= max(1.0, interval)


def _auto_identity_price(oem: str) -> dict:
    manufacturers = []
    for cat in finder.CATALOGS:
        if cat.kind == "parts" and cat.manufacturer not in manufacturers:
            manufacturers.append(cat.manufacturer)
    for manufacturer in manufacturers:
        r = oemixibot_dcp_price.get_dealer_price_dict(manufacturer, oem)
        status = str(r.get("status") or "").upper()
        if status == "FOUND":
            return {**r, "manufacturer": manufacturer}
        if status in {
            "CLOUDFLARE", "AUTH_REQUIRED", "BROWSER_DOWN", "TECHNICAL_ERROR",
        }:
            return r
    return {"status": "NOT_FOUND", "error_code": "IDENTITY_NOT_FOUND"}


def run_once() -> bool:
    item = _claim()
    if not item:
        return False

    request_id = str(item["request_id"])
    manufacturer = str(item["manufacturer"])
    oem = str(item["oem"])
    try:
        result = (
            _auto_identity_price(oem)
            if manufacturer == "__AUTO__"
            else oemixibot_dcp_price.get_dealer_price_dict(manufacturer, oem)
        )
    except Exception as exc:
        result = {
            "status": "TECHNICAL_ERROR",
            "dealer_price_usd": None,
            "source": None,
            "current_oem": None,
            "name": None,
            "message": type(exc).__name__,
        }

    status = str(result.get("status") or "TECHNICAL_ERROR").upper()
    _finish(request_id, result)

    if status in {"CLOUDFLARE", "AUTH_REQUIRED", "TECHNICAL_ERROR"}:
        _set_dcp_health(
            status,
            detail=str(result.get("message") or status),
        )
    else:
        _write_status("online", detail=status)

    print(
        "DP_SYNC",
        manufacturer,
        oem,
        status,
        flush=True,
    )
    return True


def main() -> int:
    if not _acquire_singleton():
        print("DP_SYNC_AGENT already running; exiting duplicate", flush=True)
        return 23
    _dealer_token()
    _write_status("online", detail="started")
    print("DP_SYNC_AGENT starting via signed HTTPS", flush=True)

    if STARTUP_GRACE_SECONDS > 0:
        time.sleep(STARTUP_GRACE_SECONDS)
    _preflight_dcp()

    while True:
        try:
            if _health_check_due():
                _preflight_dcp()

            if _DCP_STATUS != "READY":
                time.sleep(min(max(POLL_SECONDS, 1.0), 5.0))
                continue

            if not run_once():
                _write_status("online", detail="idle")
                time.sleep(POLL_SECONDS)
        except urllib.error.HTTPError as exc:
            _write_status("offline", detail=f"http_{exc.code}")
            print("DP_SYNC_HTTP_ERROR", exc.code, flush=True)
            time.sleep(5)
        except Exception as exc:
            _set_dcp_health(
                "TECHNICAL_ERROR",
                detail=type(exc).__name__,
            )
            traceback.print_exc()
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
