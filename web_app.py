#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""WEB1 HTTP/API layer for the unified Extremizer Pro database."""

from __future__ import annotations

import os
import asyncio
import sqlite3
import threading
import json
import urllib.parse
import urllib.request
import hashlib
import hmac
import html
import secrets
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, HTTPException, Header, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import extremizer_bot as core
import warehouse_stock_service
import warehouse_store
import stock_engine
import web_handoff
import dp_live_bridge
import dp_live_health
import pricing_analytics
import supplier_runtime
import supplier_api
import supplier_web_admin
import supplier_channels
import supplier_channel_web
import supplier_admin_auth
import admin_order_service
import admin_order_apply_service
import admin_order_web
import oem_import_maintenance
try:
    import oem_reference_service
except ImportError:
    oem_reference_service = None

BASE_DIR = Path(__file__).resolve().parent
WEB_DIR = BASE_DIR / "web"
BOT_USERNAME = (
    os.getenv("EXTREMIZER_TELEGRAM_BOT_USERNAME", "Extremizer_bot")
    .strip()
    .lstrip("@")
)
_STOCK_REFRESH_LOCK = threading.Lock()
_STOCK_REFRESH_SLOTS = threading.BoundedSemaphore(2)
_STOCK_REFRESH_INFLIGHT: set[str] = set()
_STOCK_REFRESH_MAX_PENDING = 8

app = FastAPI(
    title="Extremizer Pro WEB",
    version="WEB1",
    docs_url="/api/docs",
    redoc_url=None,
)
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

# Supplier Orders uses the exact same persistent orders DB as Telegram.
supplier_order_service = supplier_runtime.get_supplier_order_service(core.ORDERS_DB_FILE)
app.include_router(supplier_api.build_supplier_router(supplier_order_service))
app.include_router(oem_import_maintenance.router)


def _safe_admin_next(value: str | None) -> str:
    value = str(value or "").strip()
    if not value.startswith("/admin") or value.startswith("//"):
        return "/admin/supplier-orders"
    return value


def _admin_page_login_redirect(request: Request):
    if supplier_admin_auth.is_web_admin_authorized(
        session_cookie=request.cookies.get(supplier_admin_auth.WEB_ADMIN_COOKIE),
    ):
        return None
    next_value = request.url.path
    if request.url.query:
        next_value += "?" + request.url.query
    return RedirectResponse(
        "/admin/login?next=" + urllib.parse.quote(next_value, safe=""),
        status_code=303,
    )


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(next: str = "/admin/supplier-orders"):
    target = _safe_admin_next(next)
    return HTMLResponse(
        """<!doctype html><html lang="ru"><head><meta charset="utf-8">"""
        """<meta name="viewport" content="width=device-width,initial-scale=1">"""
        """<title>Extremizer Pro — WEB ADMIN</title>"""
        """<style>body{font-family:Arial,sans-serif;background:#f4f6f8;margin:0}"""
        """.box{max-width:420px;margin:12vh auto;background:#fff;padding:28px;"""
        """border-radius:14px;box-shadow:0 8px 30px #0001}h1{font-size:22px}"""
        """input,button{box-sizing:border-box;width:100%;padding:12px;margin-top:10px;"""
        """border-radius:9px;border:1px solid #ccd2d8}button{cursor:pointer;font-weight:700}"""
        """.note{font-size:13px;color:#667085;margin-top:12px}</style></head><body>"""
        """<div class="box"><h1>🔐 WEB ADMIN</h1>"""
        """<form method="post" action="/admin/login">"""
        f"""<input type="hidden" name="next" value="{html.escape(target, quote=True)}">"""
        """<input type="password" name="token" autocomplete="current-password" """
        """placeholder="ADMIN token" required autofocus>"""
        """<button type="submit">Войти</button></form>"""
        """<div class="note">Токен проверяется на сервере и не сохраняется в cookie.</div>"""
        """</div></body></html>"""
    )


@app.post("/admin/login")
def admin_login_submit(
    token: str = Form(...),
    next: str = Form(default="/admin/supplier-orders"),
):
    try:
        supplier_admin_auth.require_web_admin_token(token)
    except PermissionError:
        raise HTTPException(status_code=401, detail="Неверный ADMIN token")
    response = RedirectResponse(_safe_admin_next(next), status_code=303)
    response.set_cookie(
        supplier_admin_auth.WEB_ADMIN_COOKIE,
        supplier_admin_auth.issue_web_admin_session(),
        max_age=supplier_admin_auth.WEB_ADMIN_SESSION_MAX_AGE,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )
    return response


@app.post("/admin/logout")
def admin_logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(
        supplier_admin_auth.WEB_ADMIN_COOKIE,
        path="/",
        secure=True,
        httponly=True,
        samesite="strict",
    )
    return response


@app.get("/admin", response_class=HTMLResponse)
def admin_order_queue(
    request: Request,
    status: str | None = None,
    search: str = "",
):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect
    allowed_statuses = {"new", "confirmed", "executing", "completed", "cancelled"}
    selected_status = str(status or "").strip().lower()
    if selected_status and selected_status not in allowed_statuses:
        raise HTTPException(status_code=400, detail="invalid order status filter")
    orders = admin_order_service.list_orders(
        core.ORDERS_DB_FILE,
        status=selected_status or None,
        search=search,
        limit=200,
    )
    return admin_order_web.render_order_queue(
        orders,
        filter_name=selected_status or "all",
        search=search,
    )


