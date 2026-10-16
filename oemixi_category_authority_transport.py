"""Read-only signed Category Authority transport for Windows OEMixiBOT -> Railway."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

import dcp_category_authority

PATH = "/internal/oemixi/category-authority/v1"
PROTOCOL_VERSION = "oemixi-category-authority-v1"
MAX_BODY_BYTES = 1024
MAX_RESPONSE_BYTES = 8192
MAX_CLOCK_SKEW_SECONDS = 60

_MANUFACTURERS = {
    "arcticcat": "Arctic Cat",
    "skidoo": "Ski-Doo",
    "canam": "Can-Am",
    "seadoo": "Sea-Doo",
    "cfmoto": "CFMoto",
    "honda": "Honda",
    "yamaha": "Yamaha",
    "kawasaki": "Kawasaki",
    "suzuki": "Suzuki",
    "polaris": "Polaris",
    "ktm": "KTM",
    "indian": "Indian",
}


class CategoryAuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manufacturer: str = Field(min_length=2, max_length=64)
    oem: str = Field(min_length=4, max_length=40)


class CategoryAuthorityTransportError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code or "CATEGORY_AUTHORITY_UNAVAILABLE")
        super().__init__(self.code)


def derive_authority_key(root_secret: str) -> bytes:
    secret = str(root_secret or "").strip()
    if len(secret) < 16:
        raise ValueError("oemixibot_authority_root_secret_required")
    return hashlib.sha256(
        b"extremizer-oemixi-category-authority-v1\0" + secret.encode("utf-8")
    ).digest()


def normalize_identity(manufacturer: str, oem: str) -> tuple[str, str]:
    raw = re.sub(r"[\s_-]", "", str(manufacturer or "")).lower()
    canonical = _MANUFACTURERS.get(raw, "")
    normalized_oem = re.sub(r"\s+", "", str(oem or "")).upper()
    if not canonical or not re.fullmatch(r"[A-Z0-9-]{4,40}", normalized_oem):
        return "", ""
    return canonical, normalized_oem


def canonical_body(manufacturer: str, oem: str) -> bytes:
    return json.dumps(
        {"manufacturer": manufacturer, "oem": oem},
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def _message(timestamp: str, body: bytes) -> bytes:
    return (
        f"{PROTOCOL_VERSION}\nPOST\n{PATH}\n{timestamp}\n"
        f"{hashlib.sha256(body).hexdigest()}"
    ).encode("utf-8")


def sign_request(root_secret: str, *, timestamp: str, body: bytes) -> str:
    return hmac.new(
        derive_authority_key(root_secret),
        _message(str(timestamp), body),
        hashlib.sha256,
    ).hexdigest()


def _read_authority(db_file: Path | str, manufacturer: str, oem: str) -> dict | None:
    path = Path(db_file)
    if not path.exists() or not path.is_file():
        raise RuntimeError("authority_db_unavailable")
    uri = path.resolve().as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5) as conn:
        conn.execute("PRAGMA query_only=ON")
        return dcp_category_authority.resolve(conn, manufacturer, oem)


def build_router(*, db_file: Path | str, root_secret: str) -> APIRouter:
    key = derive_authority_key(root_secret)
    db_file = str(db_file)
    router = APIRouter()

    @router.post(PATH)
    async def category_authority(
        request: Request,
        payload: CategoryAuthorityRequest,
        x_oemixi_authority_ts: str | None = Header(default=None),
        x_oemixi_authority_sig: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_length = str(request.headers.get("content-length") or "").strip()
        if raw_length:
            try:
                if int(raw_length) > MAX_BODY_BYTES:
                    raise HTTPException(413, {"code": "request_too_large"})
            except ValueError:
                raise HTTPException(400, {"code": "invalid_content_length"})

        try:
            ts = int(str(x_oemixi_authority_ts or "").strip())
        except ValueError:
            raise HTTPException(401, {"code": "unauthorized"})
        if abs(int(time.time()) - ts) > MAX_CLOCK_SKEW_SECONDS:
            raise HTTPException(401, {"code": "stale_signature"})

        manufacturer, oem = normalize_identity(payload.manufacturer, payload.oem)
        if not manufacturer or not oem:
            raise HTTPException(400, {"code": "invalid_identity"})

        body = canonical_body(manufacturer, oem)
        supplied = str(x_oemixi_authority_sig or "").strip().lower()
        expected = hmac.new(
            key,
            _message(str(ts), body),
            hashlib.sha256,
        ).hexdigest()
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, {"code": "unauthorized"})

        try:
            result = _read_authority(db_file, manufacturer, oem)
        except Exception:
            raise HTTPException(503, {"code": "authority_db_unavailable"})

        if not result:
            return {
                "status": "CATEGORY_UNVERIFIED",
                "manufacturer": manufacturer,
                "requested_oem": oem,
            }
        return {
            "status": "CATEGORY_VERIFIED",
            "manufacturer": manufacturer,
            "requested_oem": oem,
            "resolved_oem": result["resolved_oem"],
            "item_category": result["item_category"],
            "category_source_kind": result["category_source_kind"],
            "category_source_ref": result["category_source_ref"],
            "category_verified_at": result["category_verified_at"],
        }

    return router


@dataclass(frozen=True)
class HTTPSCategoryAuthorityResolver:
    base_url: str
    root_secret: str
    timeout_seconds: float = 5.0
    max_response_bytes: int = MAX_RESPONSE_BYTES
    opener: Callable[..., Any] = urllib.request.urlopen
    allow_test_http: bool = False

    def __post_init__(self):
        parsed = urlparse(str(self.base_url or ""))
        if parsed.scheme != "https" and not self.allow_test_http:
            raise ValueError("https_required")
        if not parsed.netloc:
            raise ValueError("invalid_authority_url")
        if not (0 < float(self.timeout_seconds) <= 15):
            raise ValueError("invalid_authority_timeout")
        derive_authority_key(self.root_secret)

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + PATH

    def __call__(self, manufacturer: str, oem: str) -> dict | None:
        manufacturer, oem = normalize_identity(manufacturer, oem)
        if not manufacturer or not oem:
            return None
        body = canonical_body(manufacturer, oem)
        ts = str(int(time.time()))
        signature = sign_request(self.root_secret, timestamp=ts, body=body)
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-OEMixi-Authority-TS": ts,
                "X-OEMixi-Authority-Sig": signature,
            },
        )
        try:
            with self.opener(req, timeout=float(self.timeout_seconds)) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_RESPONSE_TOO_LARGE")
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_AUTH_FAILED")
            if exc.code == 503:
                raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_UNAVAILABLE")
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_HTTP_ERROR")
        except CategoryAuthorityTransportError:
            raise
        except Exception:
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_UNAVAILABLE")

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except Exception:
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        if not isinstance(decoded, dict):
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        status = str(decoded.get("status") or "")
        if status == "CATEGORY_UNVERIFIED":
            return None
        if status != "CATEGORY_VERIFIED":
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        required = (
            "resolved_oem", "item_category", "category_source_kind",
            "category_source_ref", "category_verified_at",
        )
        if any(not isinstance(decoded.get(k), str) or not decoded[k].strip() for k in required):
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        return {
            "resolved_oem": decoded["resolved_oem"],
            "item_category": decoded["item_category"],
            "category_source_kind": decoded["category_source_kind"],
            "category_source_ref": decoded["category_source_ref"],
            "category_verified_at": decoded["category_verified_at"],
        }


__all__ = [
    "PATH",
    "PROTOCOL_VERSION",
    "CategoryAuthorityRequest",
    "CategoryAuthorityTransportError",
    "derive_authority_key",
    "normalize_identity",
    "canonical_body",
    "sign_request",
    "build_router",
    "HTTPSCategoryAuthorityResolver",
]
