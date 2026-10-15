"""Secure OEMixiBOT -> Shared Gateway transport prototype.

Disabled by default. The server router is only constructed/registered when
explicitly requested by the host application. No producer or browser endpoint
is exposed to this consumer.

Authentication reuses the existing OEMixiBOT root secret with a separate
domain-separated HMAC key. Requests sign method/path/timestamp/nonce/body hash.
Replay nonces are stored server-side in the coordination DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
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

import dp_live_bridge
from unified_price_contract_v1 import from_gateway_and_msrp, dealer_legacy_offer


PATH = "/internal/oemixibot/price-gateway/v1"
PROTOCOL_VERSION = "oemixi-price-gateway-v1"
MAX_BODY_BYTES = 2048
MAX_RESPONSE_BYTES = 16384
MAX_CLOCK_SKEW_SECONDS = 60
NONCE_TTL_SECONDS = 180
ALLOWED_GATEWAY_STATUSES = {
    "FOUND", "NO_PRICE", "NOT_FOUND", "INVALID", "CLOUDFLARE",
    "AUTH_REQUIRED", "RATE_LIMITED", "TECHNICAL_ERROR", "TIMEOUT",
    "ADMISSION_CLOSED", "CLOSED",
}


class SecurePriceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    manufacturer: str = Field(min_length=2, max_length=64)
    oem: str = Field(min_length=4, max_length=40)


def derive_consumer_key(root_secret: str) -> bytes:
    secret = str(root_secret or "").strip()
    if len(secret) < 16:
        raise ValueError("oemixibot_gateway_root_secret_required")
    return hashlib.sha256(
        b"extremizer-oemixibot-price-gateway-v1\0" + secret.encode("utf-8")
    ).digest()


def canonical_body(manufacturer: str, oem: str) -> bytes:
    return json.dumps(
        {"manufacturer": manufacturer, "oem": oem},
        separators=(",", ":"),
        sort_keys=True,
        ensure_ascii=False,
    ).encode("utf-8")


def body_sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def signature_message(
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body_hash: str,
) -> bytes:
    return (
        f"{PROTOCOL_VERSION}\n{method.upper()}\n{path}\n{timestamp}\n"
        f"{nonce}\n{body_hash}"
    ).encode("utf-8")


def sign_request(
    root_secret: str,
    *,
    method: str,
    path: str,
    timestamp: str,
    nonce: str,
    body: bytes,
) -> str:
    return hmac.new(
        derive_consumer_key(root_secret),
        signature_message(method, path, timestamp, nonce, body_sha256(body)),
        hashlib.sha256,
    ).hexdigest()


def _normalize_identity(manufacturer: str, oem: str) -> tuple[str, str]:
    return dp_live_bridge._request_key(manufacturer, oem)


def read_verified_msrp_cache(
    db_file: Path | str,
    manufacturer: str,
    oem: str,
    *,
    max_age_hours: float,
) -> dict[str, Any]:
    """Read existing catalog MSRP cache only; never enqueue or browse."""
    manufacturer, oem = _normalize_identity(manufacturer, oem)
    if not manufacturer or not oem:
        return {"gateway_status": "INVALID"}
    if (
        type(max_age_hours) not in (int, float)
        or not math.isfinite(float(max_age_hours))
        or float(max_age_hours) <= 0
    ):
        return {"gateway_status": "INVALID"}

    path = Path(db_file)
    if not path.exists():
        return {"gateway_status": "NO_PRICE"}
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5) as conn:
            conn.row_factory = sqlite3.Row
            tables = {
                str(r[0]) for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            if "oem_catalog_cache" not in tables:
                return {"gateway_status": "NO_PRICE"}

            current_oem = oem
            if "oem_catalog_aliases" in tables:
                alias = conn.execute(
                    """SELECT current_oem FROM oem_catalog_aliases
                       WHERE manufacturer=? AND alias_oem=?""",
                    (manufacturer, oem),
                ).fetchone()
                if alias and alias[0]:
                    current_oem = str(alias[0])

            row = conn.execute(
                """SELECT current_oem,catalog,msrp_usd,msrp_verified,
                          source_kind,source_ref,last_verified_at
                     FROM oem_catalog_cache
                    WHERE manufacturer=? AND current_oem=?""",
                (manufacturer, current_oem),
            ).fetchone()
    except sqlite3.Error:
        return {"gateway_status": "TECHNICAL_ERROR"}

    if not row or int(row["msrp_verified"] or 0) != 1:
        return {"gateway_status": "NO_PRICE"}
    price = row["msrp_usd"]
    if (
        type(price) not in (int, float)
        or isinstance(price, bool)
        or not math.isfinite(float(price))
        or float(price) <= 0
    ):
        return {"gateway_status": "NO_PRICE"}

    source_kind = str(row["source_kind"] or "catalog").strip()
    source_ref = str(row["source_ref"] or "unknown").strip()
    return {
        "gateway_status": "FOUND",
        "manufacturer": manufacturer,
        "current_oem": str(row["current_oem"] or current_oem),
        "item_category": str(row["catalog"] or "").strip() or None,
        "msrp_usd": float(price),
        "source": f"verified_public_msrp:{source_kind}:{source_ref}",
        "last_verified_at": str(row["last_verified_at"] or ""),
    }


def _init_replay_store(db_file: Path | str) -> None:
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS oemixi_gateway_replay_v1 (
                   nonce TEXT PRIMARY KEY,
                   seen_at INTEGER NOT NULL
               )"""
        )
        conn.commit()