@app.get("/admin/orders/{order_id}", response_class=HTMLResponse)
def admin_order_card(request: Request, order_id: str):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect
    try:
        snapshot = admin_order_service.get_order(order_id, core.ORDERS_DB_FILE)
    except admin_order_service.OrderNotFound:
        raise HTTPException(status_code=404, detail="client order not found")
    return admin_order_web.render_order_card(snapshot)


@app.get("/admin/orders/{order_id}/prepare-dry-run", response_class=HTMLResponse)
def admin_order_prepare_dry_run(request: Request, order_id: str):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect
    try:
        snapshot = admin_order_service.get_order(order_id, core.ORDERS_DB_FILE)
        dry_run = admin_order_service.prepare_order_dry_run(order_id, core.ORDERS_DB_FILE)
    except admin_order_service.OrderNotFound:
        raise HTTPException(status_code=404, detail="client order not found")
    return admin_order_web.render_order_card(snapshot, dry_run=dry_run)

@app.post("/admin/orders/{order_id}/prepare-apply", response_class=HTMLResponse)
def admin_order_prepare_apply(request: Request, order_id: str):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect

    result = admin_order_apply_service.prepare_order_apply(
        order_id,
        core.ORDERS_DB_FILE,
    )
    if result.get("reason") == "order_not_found":
        raise HTTPException(status_code=404, detail="client order not found")

    try:
        snapshot = admin_order_service.get_order(order_id, core.ORDERS_DB_FILE)
        dry_run = admin_order_service.prepare_order_dry_run(
            order_id,
            core.ORDERS_DB_FILE,
        )
    except admin_order_service.OrderNotFound:
        raise HTTPException(status_code=404, detail="client order not found")

    return admin_order_web.render_order_card(
        snapshot,
        dry_run=dry_run,
        apply_result=result,
    )


@app.get("/admin/supplier-orders", response_class=HTMLResponse)
def supplier_admin_queue(request: Request, filter: str = "all"):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect
    if filter not in {"all","need_send","working","shipped","delivered"}: filter="all"
    return supplier_web_admin.render_queue(supplier_order_service, filter)

@app.get("/admin/supplier-channels", response_class=HTMLResponse)
def supplier_channels_page(request: Request):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect
    return supplier_channel_web.render(supplier_order_service, supplier_channels)

@app.post("/api/admin/supplier-channels/{warehouse_id}")
def supplier_channel_save(
    request: Request,
    warehouse_id: int,
    body: supplier_api.SupplierChannelIn,
    x_extremizer_admin_token: str | None = Header(default=None),
):
    try:
        supplier_admin_auth.require_web_admin_access(
            header_token=x_extremizer_admin_token,
            session_cookie=request.cookies.get(supplier_admin_auth.WEB_ADMIN_COOKIE),
        )
    except PermissionError as exc:
        raise HTTPException(401, str(exc))
    try:
        return supplier_channels.save(
            core.ORDERS_DB_FILE, warehouse_id, body.channel, body.recipient, body.custom_name
        )
    except ValueError as exc:
        raise HTTPException(409, str(exc))

@app.get("/admin/supplier-orders/{supplier_order_id}", response_class=HTMLResponse)
def supplier_admin_card(request: Request, supplier_order_id: int):
    redirect = _admin_page_login_redirect(request)
    if redirect:
        return redirect
    try: order=supplier_order_service.get(supplier_order_id)
    except KeyError: raise HTTPException(404,"supplier order not found")
    return supplier_web_admin.render_card(order)


class HandoffItem(BaseModel):
    manufacturer: str
    oem: str
    requested_oem: str | None = None
    offer_source: str = "usa"
    warehouse_id: int | None = None
    qty: int = Field(default=1, ge=1, le=99)


class HandoffRequest(BaseModel):
    items: list[HandoffItem] = Field(min_length=1, max_length=20)


class VKMaintenanceRequest(BaseModel):
    access_token: str
    method: str
    params: dict[str, Any] = {}


class VKAuthExchangeRequest(BaseModel):
    code: str
    device_id: str
    state: str
    code_verifier: str


class VKBackupRequest(BaseModel):
    access_token: str


class DPSyncLookupRequest(BaseModel):
    manufacturer: str
    oem: str


class DPSyncResultRequest(BaseModel):
    request_id: str
    status: str
    dealer_price_usd: float | None = None
    source: str | None = None
    current_oem: str | None = None
    name: str | None = None
    error_code: str | None = None
    manufacturer: str | None = None


class DPSyncHealthRequest(BaseModel):
    status: str
    detail: str | None = None
    source: str | None = "local_agent"
    checked_at: str | None = None


class OEMixiAnalyticsRequest(BaseModel):
    telegram_user_id: int
    username: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    oem: str
    manufacturer: str | None = None
    result_status: str | None = None
    price_status: str | None = None
    display_price_amount: float | None = Field(default=None, ge=0)
    display_price_currency: str | None = None


def _oemixibot_token() -> str:
    token = os.getenv("OEMIXIBOT_TOKEN", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail={"code": "oemixibot_unconfigured"})
    return token


def _dp_sync_key() -> bytes:
    token = _oemixibot_token()
    return hashlib.sha256(
        b"extremizer-dp-sync-v1\0" + token.encode("utf-8")
    ).digest()


def _pricing_analytics_key() -> bytes:
    token = _oemixibot_token()
    return hashlib.sha256(
        b"extremizer-pricing-analytics-v1\0" + token.encode("utf-8")
    ).digest()


def _oemixibot_identity_key() -> bytes:
    token = _oemixibot_token()
    return hashlib.sha256(
        b"extremizer-oemixibot-identity-v1\0" + token.encode("utf-8")
    ).digest()


