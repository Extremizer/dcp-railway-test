#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OEMixiBOT -> Extremizer pricing CRM HTTPS adapter.

Intended for the isolated local OEMixiBOT runtime.
One call = one completed dealer OEM request.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://dcp-railway-test-production.up.railway.app"
PATH = "/internal/pricing-analytics/oemixibot/request"


def _token() -> str:
    token = os.getenv("OEMIXIBOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("OEMIXIBOT_TOKEN is not configured")
    return token


def _key() -> bytes:
    return hashlib.sha256(
        b"extremizer-pricing-analytics-v1\0" + _token().encode("utf-8")
    ).digest()


def _headers(timestamp: int) -> dict[str, str]:
    message = f"v1\n{PATH}\n{timestamp}".encode("utf-8")
    signature = hmac.new(_key(), message, hashlib.sha256).hexdigest()
    return {
        "Content-Type": "application/json",
        "X-Pricing-Analytics-Ts": str(timestamp),
        "X-Pricing-Analytics-Sig": signature,
    }


def send_oem_request(
    *,
    telegram_user_id: int,
    oem: str,
    username: str | None = None,
    first_name: str | None = None,
    last_name: str | None = None,
    manufacturer: str | None = None,
    result_status: str | None = None,
    price_status: str | None = None,
    display_price_amount: int | float | None = None,
    display_price_currency: str | None = None,
    base_url: str | None = None,
    timeout: float = 8.0,
) -> bool:
    """Send one completed OEM request to the shared Railway CRM.

    Failure is intentionally non-blocking for the dealer bot.
    """
    payload: dict[str, Any] = {
        "telegram_user_id": int(telegram_user_id),
        "username": username,
        "first_name": first_name,
        "last_name": last_name,
        "oem": str(oem),
        "manufacturer": manufacturer,
        "result_status": result_status,
        "price_status": price_status,
        "display_price_amount": display_price_amount,
        "display_price_currency": display_price_currency,
    }
    body = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    url = (base_url or os.getenv("EXTREMIZER_ANALYTICS_BASE_URL") or DEFAULT_BASE_URL).rstrip("/") + PATH
    ts = int(time.time())
    request = urllib.request.Request(
        url,
        data=body,
        headers=_headers(ts),
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            return 200 <= int(response.status) < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return False


async def send_oem_request_async(**kwargs: Any) -> bool:
    return await asyncio.to_thread(send_oem_request, **kwargs)