def _claim_nonce(db_file: Path | str, nonce: str, now: int) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_-]{16,96}", str(nonce or "")):
        return False
    _init_replay_store(db_file)
    cutoff = now - NONCE_TTL_SECONDS
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM oemixi_gateway_replay_v1 WHERE seen_at < ?",
            (cutoff,),
        )
        try:
            conn.execute(
                "INSERT INTO oemixi_gateway_replay_v1(nonce,seen_at) VALUES(?,?)",
                (nonce, now),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        conn.commit()
        return True


def _sanitize_gateway_result(result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {
            "status": "TECHNICAL_ERROR",
            "error_code": "invalid_gateway_response",
        }
    status = str(result.get("status") or "TECHNICAL_ERROR").upper()
    if status not in ALLOWED_GATEWAY_STATUSES:
        status = "TECHNICAL_ERROR"
    out: dict[str, Any] = {"status": status}
    for key in (
        "manufacturer", "query_oem", "current_oem", "name", "source",
        "last_verified_at", "currency", "error_code", "request_id",
        "cache_committed_at", "msrp_source", "msrp_verified_at",
        "price_contract_version",
    ):
        value = result.get(key)
        if value is not None:
            out[key] = value
    if status == "FOUND":
        price = result.get("dealer_price_usd")
        if (
            type(price) not in (int, float)
            or isinstance(price, bool)
            or not math.isfinite(float(price))
            or float(price) <= 0
        ):
            return {
                "status": "NO_PRICE",
                "error_code": "invalid_gateway_dealer_price",
            }
        out["dealer_price_usd"] = float(price)
        msrp = result.get("msrp_usd")
        if (
            type(msrp) in (int, float)
            and not isinstance(msrp, bool)
            and math.isfinite(float(msrp))
            and float(msrp) > 0
        ):
            out["msrp_usd"] = float(msrp)
        out["server_cache_committed"] = bool(
            result.get("server_cache_committed")
            or result.get("verified_cache_hit")
        )
    return out


def build_router(
    *,
    db_file: Path | str,
    root_secret: str,
    shared_gateway: Callable[..., dict],
    server_max_age_hours: float,
    msrp_max_age_hours: float,
    wait_seconds: float = 5.0,
    msrp_reader: Callable[..., dict] | None = None,
) -> APIRouter:
    """Build the disabled-by-default prototype router.

    Freshness is server configuration. It is intentionally absent from the
    request model so Windows consumers cannot choose production freshness.
    """
    if not callable(shared_gateway):
        raise ValueError("shared_gateway_required")
    if (
        type(server_max_age_hours) not in (int, float)
        or not math.isfinite(float(server_max_age_hours))
        or float(server_max_age_hours) <= 0
    ):
        raise ValueError("explicit_server_freshness_required")
    if (
        type(wait_seconds) not in (int, float)
        or not math.isfinite(float(wait_seconds))
        or not (0 < float(wait_seconds) <= 15)
    ):
        raise ValueError("invalid_gateway_wait_seconds")
    if (
        type(msrp_max_age_hours) not in (int, float)
        or not math.isfinite(float(msrp_max_age_hours))
        or float(msrp_max_age_hours) <= 0
    ):
        raise ValueError("explicit_server_msrp_freshness_required")
    key = derive_consumer_key(root_secret)
    db_file = str(db_file)
    max_age = float(server_max_age_hours)
    msrp_age = float(msrp_max_age_hours)
    wait = float(wait_seconds)
    reader = msrp_reader or (
        lambda manufacturer, oem, *, max_age_hours:
        read_verified_msrp_cache(
            db_file, manufacturer, oem, max_age_hours=max_age_hours
        )
    )
    router = APIRouter()

    @router.post(PATH)
    def price_gateway(
        request: Request,
        payload: SecurePriceRequest,
        x_oemixi_gateway_ts: str | None = Header(default=None),
        x_oemixi_gateway_nonce: str | None = Header(default=None),
        x_oemixi_gateway_sig: str | None = Header(default=None),
    ) -> dict[str, Any]:
        raw_length = str(request.headers.get("content-length") or "").strip()
        if raw_length:
            try:
                if int(raw_length) > MAX_BODY_BYTES:
                    raise HTTPException(413, {"code": "request_too_large"})
            except ValueError:
                raise HTTPException(400, {"code": "invalid_content_length"})
        try:
            ts = int(str(x_oemixi_gateway_ts or "").strip())
        except ValueError:
            raise HTTPException(401, {"code": "unauthorized"})
        now = int(time.time())
        if abs(now - ts) > MAX_CLOCK_SKEW_SECONDS:
            raise HTTPException(401, {"code": "stale_signature"})

        manufacturer, oem = _normalize_identity(
            payload.manufacturer,
            payload.oem,
        )
        if not manufacturer or not oem:
            raise HTTPException(400, {"code": "invalid_identity"})

        body = canonical_body(manufacturer, oem)
        if len(body) > MAX_BODY_BYTES:
            raise HTTPException(413, {"code": "request_too_large"})
        nonce = str(x_oemixi_gateway_nonce or "").strip()
        supplied = str(x_oemixi_gateway_sig or "").strip().lower()
        expected = hmac.new(
            key,
            signature_message(
                "POST", PATH, str(ts), nonce, body_sha256(body)
            ),
            hashlib.sha256,
        ).hexdigest()
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, {"code": "unauthorized"})
        if not _claim_nonce(db_file, nonce, now):
            raise HTTPException(409, {"code": "replay_rejected"})

        # Consumer has no access to producer/claim/finish. The server alone
        # decides cache/live/admission through the existing shared gateway.
        try:
            try:
                dealer = shared_gateway(
                    manufacturer,
                    oem,
                    max_age_hours=max_age,
                    wait_seconds=wait,
                )
            except TypeError:
                dealer = shared_gateway(
                    manufacturer,
                    oem,
                    max_age_hours=max_age,
                )
            if not isinstance(dealer, dict):
                dealer = {
                    "status": "TECHNICAL_ERROR",
                    "error_code": "invalid_gateway_response",
                }
            try:
                msrp = reader(
                    manufacturer,
                    oem,
                    max_age_hours=msrp_age,
                )
            except Exception:
                msrp = {
                    "gateway_status": "TECHNICAL_ERROR",
                    "error_code": "msrp_reader_unavailable",
                }
            if not isinstance(msrp, dict):
                msrp = {
                    "gateway_status": "TECHNICAL_ERROR",
                    "error_code": "invalid_msrp_response",
                }

            contract = from_gateway_and_msrp(
                manufacturer=manufacturer,
                requested_oem=oem,
                item_category=(
                    dealer.get("item_category")
                    or msrp.get("item_category")
                ),
                dealer_result=dealer,
                msrp_result=msrp,
                dealer_max_age_hours=max_age,
                msrp_max_age_hours=msrp_age,
            )
            result = dealer_legacy_offer(contract)

            # Preserve only server-generated compatibility evidence required by
            # existing consumers. Windows never supplies these fields.
            for evidence_key in (
                "server_cache_committed",
                "verified_cache_hit",
                "cache_committed_at",
                "request_id",
                "name",
            ):
                if dealer.get(evidence_key) is not None:
                    result[evidence_key] = dealer.get(evidence_key)
        except Exception:
            result = {
                "status": "TECHNICAL_ERROR",
                "error_code": "shared_gateway_unavailable",
            }
        return _sanitize_gateway_result(result)

    return router


@dataclass(frozen=True)
class HTTPSPriceTransport:
    """Windows-safe synchronous client callable for SharedDealerPriceAdapter."""

    base_url: str
    root_secret: str
    timeout_seconds: float = 7.0
    max_response_bytes: int = MAX_RESPONSE_BYTES
    opener: Callable[..., Any] = urllib.request.urlopen
    allow_test_http: bool = False

    def __post_init__(self):
        parsed = urlparse(str(self.base_url or ""))
        if parsed.scheme != "https" and not self.allow_test_http:
            raise ValueError("https_required")
        if not parsed.netloc:
            raise ValueError("invalid_gateway_url")
        if (
            type(self.timeout_seconds) not in (int, float)
            or not math.isfinite(float(self.timeout_seconds))
            or not (0 < float(self.timeout_seconds) <= 15)
        ):
            raise ValueError("invalid_transport_timeout")
        if (
            type(self.max_response_bytes) is not int
            or not (256 <= self.max_response_bytes <= 65536)
        ):
            raise ValueError("invalid_response_limit")
        derive_consumer_key(self.root_secret)

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + PATH

    def __call__(self, manufacturer: str, oem: str, *, max_age_hours=None) -> dict:
        # max_age_hours is accepted only for SharedDealerPriceAdapter callable
        # compatibility. It is never transmitted or trusted by the server.
        manufacturer, oem = _normalize_identity(manufacturer, oem)
        if not manufacturer or not oem:
            return {"status": "INVALID"}
        body = canonical_body(manufacturer, oem)
        if len(body) > MAX_BODY_BYTES:
            return {"status": "INVALID", "error_code": "request_too_large"}

        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        sig = sign_request(
            self.root_secret,
            method="POST",
            path=PATH,
            timestamp=ts,
            nonce=nonce,
            body=body,
        )
        req = urllib.request.Request(
            self.endpoint,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-OEMixi-Gateway-TS": ts,
                "X-OEMixi-Gateway-Nonce": nonce,
                "X-OEMixi-Gateway-Sig": sig,
            },
        )
        try:
            with self.opener(req, timeout=float(self.timeout_seconds)) as response:
                raw = response.read(self.max_response_bytes + 1)
                if len(raw) > self.max_response_bytes:
                    return {
                        "status": "TECHNICAL_ERROR",
                        "error_code": "response_too_large",
                    }
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                return {
                    "status": "TECHNICAL_ERROR",
                    "error_code": "gateway_auth_failed",
                }
            if exc.code == 429:
                return {
                    "status": "RATE_LIMITED",
                    "error_code": "gateway_rate_limited",
                }
            if 500 <= exc.code <= 599:
                return {
                    "status": "TECHNICAL_ERROR",
                    "error_code": "gateway_5xx",
                }
            return {
                "status": "TECHNICAL_ERROR",
                "error_code": f"gateway_http_{exc.code}",
            }
        except Exception:
            return {
                "status": "TECHNICAL_ERROR",
                "error_code": "gateway_transport_error",
            }

        try:
            decoded = json.loads(raw.decode("utf-8"))
        except Exception:
            return {
                "status": "TECHNICAL_ERROR",
                "error_code": "invalid_gateway_json",
            }
        return _sanitize_gateway_result(decoded)


__all__ = [
    "PATH",
    "PROTOCOL_VERSION",
    "SecurePriceRequest",
    "derive_consumer_key",
    "canonical_body",
    "sign_request",
    "read_verified_msrp_cache",
    "build_router",
    "HTTPSPriceTransport",
]