def _require_dp_sync_signature(
    path: str,
    timestamp: str | None,
    signature: str | None,
) -> None:
    try:
        ts = int(str(timestamp or "").strip())
    except ValueError:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    if abs(int(time.time()) - ts) > 60:
        raise HTTPException(status_code=401, detail={"code": "stale_signature"})
    message = f"v1\n{path}\n{ts}".encode("utf-8")
    expected = hmac.new(_dp_sync_key(), message, hashlib.sha256).hexdigest()
    supplied = str(signature or "").strip().lower()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})



def _require_pricing_analytics_signature(
    path: str,
    timestamp: str | None,
    signature: str | None,
) -> None:
    try:
        ts = int(str(timestamp or "").strip())
    except ValueError:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    if abs(int(time.time()) - ts) > 60:
        raise HTTPException(status_code=401, detail={"code": "stale_signature"})
    message = f"v1\n{path}\n{ts}".encode("utf-8")
    expected = hmac.new(
        _pricing_analytics_key(),
        message,
        hashlib.sha256,
    ).hexdigest()
    supplied = str(signature or "").strip().lower()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})


def _require_oemixibot_identity_signature(
    path: str,
    timestamp: str | None,
    signature: str | None,
) -> None:
    try:
        ts = int(str(timestamp or "").strip())
    except ValueError:
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})
    if abs(int(time.time()) - ts) > 60:
        raise HTTPException(status_code=401, detail={"code": "stale_signature"})
    message = f"v1\n{path}\n{ts}".encode("utf-8")
    expected = hmac.new(
        _oemixibot_identity_key(),
        message,
        hashlib.sha256,
    ).hexdigest()
    supplied = str(signature or "").strip().lower()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail={"code": "unauthorized"})




def _init_web1() -> None:
    core.init_orders_db()
    warehouse_store.init_warehouse_db(core.ORDERS_DB_FILE)
    web_handoff.init_web_handoff_db(core.ORDERS_DB_FILE)
    dp_live_bridge.init(core.ORDERS_DB_FILE)
    dp_live_health.init(core.ORDERS_DB_FILE)
    pricing_analytics.init_analytics(core.ORDERS_DB_FILE)


@app.on_event("startup")
def startup() -> None:
    _init_web1()


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "project": "Extremizer Pro",
        "layer": "WEB",
        "stage": "WEB1",
    }


@app.get("/internal/oemixibot/identity")
def oemixibot_identity_lookup(
    oem: str,
    x_oemixibot_identity_ts: str | None = Header(default=None),
    x_oemixibot_identity_sig: str | None = Header(default=None),
) -> dict[str, Any]:
    path = "/internal/oemixibot/identity"
    _require_oemixibot_identity_signature(
        path,
        x_oemixibot_identity_ts,
        x_oemixibot_identity_sig,
    )

    normalized = core.finder.normalize_oem(str(oem or "").strip())
    if not normalized:
        raise HTTPException(status_code=400, detail={"code": "invalid_oem"})

    identity = core.resolve_oem_identity(normalized)
    candidates = set()
    for value in (identity.get("manufacturer_candidates") or []):
        canonical = core.finder.manufacturer_alias(str(value or "").strip())
        if canonical:
            candidates.add(canonical)
    if identity.get("manufacturer"):
        canonical = core.finder.manufacturer_alias(
            str(identity.get("manufacturer") or "").strip()
        )
        if canonical:
            candidates.add(canonical)

    # Read additional trusted production sources directly.  The endpoint is
    # deliberately conservative: exactly one unique canonical manufacturer
    # across all confirmed sources is required.
    core.init_orders_db()
    with sqlite3.connect(core.ORDERS_DB_FILE) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }

        if "probnik_oem_identity" in tables:
            for row in conn.execute(
                """SELECT DISTINCT manufacturer
                     FROM probnik_oem_identity
                    WHERE (oem=? OR current_oem=?)
                      AND verified=1
                      AND manufacturer IS NOT NULL
                      AND TRIM(manufacturer)<>''""",
                (normalized, normalized),
            ):
                canonical = core.finder.manufacturer_alias(str(row[0] or "").strip())
                if canonical:
                    candidates.add(canonical)

        if "dealer_price_cache" in tables:
            for row in conn.execute(
                """SELECT DISTINCT manufacturer
                     FROM dealer_price_cache
                    WHERE oem=?
                      AND dealer_price_usd>0
                      AND manufacturer IS NOT NULL
                      AND TRIM(manufacturer)<>''""",
                (normalized,),
            ):
                canonical = core.finder.manufacturer_alias(str(row[0] or "").strip())
                if canonical:
                    candidates.add(canonical)

        if "oem_catalog_cache" in tables:
            for row in conn.execute(
                """SELECT DISTINCT manufacturer
                     FROM oem_catalog_cache
                    WHERE current_oem=?
                      AND msrp_verified=1
                      AND manufacturer IS NOT NULL
                      AND TRIM(manufacturer)<>''""",
                (normalized,),
            ):
                canonical = core.finder.manufacturer_alias(str(row[0] or "").strip())
                if canonical:
                    candidates.add(canonical)

        if "oem_catalog_aliases" in tables:
            for row in conn.execute(
                """SELECT DISTINCT a.manufacturer
                     FROM oem_catalog_aliases AS a
                     JOIN oem_catalog_cache AS c
                       ON c.manufacturer=a.manufacturer
                      AND c.current_oem=a.current_oem
                    WHERE a.alias_oem=?
                      AND c.msrp_verified=1
                      AND a.manufacturer IS NOT NULL
                      AND TRIM(a.manufacturer)<>''""",
                (normalized,),
            ):
                canonical = core.finder.manufacturer_alias(str(row[0] or "").strip())
                if canonical:
                    candidates.add(canonical)

    candidates = sorted(candidates)
    if len(candidates) == 1:
        return {
            "ok": True,
            "status": "FOUND",
            "oem": normalized,
            "manufacturer": candidates[0],
            "item_type": identity.get("item_type"),
            "source": "shared_production_identity",
        }

    return {
        "ok": True,
        "status": "UNRESOLVED",
        "oem": normalized,
        "manufacturer": None,
        "manufacturer_candidates": candidates,
    }


