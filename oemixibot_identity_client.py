#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only OEMixiBOT -> Extremizer shared identity lookup."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any


DEFAULT_BASE_URL = "https://dcp-railway-test-production.up.railway.app"
PATH = "/internal/oemixibot/identity"


def _token() -> str:
    token = os.getenv("OEMIXIBOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("OEMIXIBOT_TOKEN is not configured")
    return token


def _key() -> bytes:
    return hashlib.sha256(
        b"extremizer-oemixibot-identity-v1\0" + _token().encode("utf-8")
    ).digest()


def _headers(timestamp: int) -> dict[str, str]:
    message = f"v1\n{PATH}\n{timestamp}".encode("utf-8")
    signature = hmac.new(_key(), message, hashlib.sha256).hexdigest()
    return {
        "X-OEMixiBOT-Identity-Ts": str(timestamp),
        "X-OEMixiBOT-Identity-Sig": signature,
    }


def lookup_shared_identity(
    oem: str,
    *,
    base_url: str | None = None,
    timeout: float = 4.0,
) -> dict[str, Any] | None:
    normalized = str(oem or "").strip()
    if not normalized:
        return None

    root = (
        base_url
        or os.getenv("EXTREMIZER_ANALYTICS_BASE_URL")
        or DEFAULT_BASE_URL
    ).rstrip("/")
    query = urllib.parse.urlencode({"oem": normalized})
    url = root + PATH + "?" + query
    ts = int(time.time())
    request = urllib.request.Request(url, headers=_headers(ts), method="GET")

    try:
        with urllib.request.urlopen(request, timeout=float(timeout)) as response:
            if not (200 <= int(response.status) < 300):
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except (
        urllib.error.URLError,
        urllib.error.HTTPError,
        TimeoutError,
        OSError,
        ValueError,
        json.JSONDecodeError,
    ):
        return None

    if str(payload.get("status") or "").upper() != "FOUND":
        return None
    manufacturer = str(payload.get("manufacturer") or "").strip()
    if not manufacturer:
        return None
    return payload
