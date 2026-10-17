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
import math
import os
import time
import traceback
import uuid

import dp_sync_protocol
from dp_result_completion import _dealer_source, _identity
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
PRODUCER_ID = "dp-v2-" + uuid.uuid4().hex
JOURNAL_FILE = Path(__file__).with_name("dp_sync_agent_inflight_v2.json")
_PRODUCER_STOP = False
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
    # These bytes are both signed and transmitted; never serialize a second time.
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8") if payload is not None else b""
    if path == "/internal/dp-sync/health":
        headers = _headers(path)  # Stage 2 explicitly retains the v1 health contract.
    else:
        ts = int(time.time())
        headers = {"X-DP-Protocol": "2", "X-DP-Producer-ID": PRODUCER_ID,
                   "X-DP-Sync-Ts": str(ts),
                   "X-DP-Sync-Sig": dp_sync_protocol.sign(_key(), method, path, ts, PRODUCER_ID, body)}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE_URL + path, data=body if payload is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))
    if not isinstance(result, dict):
        raise ValueError("invalid_server_response")
    return result


def _journal(phase, item=None):
    # Persist uncertainty BEFORE claiming or touching DCP. No token, cookie, price
    # or claim secret is retained. Any remaining journal blocks the next boot.
    data = {"protocol_version": 2, "producer_id": PRODUCER_ID, "phase": phase,
            "created_at": _utc_now()}
    if item:
        data.update(request_id=item["request_id"], claim_generation=item["claim_generation"])
    tmp = JOURNAL_FILE.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, JOURNAL_FILE)
    if os.name != "nt":
        fd = os.open(str(JOURNAL_FILE.parent), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _clear_journal():
    JOURNAL_FILE.unlink()


def _claim() -> dict | None:
    response = _request("/internal/dp-sync/next")
    if "error_code" in response or "request" not in response:
        raise ValueError("producer_claim_rejected")
    item = response["request"]
    if item is None:
        return None
    if (not isinstance(item, dict) or item.get("protocol_version") != 2
        or item.get("producer_id") != PRODUCER_ID
        or not isinstance(item.get("claim_token"), str) or not item["claim_token"]
        or type(item.get("claim_generation")) is not int or item["claim_generation"] < 1
        or not isinstance(item.get("request_id"), str) or not item["request_id"]):
        raise ValueError("invalid_claim_fence")
    lease = datetime.fromisoformat(str(item.get("lease_expires_at") or ""))
    if lease.tzinfo is None or lease <= datetime.now(timezone.utc):
        raise ValueError("invalid_claim_lease")
    manufacturer, oem = _identity(item.get("manufacturer"), item.get("oem"))
    if not all(item.get(key) for key in ("item_category","category_source_kind","category_source_ref","category_verified_at")):
        raise ValueError("category_claim_evidence_missing")
    if item["oem"] != oem or item["manufacturer"] != manufacturer:
        raise ValueError("noncanonical_claim_identity")
    return item


def _validated_result(item, result):
    status = str(result.get("status") or "TECHNICAL_ERROR").upper()
    if status == "BROWSER_DOWN":
        return {"status": "TECHNICAL_ERROR", "error_code": "BROWSER_DOWN"}
    if status != "FOUND":
        return {"status": status if status in {"NOT_FOUND", "NO_PRICE", "CLOUDFLARE", "AUTH_REQUIRED", "TECHNICAL_ERROR"} else "TECHNICAL_ERROR",
                "error_code": status}
    try:
        manufacturer, query = _identity(result.get("manufacturer"), result.get("query_oem"))
        _, current = _identity(manufacturer, result.get("current_oem") or query)
        _, sku = _identity(manufacturer, result.get("matched_sku"))
        price = result.get("dealer_price_usd")
        if (query != item["oem"] or item["manufacturer"] != manufacturer
            or sku not in {query, current} or type(price) not in (int, float)
            or not math.isfinite(price) or price <= 0 or result.get("currency", "USD") != "USD"):
            raise ValueError("invalid_producer_identity_or_price")
        if manufacturer == "Polaris":
            from polaris_dealer_cart_completion_contract import SOURCE,verify_polaris_cart_proof
            authority = {k:item.get(k) for k in ("item_category","category_source_kind",
                                                  "category_source_ref","category_verified_at")}
            resolved = str(item.get("resolved_oem") or current)
            if (item.get("item_category")!="Parts" or
                current != resolved or sku != resolved or
                result.get("source")!=SOURCE):
                raise ValueError("polaris_cart_identity_unverified")
            verify_polaris_cart_proof(result,requested_oem=query,resolved_oem=resolved,
                                      category_evidence=authority)
            return {"status":"FOUND","manufacturer":manufacturer,"query_oem":query,
                    "matched_sku":sku,"current_oem":current,"dealer_price_usd":price,
                    "currency":"USD","source":SOURCE,"name":result.get("name"),
                    "dealer_cart_proof":result["dealer_cart_proof"]}
        _dealer_source(result.get("source"), manufacturer, query)
        return {"status": "FOUND", "manufacturer": manufacturer, "query_oem": query,
                "matched_sku": sku, "current_oem": current, "dealer_price_usd": price,
                "currency": "USD", "source": result["source"], "name": result.get("name")}
    except Exception:
        return {"status": "TECHNICAL_ERROR", "error_code": "producer_result_contract_rejected"}


def _finish(item: dict, result: dict) -> dict:
    payload = {**result, "request_id": item["request_id"], "protocol_version": 2,
               "producer_id": PRODUCER_ID, "claim_token": item["claim_token"],
               "claim_generation": item["claim_generation"]}
    response = _request("/internal/dp-sync/result", method="POST", payload=payload)
    if (response.get("ok") is not True or response.get("status") != result["status"]
        or response.get("server_cache_committed") is not (result["status"] == "FOUND")):
        raise ValueError("result_commit_ack_not_proven")
    return response


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

    # WOPS or any other challenged site must not poison shared DCP health.
    dcp_pages = [x for x in tabs if oemixibot_dcp_price._is_dcp_page(x)]
    for tab in dcp_pages:
        tab_url = str(tab.get("url") or "")
        tab_title = str(tab.get("title") or "")
        marker_text = (tab_url + "\n" + tab_title).lower()
        if (
            "challenges.cloudflare.com" in marker_text
            or any(marker in marker_text for marker in _CF_MARKERS)
        ):
            _set_dcp_health("CLOUDFLARE", detail="cloudflare_tab_detected")
            return _DCP_STATUS

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

    if not oemixibot_dcp_price._is_dcp_page({"type": "page", "url": probe.get("href")}):
        _set_dcp_health("TECHNICAL_ERROR", detail="dcp_document_context_changed")
    elif probe.get("challenged"):
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
    try:
        gap = float(os.getenv("DP_SYNC_LOOKUP_GAP_SECONDS", "5"))
    except ValueError:
        gap = 5.0
    if not math.isfinite(gap) or gap < 0:
        gap = 5.0
    for index, manufacturer in enumerate(manufacturers):
        if index:
            time.sleep(gap)
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
    global _PRODUCER_STOP
    if _PRODUCER_STOP or JOURNAL_FILE.exists():
        _PRODUCER_STOP = True
        return False
    try:
        _journal("claim_delivery_uncertain")
        item = _claim()
        if not item:
            _clear_journal()
            return False
        _journal("lookup_inflight", item)
        try:
            raw = oemixibot_dcp_price.get_dealer_price_dict(item["manufacturer"], item["oem"], category_evidence=item)
        except Exception:
            raw = {"status": "TECHNICAL_ERROR"}
        result = _validated_result(item, raw)
        status = result["status"]
        if status in {"CLOUDFLARE", "AUTH_REQUIRED", "TECHNICAL_ERROR"}:
            _set_dcp_health(status, detail=status)
        _journal("result_delivery_uncertain", item)
        _finish(item, result)
        _clear_journal()
        if status in {"CLOUDFLARE", "AUTH_REQUIRED"}:
            _PRODUCER_STOP = True  # Explicit operator recovery; no automatic search.
        _write_status(_agent_status_for_dcp(status), detail=status)
        return True
    except Exception:
        # Includes 401/409, malformed claims, GET/POST timeouts and lost ACK after
        # server COMMIT. Even passive READY cannot clear this boot/disk latch.
        _PRODUCER_STOP = True
        _write_status("offline", detail="producer_v2_reconciliation_required")
        raise


def main() -> int:
    if not _acquire_singleton():
        print("DP_SYNC_AGENT already running; exiting duplicate", flush=True)
        return 23
    if JOURNAL_FILE.exists():
        _write_status("offline", detail="producer_v2_reconciliation_required")
        return 24
    _dealer_token()
    _write_status("online", detail="started")
    print("DP_SYNC_AGENT starting via signed HTTPS", flush=True)

    if STARTUP_GRACE_SECONDS > 0:
        time.sleep(STARTUP_GRACE_SECONDS)
    _preflight_dcp()

    while True:
        if _PRODUCER_STOP:
            return 24
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
            print("DP_SYNC_ERROR", type(exc).__name__, flush=True)
            time.sleep(5)


if __name__ == "__main__":
    raise SystemExit(main())