@app.post("/internal/pricing-analytics/oemixibot/request")
def oemixibot_pricing_analytics_request(
    payload: OEMixiAnalyticsRequest,
    x_pricing_analytics_ts: str | None = Header(default=None),
    x_pricing_analytics_sig: str | None = Header(default=None),
) -> dict[str, Any]:
    path = "/internal/pricing-analytics/oemixibot/request"
    _require_pricing_analytics_signature(
        path,
        x_pricing_analytics_ts,
        x_pricing_analytics_sig,
    )

    oem = core.finder.normalize_oem(str(payload.oem or "").strip())
    if not oem:
        raise HTTPException(status_code=400, detail={"code": "invalid_oem"})

    manufacturer = None
    if payload.manufacturer:
        manufacturer = core.finder.manufacturer_alias(
            str(payload.manufacturer).strip()
        ) or str(payload.manufacturer).strip()

    request_id = pricing_analytics.begin_request(
        core.ORDERS_DB_FILE,
        source_bot="oemixibot",
        source_kind="private",
        oem=oem,
        manufacturer=manufacturer,
        telegram_user_id=int(payload.telegram_user_id),
        username=payload.username,
        first_name=payload.first_name,
        last_name=payload.last_name,
        source_chat_id=int(payload.telegram_user_id),
    )
    if request_id is None:
        raise HTTPException(status_code=500, detail={"code": "analytics_write_failed"})

    pricing_analytics.complete_request(
        core.ORDERS_DB_FILE,
        request_id,
        manufacturer=manufacturer,
        result_status=payload.result_status,
        price_status=payload.price_status,
        display_price_amount=payload.display_price_amount,
        display_price_currency=payload.display_price_currency,
        stock_rf_status="NOT_APPLICABLE",
        stock_rf=[],
    )
    return {"ok": True, "request_id": request_id}



def _dp_health_alert_text(transition: dict[str, Any]) -> str | None:
    status = str(transition.get("status") or "UNKNOWN").upper()
    previous = str(transition.get("previous_status") or "UNKNOWN").upper()
    detail = str(transition.get("detail") or "").strip()

    if status == "READY":
        if previous not in {
            "CLOUDFLARE", "AUTH_REQUIRED", "BROWSER_DOWN", "TECHNICAL_ERROR",
        }:
            return None
        return (
            "✅ DCP LIVE восстановлен\n"
            "Live-DP снова доступен в Пробнике, OEMixiBOT и Проценках."
        )

    if status == "CLOUDFLARE":
        return (
            "⚠️ DCP требует Cloudflare\n"
            "Live-DP временно недоступен во всех ботах.\n"
            "Открой специальный Chrome DCP и пройди проверку."
        )

    if status == "AUTH_REQUIRED":
        return (
            "⚠️ DCP требует авторизацию\n"
            "Live-DP временно недоступен во всех ботах.\n"
            "Открой специальный Chrome DCP и войди в DCP."
        )

    if status == "BROWSER_DOWN":
        return (
            "⚠️ DCP Chrome недоступен\n"
            "Live-DP временно недоступен во всех ботах.\n"
            "Проверь специальный Chrome с портом 9222."
        )

    if status == "TECHNICAL_ERROR":
        suffix = f"\nДиагностика: {detail[:160]}" if detail else ""
        return (
            "⚠️ Ошибка DCP LIVE\n"
            "Live-DP временно недоступен во всех ботах."
            + suffix
        )

    return None


def _send_dp_health_alert(transition: dict[str, Any]) -> None:
    text = _dp_health_alert_text(transition)
    if not text:
        return
    token = os.getenv("EXTREMIZER_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_MANAGER_CHAT_ID", "").strip()
    if not token or not chat_id:
        return
    data = urllib.parse.urlencode(
        {"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}
    ).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.telegram.org/bot{token}/sendMessage",
        data=data,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except Exception:
        # Health reporting must never fail because Telegram is temporarily down.
        pass


@app.post("/internal/dp-sync/health")
def dp_sync_health(
    payload: DPSyncHealthRequest,
    background_tasks: BackgroundTasks,
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
) -> dict[str, Any]:
    path = "/internal/dp-sync/health"
    _require_dp_sync_signature(path, x_dp_sync_ts, x_dp_sync_sig)

    status = str(payload.status or "").strip().upper()
    if status not in dp_live_health.VALID_STATUSES:
        raise HTTPException(status_code=400, detail={"code": "invalid_health_status"})

    transition = dp_live_health.set_health(
        core.ORDERS_DB_FILE,
        status,
        detail=payload.detail,
        source=payload.source or "local_agent",
        checked_at=payload.checked_at,
    )
    if transition.get("changed"):
        background_tasks.add_task(_send_dp_health_alert, transition)

    return {
        "ok": True,
        "health": dp_live_health.get_health(core.ORDERS_DB_FILE),
        "changed": bool(transition.get("changed")),
    }


@app.get("/internal/dp-sync/next")
def dp_sync_next(
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
) -> dict[str, Any]:
    path = "/internal/dp-sync/next"
    _require_dp_sync_signature(path, x_dp_sync_ts, x_dp_sync_sig)
    return {"request": dp_live_bridge.claim_next(core.ORDERS_DB_FILE)}


@app.post("/internal/dp-sync/result")
def dp_sync_result(
    payload: DPSyncResultRequest,
    x_dp_sync_ts: str | None = Header(default=None),
    x_dp_sync_sig: str | None = Header(default=None),
) -> dict[str, Any]:
    path = "/internal/dp-sync/result"
    _require_dp_sync_signature(path, x_dp_sync_ts, x_dp_sync_sig)

    status = str(payload.status or "").strip().upper() or "TECHNICAL_ERROR"
    allowed = {
        "FOUND", "NOT_FOUND", "NO_PRICE", "CLOUDFLARE",
        "AUTH_REQUIRED", "TECHNICAL_ERROR",
    }
    if status not in allowed:
        raise HTTPException(status_code=400, detail={"code": "invalid_status"})

    with sqlite3.connect(core.ORDERS_DB_FILE) as conn:
        row = conn.execute(
            """SELECT manufacturer,oem
                 FROM dp_live_requests
                WHERE request_id=?""",
            (payload.request_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail={"code": "request_not_found"})

    requested_manufacturer = str(row[0] or "").strip()
    requested_oem = core.finder.normalize_oem(str(row[1] or "").strip())
    if requested_manufacturer == "__AUTO__":
        manufacturer = core.finder.manufacturer_alias(
            str(payload.manufacturer or "").strip()
        )
    else:
        manufacturer = core.finder.manufacturer_alias(requested_manufacturer)
    if not manufacturer or not requested_oem:
        raise HTTPException(status_code=400, detail={"code": "invalid_request_identity"})

    source = str(payload.source or "").strip() or None
    raw_current_oem = str(payload.current_oem or "").strip()
    current_oem = core.finder.normalize_oem(raw_current_oem) if raw_current_oem else None
    name = str(payload.name or "").strip() or None

    if status == "FOUND":
        price = payload.dealer_price_usd
        if not isinstance(price, (int, float)) or float(price) <= 0:
            raise HTTPException(status_code=400, detail={"code": "invalid_dp"})
        verified_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        verified_source = "verified_local_dcp:" + (source or "dcp")
        if not core.upsert_dealer_price_cache(
            manufacturer,
            requested_oem,
            float(price),
            verified_source,
            verified_at,
        ):
            raise HTTPException(status_code=500, detail={"code": "cache_write_failed"})
        if current_oem and current_oem != requested_oem:
            core.upsert_dealer_price_cache(
                manufacturer,
                current_oem,
                float(price),
                verified_source,
                verified_at,
            )
        source = verified_source
        if requested_manufacturer == "__AUTO__":
            # Persist only identity verified by the trusted local DCP agent.
            with sqlite3.connect(core.ORDERS_DB_FILE) as conn:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS probnik_oem_identity (
                           manufacturer TEXT NOT NULL, oem TEXT NOT NULL,
                           current_oem TEXT NOT NULL, name TEXT, verified INTEGER NOT NULL DEFAULT 0,
                           source_kind TEXT, source_ref TEXT, first_seen_at TEXT NOT NULL,
                           last_verified_at TEXT NOT NULL, PRIMARY KEY(manufacturer,oem))"""
                )
                conn.execute(
                    """INSERT INTO probnik_oem_identity(
                           manufacturer,oem,current_oem,name,verified,source_kind,source_ref,
                           first_seen_at,last_verified_at) VALUES(?,?,?,?,1,?,?,?,?)
                       ON CONFLICT(manufacturer,oem) DO UPDATE SET
                           current_oem=excluded.current_oem,name=excluded.name,verified=1,
                           source_kind=excluded.source_kind,source_ref=excluded.source_ref,
                           last_verified_at=excluded.last_verified_at""",
                    (manufacturer, requested_oem, current_oem or requested_oem, name,
                     "verified_local_dcp", source or "dcp", verified_at, verified_at),
                )
                conn.commit()

    ok = dp_live_bridge.finish(
        core.ORDERS_DB_FILE,
        payload.request_id,
        result_status=status,
        dealer_price_usd=(
            float(payload.dealer_price_usd)
            if status == "FOUND" and payload.dealer_price_usd is not None
            else None
        ),
        source=source,
        current_oem=current_oem,
        name=name[:300] if name else None,
        manufacturer=manufacturer if requested_manufacturer == "__AUTO__" else None,
        error_code=(
            str(payload.error_code).strip()[:120]
            if payload.error_code else None
        ),
    )
    if not ok:
        raise HTTPException(status_code=409, detail={"code": "request_already_finished"})
    return {"ok": True, "status": status}


def _weight_for_oem(oem: str) -> dict[str, Any] | None:
    """Return only client-safe OEM-BASA weight fields."""
    if oem_reference_service is not None:
        reference = oem_reference_service.lookup_oem(oem)
        if not reference:
            return None
        actual = reference.get("actual_weight_kg")
        volume = reference.get("volume_weight_kg")
        if not actual and not volume:
            return None
        return {
            "actual_kg": actual,
            "volume_kg": volume,
            "notice": "Данные по весу носят справочный характер.",
        }

    # Development fallback for a same-DB oem_reference fixture.
    with sqlite3.connect(core.ORDERS_DB_FILE) as conn:
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='oem_reference'"
        ).fetchone()
        if not table:
            return None
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(oem_reference)").fetchall()
        }
        actual_col = next(
            (x for x in ("actual_weight_kg", "fact_weight_kg", "factual_weight_kg")
             if x in columns),
            None,
        )
        volume_col = next(
            (x for x in ("volume_weight_kg", "volumetric_weight_kg")
             if x in columns),
            None,
        )
        if "oem" not in columns or not (actual_col or volume_col):
            return None
        selected = [x for x in (actual_col, volume_col) if x]
        row = conn.execute(
            f"SELECT {', '.join(selected)} FROM oem_reference "
            "WHERE oem = ? COLLATE NOCASE LIMIT 1",
            (oem,),
        ).fetchone()
    if not row:
        return None
    values = dict(zip(selected, row))
    actual = float(values[actual_col]) if actual_col and values.get(actual_col) else None
    volume = float(values[volume_col]) if volume_col and values.get(volume_col) else None
    if not actual and not volume:
        return None
    return {
        "actual_kg": actual if actual and actual > 0 else None,
        "volume_kg": volume if volume and volume > 0 else None,
        "notice": "Данные по весу носят справочный характер.",
    }


def _resolve_catalog_result(
    raw_oem: str,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    oem = core.finder.normalize_oem(str(raw_oem or ""))
    if not oem:
        raise HTTPException(status_code=400, detail={"code": "invalid_oem"})

    canonical = None
    if manufacturer:
        canonical = core.finder.manufacturer_alias(manufacturer)
        if not canonical:
            raise HTTPException(
                status_code=400,
                detail={"code": "invalid_manufacturer"},
            )
    else:
        canonical = core.infer_manufacturer_from_verified_cache(oem)

    if not canonical:
        raise HTTPException(
            status_code=404,
            detail={"code": "oem_not_in_verified_cache"},
        )

    result = core.public_msrp_cache_result(canonical, oem)
    if not result or str(result.get("status") or "").upper() != "FOUND":
        raise HTTPException(status_code=404, detail={"code": "oem_not_found"})
    return result


def _stock_rows(oem: str) -> list[dict[str, Any]]:
    return warehouse_stock_service.client_stock_summary(
        oem,
        db_file=core.ORDERS_DB_FILE,
    )


def _stock_refresh_needed(oem: str) -> bool:
    rows = _stock_rows(oem)
    if not rows or any(not row.get("is_fresh") for row in rows):
        return True

    price_capable_adapters = {"orangeatv", "vladextremelife"}
    for row in rows:
        qty = row.get("available_quantity")
        if qty is None or float(qty) <= 0 or row.get("price_rub") is not None:
            continue
        warehouse_id = int(row.get("warehouse_id") or 0)
        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            core.ORDERS_DB_FILE,
        )
        selected = stock_engine.get_available_stock(
            warehouse_id,
            oem,
            core.ORDERS_DB_FILE,
        )
        if (
            str(selected.get("source_type") or "") == "website"
            and str((warehouse or {}).get("adapter_type") or "") in price_capable_adapters
        ):
            return True
    return False


def _stock_refreshing(oem: str) -> bool:
    with _STOCK_REFRESH_LOCK:
        return oem in _STOCK_REFRESH_INFLIGHT


def _run_stock_refresh(oem: str) -> None:
    acquired = False
    try:
        acquired = _STOCK_REFRESH_SLOTS.acquire(timeout=0.1)
        if not acquired:
            return
        warehouse_stock_service.refresh_all_active_warehouses(
            oem,
            db_file=core.ORDERS_DB_FILE,
        )
    finally:
        if acquired:
            _STOCK_REFRESH_SLOTS.release()
        with _STOCK_REFRESH_LOCK:
            _STOCK_REFRESH_INFLIGHT.discard(oem)


def _queue_stock_refresh(background_tasks: BackgroundTasks, oem: str) -> bool:
    with _STOCK_REFRESH_LOCK:
        if oem in _STOCK_REFRESH_INFLIGHT:
            return False
        if len(_STOCK_REFRESH_INFLIGHT) >= _STOCK_REFRESH_MAX_PENDING:
            return False
        _STOCK_REFRESH_INFLIGHT.add(oem)
    background_tasks.add_task(_run_stock_refresh, oem)
    return True


def _warehouse_only_card(raw_oem: str) -> dict[str, Any] | None:
    """Build a client-safe card from fresh priced warehouse stock only."""
    try:
        oem = core.finder.normalize_oem(str(raw_oem or ""))
    except ValueError:
        return None
    if not oem:
        return None

    stock_rows = _stock_rows(oem)
    positive_stock = []
    warehouse_offers = []
    for row in stock_rows:
        qty = row.get("available_quantity")
        if row.get("is_fresh") and qty is not None and float(qty) > 0:
            price_rub = row.get("price_rub")
            positive_stock.append({
                "warehouse_id": int(row.get("warehouse_id") or 0),
                "warehouse": str(row.get("public_name") or ""),
                "quantity": float(qty),
                "price_rub": float(price_rub) if price_rub is not None else None,
            })
            if price_rub is not None and float(price_rub) > 0:
                warehouse_offers.append({
                    "key": f"warehouse:{int(row.get('warehouse_id') or 0)}",
                    "source": "warehouse",
                    "warehouse_id": int(row.get("warehouse_id") or 0),
                    "label": str(row.get("public_name") or ""),
                    "price_rub": float(price_rub),
                    "available_quantity": float(qty),
                    "can_add": True,
                })

    if not warehouse_offers:
        return None

    stock_known = bool(stock_rows) and all(
        row.get("is_fresh") and row.get("available_quantity") is not None
        for row in stock_rows
    )
    return {
        "manufacturer": "",
        "oem": oem,
        "requested_oem": oem,
        "name": None,
        "catalog": None,
        "previous_oems": [],
        "price": {
            "available": False,
            "customer_rub": None,
            "msrp_rub": None,
            "benefit_pct": None,
        },
        "offers": warehouse_offers,
        "stock": {
            "known": stock_known,
            "warehouses": positive_stock,
        },
        "weight": _weight_for_oem(oem),
        "delivery_notice": "Для позиции со склада РФ доставка из США не требуется.",
        "warehouse_only": True,
    }


def build_oem_card(
    raw_oem: str,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    try:
        result = _resolve_catalog_result(raw_oem, manufacturer)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        warehouse_card = _warehouse_only_card(raw_oem)
        if warehouse_card is not None:
            return warehouse_card
        raise
    canonical = str(result.get("manufacturer") or "")
    current_oem = str(result.get("item_sku") or result.get("oem") or "")
    requested_oem = str(result.get("query_oem") or raw_oem or current_oem)

    rate = core.load_usd_rub_rate()
    coefficient = core.load_price_coefficient()
    dp = core.get_dealer_price_cache(canonical, current_oem, mark_used=False)
    customer_rub = None
    if dp and dp.get("fresh"):
        customer_rub = core.customer_rub_price_from_dp(
            dp.get("dealer_price_usd"),
            coefficient=coefficient,
            rate=rate,
        )

    msrp_usd = result.get("price")
    msrp_rub = (
        int(round(float(msrp_usd) * rate))
        if isinstance(msrp_usd, (int, float)) and rate > 0
        else None
    )
    show_msrp = (
        customer_rub is not None
        and msrp_rub is not None
        and msrp_rub > customer_rub
    )
    benefit_pct = (
        round((msrp_rub - customer_rub) / msrp_rub * 100, 1)
        if show_msrp
        else None
    )

    stock_rows = _stock_rows(current_oem)
    positive_stock = []
    warehouse_offers = []
    for row in stock_rows:
        qty = row.get("available_quantity")
        if row.get("is_fresh") and qty is not None and float(qty) > 0:
            price_rub = row.get("price_rub")
            positive_stock.append({
                "warehouse_id": int(row.get("warehouse_id") or 0),
                "warehouse": str(row.get("public_name") or ""),
                "quantity": float(qty),
                "price_rub": float(price_rub) if price_rub is not None else None,
            })
            warehouse_offers.append({
                "key": f"warehouse:{int(row.get('warehouse_id') or 0)}",
                "source": "warehouse",
                "warehouse_id": int(row.get("warehouse_id") or 0),
                "label": str(row.get("public_name") or ""),
                "price_rub": float(price_rub) if price_rub is not None else None,
                "available_quantity": float(qty),
                "can_add": price_rub is not None and float(price_rub) > 0,
            })
    stock_known = bool(stock_rows) and all(
        row.get("is_fresh") and row.get("available_quantity") is not None
        for row in stock_rows
    )

    return {
        "manufacturer": canonical,
        "oem": current_oem,
        "requested_oem": requested_oem,
        "name": result.get("name"),
        "catalog": result.get("catalog"),
        "previous_oems": list(result.get("previous_oems") or []),
        "price": {
            "available": customer_rub is not None,
            "customer_rub": customer_rub,
            "msrp_rub": msrp_rub if show_msrp else None,
            "benefit_pct": benefit_pct,
        },
        "offers": (
            ([{
                "key": "usa",
                "source": "usa",
                "warehouse_id": None,
                "label": "США",
                "price_rub": customer_rub,
                "available_quantity": None,
                "can_add": customer_rub is not None,
            }] if customer_rub is not None else [])
            + warehouse_offers
        ),
        "stock": {
            "known": stock_known,
            "warehouses": positive_stock,
        },
        "weight": _weight_for_oem(current_oem),
        "delivery_notice": (
            "* - в цену не входит стоимость доставки из штатов 🚚"
        ),
    }


@app.get("/api/v1/oem/{oem}")
def get_oem(
    oem: str,
    background_tasks: BackgroundTasks,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    card = build_oem_card(oem, manufacturer)
    current_oem = str(card["oem"])
    if _stock_refresh_needed(current_oem):
        _queue_stock_refresh(background_tasks, current_oem)
    card["stock"]["refreshing"] = _stock_refreshing(current_oem)
    return card


@app.get("/api/v1/oem/{oem}/stock")
def get_oem_stock(
    oem: str,
    background_tasks: BackgroundTasks,
    manufacturer: str | None = None,
) -> dict[str, Any]:
    card = build_oem_card(oem, manufacturer)
    current_oem = str(card["oem"])
    if _stock_refresh_needed(current_oem):
        _queue_stock_refresh(background_tasks, current_oem)
    stock = dict(card["stock"])
    stock["refreshing"] = _stock_refreshing(current_oem)
    stock["offers"] = list(card.get("offers") or [])
    return stock


@app.post("/api/v1/handoff")
def create_telegram_handoff(payload: HandoffRequest) -> dict[str, Any]:
    verified_items = []
    for item in payload.items:
        card = build_oem_card(item.oem, item.manufacturer)
        source = str(item.offer_source or "usa").strip().lower()
        if source not in {"usa", "warehouse"}:
            raise HTTPException(status_code=400, detail={"code": "invalid_offer_source"})

        selected_offer = None
        if source == "usa":
            selected_offer = next(
                (x for x in card.get("offers", []) if x.get("source") == "usa"),
                None,
            )
        else:
            if item.warehouse_id is None:
                raise HTTPException(status_code=400, detail={"code": "warehouse_required"})
            selected_offer = next(
                (
                    x for x in card.get("offers", [])
                    if x.get("source") == "warehouse"
                    and int(x.get("warehouse_id") or 0) == int(item.warehouse_id)
                ),
                None,
            )

        if not selected_offer or not selected_offer.get("can_add"):
            raise HTTPException(status_code=409, detail={"code": "offer_unavailable"})
        available = selected_offer.get("available_quantity")
        if available is not None and float(available) < int(item.qty):
            raise HTTPException(status_code=409, detail={"code": "insufficient_stock"})

        verified_items.append({
            "manufacturer": card["manufacturer"],
            "oem": card["oem"],
            "requested_oem": item.requested_oem or card["requested_oem"],
            "offer_source": source,
            "warehouse_id": selected_offer.get("warehouse_id"),
            "warehouse_public_name": (
                selected_offer.get("label") if source == "warehouse" else None
            ),
            "price_snapshot_rub": selected_offer.get("price_rub"),
            "available_snapshot": available,
            "qty": item.qty,
        })

    token = web_handoff.create_handoff(
        verified_items,
        db_file=core.ORDERS_DB_FILE,
    )
    return {
        "ok": True,
        "telegram_url": f"https://t.me/{BOT_USERNAME}?start=web_{token}",
        "expires_in_minutes": web_handoff.DEFAULT_TTL_MINUTES,
    }



@app.post("/vk-auth/exchange")
def vk_auth_exchange(req: VKAuthExchangeRequest) -> dict[str, Any]:
    payload = {
        "grant_type": "authorization_code",
        "redirect_uri": "https://dcp-railway-test-production.up.railway.app/vk-auth/callback",
        "client_id": "54800337",
        "code_verifier": req.code_verifier,
        "state": req.state,
        "device_id": req.device_id,
    }
    request = urllib.request.Request(
        "https://id.vk.ru/oauth2/auth?" + urllib.parse.urlencode(payload),
        data=urllib.parse.urlencode({"code": req.code}).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VK auth upstream error: {type(exc).__name__}") from exc

    token = (result.get("access_token") or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail=result)
    return {"access_token": token}


def _vk_call(method: str, token: str, params: dict[str, Any]) -> Any:
    payload = dict(params)
    payload["access_token"] = token
    payload["v"] = "5.199"
    request = urllib.request.Request(
        f"https://api.vk.com/method/{method}",
        data=urllib.parse.urlencode(payload).encode("utf-8"),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VK upstream error: {type(exc).__name__}") from exc
    if "error" in result:
        err = result["error"]
        raise HTTPException(status_code=400, detail={"error_code": err.get("error_code"), "error_msg": err.get("error_msg")})
    return result.get("response")


@app.post("/vk-maintenance/backup")
def vk_maintenance_backup(req: VKBackupRequest) -> dict[str, Any]:
    token = (req.access_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing VK access token")

    owner_id = "-57091749"
    posts: list[dict[str, Any]] = []
    offset = 0
    total = None
    while True:
        response = _vk_call("wall.get", token, {"owner_id": owner_id, "count": "100", "offset": str(offset), "filter": "all"})
        items = list((response or {}).get("items") or [])
        if total is None:
            total = int((response or {}).get("count") or 0)
        posts.extend(items)
        offset += len(items)
        if not items or offset >= total:
            break
        time.sleep(0.35)

    backup_id = secrets.token_hex(16)
    created_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "community": "https://vk.ru/extremizer",
        "owner_id": int(owner_id),
        "created_at": created_at,
        "count": len(posts),
        "posts": posts,
    }
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    backup_dir = Path(os.getenv("VK_BACKUP_DIR", "/data/vk_backups"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    path = backup_dir / f"vk_extremizer_wall_backup_{backup_id}.json"
    path.write_bytes(raw)
    return {
        "ok": True,
        "backup_id": backup_id,
        "count": len(posts),
        "bytes": len(raw),
        "sha256": digest,
        "download_url": f"/vk-maintenance/backup/{backup_id}",
    }


@app.get("/vk-maintenance/backup/{backup_id}")
def vk_maintenance_backup_download(backup_id: str):
    if len(backup_id) != 32 or any(ch not in "0123456789abcdef" for ch in backup_id):
        raise HTTPException(status_code=400, detail="Invalid backup id")
    path = Path(os.getenv("VK_BACKUP_DIR", "/data/vk_backups")) / f"vk_extremizer_wall_backup_{backup_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Backup not found")
    return FileResponse(path, media_type="application/json", filename=path.name)


@app.post("/vk-maintenance/api")
def vk_maintenance_api(req: VKMaintenanceRequest) -> dict[str, Any]:
    allowed = {"wall.get", "wall.delete"}
    if req.method not in allowed:
        raise HTTPException(status_code=400, detail="VK method not allowed")

    token = (req.access_token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Missing VK access token")

    payload = dict(req.params or {})
    payload["access_token"] = token
    payload["v"] = "5.199"
    data = urllib.parse.urlencode(payload).encode("utf-8")
    request = urllib.request.Request(
        f"https://api.vk.com/method/{req.method}",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"VK upstream error: {type(exc).__name__}") from exc

    if "error" in result:
        err = result["error"]
        raise HTTPException(
            status_code=400,
            detail={
                "error_code": err.get("error_code"),
                "error_msg": err.get("error_msg"),
            },
        )
    return {"response": result.get("response")}


# Temporary isolated VK wall maintenance UI.
@app.get("/vk-cleanup")
def vk_cleanup_page():
    return FileResponse(
        WEB_DIR / "vk_cleanup.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )


@app.get("/vk-auth/callback")
def vk_auth_callback_page():
    return FileResponse(
        WEB_DIR / "vk_cleanup.html",
        headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"},
    )
