"""Authoritative OEMixiBOT poller cutover fence.

Default owner is WINDOWS. Railway polling must not start until Release Control
explicitly transitions the fence to RAILWAY with evidence that the Windows
poller is stopped.

This module is isolated and disabled until its router is mounted by Release
Control.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
from pathlib import Path
import secrets
import sqlite3
import time
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException

FENCE_PATH = "/internal/oemixibot/poller-fence/v1"
PROTOCOL_VERSION = "oemixi-poller-fence-v1"
MAX_CLOCK_SKEW_SECONDS = 60
OWNER_WINDOWS = "WINDOWS"
OWNER_RAILWAY = "RAILWAY"


def _derive_key(root_secret: str) -> bytes:
    secret = str(root_secret or "").strip()
    if len(secret) < 16:
        raise ValueError("poller_fence_root_secret_required")
    return hashlib.sha256(
        b"extremizer-oemixibot-poller-fence-v1\0" + secret.encode("utf-8")
    ).digest()


def _message(timestamp: str, nonce: str) -> bytes:
    return f"{PROTOCOL_VERSION}\nGET\n{FENCE_PATH}\n{timestamp}\n{nonce}".encode("utf-8")


def sign_fence_request(root_secret: str, *, timestamp: str, nonce: str) -> str:
    return hmac.new(
        _derive_key(root_secret),
        _message(timestamp, nonce),
        hashlib.sha256,
    ).hexdigest()


def init_fence(db_file: Path | str) -> None:
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS oemixibot_poller_fence_v1(
                   id INTEGER PRIMARY KEY CHECK(id=1),
                   active_owner TEXT NOT NULL,
                   generation INTEGER NOT NULL,
                   evidence_sha256 TEXT,
                   actor TEXT NOT NULL,
                   updated_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """INSERT OR IGNORE INTO oemixibot_poller_fence_v1
               (id,active_owner,generation,evidence_sha256,actor,updated_at)
               VALUES(1,?,1,NULL,'system-default',
                      strftime('%Y-%m-%dT%H:%M:%fZ','now'))""",
            (OWNER_WINDOWS,),
        )
        conn.commit()


def read_fence(db_file: Path | str) -> dict[str, Any]:
    init_fence(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """SELECT active_owner,generation,evidence_sha256,actor,updated_at
               FROM oemixibot_poller_fence_v1 WHERE id=1"""
        ).fetchone()
    if not row:
        raise RuntimeError("poller_fence_missing")
    return dict(row)


def transition_to_railway(
    db_file: Path | str,
    *,
    expected_generation: int,
    windows_stop_evidence: str,
    actor: str,
) -> dict[str, Any]:
    evidence = str(windows_stop_evidence or "").strip()
    actor = str(actor or "").strip()
    if len(evidence) < 16:
        raise ValueError("windows_stop_evidence_required")
    if not actor:
        raise ValueError("actor_required")
    if type(expected_generation) is not int or expected_generation < 1:
        raise ValueError("expected_generation_invalid")

    init_fence(db_file)
    with sqlite3.connect(str(db_file), timeout=10) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT active_owner,generation FROM oemixibot_poller_fence_v1 WHERE id=1"
        ).fetchone()
        if not row:
            raise RuntimeError("poller_fence_missing")
        if int(row["generation"]) != expected_generation:
            raise ValueError("poller_fence_generation_mismatch")
        if str(row["active_owner"]) != OWNER_WINDOWS:
            raise ValueError("poller_fence_not_owned_by_windows")
        digest = hashlib.sha256(evidence.encode("utf-8")).hexdigest()
        conn.execute(
            """UPDATE oemixibot_poller_fence_v1
               SET active_owner=?,generation=generation+1,evidence_sha256=?,
                   actor=?,updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')
               WHERE id=1""",
            (OWNER_RAILWAY, digest, actor),
        )
        conn.commit()
    return read_fence(db_file)


def build_fence_router(*, db_file: Path | str, root_secret: str) -> APIRouter:
    key = _derive_key(root_secret)
    db_file = str(db_file)
    router = APIRouter()

    @router.get(FENCE_PATH)
    def get_fence(
        x_oemixi_fence_ts: str | None = Header(default=None),
        x_oemixi_fence_nonce: str | None = Header(default=None),
        x_oemixi_fence_sig: str | None = Header(default=None),
    ):
        try:
            ts = int(str(x_oemixi_fence_ts or "").strip())
        except ValueError:
            raise HTTPException(401, {"code": "unauthorized"})
        if abs(int(time.time()) - ts) > MAX_CLOCK_SKEW_SECONDS:
            raise HTTPException(401, {"code": "stale_signature"})
        nonce = str(x_oemixi_fence_nonce or "").strip()
        supplied = str(x_oemixi_fence_sig or "").strip().lower()
        if len(nonce) < 16:
            raise HTTPException(401, {"code": "unauthorized"})
        expected = hmac.new(
            key,
            _message(str(ts), nonce),
            hashlib.sha256,
        ).hexdigest()
        if not supplied or not secrets.compare_digest(supplied, expected):
            raise HTTPException(401, {"code": "unauthorized"})
        return {"ok": True, **read_fence(db_file)}

    return router


@dataclass(frozen=True)
class PollerFenceClient:
    base_url: str
    root_secret: str
    timeout_seconds: float = 5.0
    opener: Callable[..., Any] = urllib.request.urlopen
    allow_railway_private_http: bool = False

    def __post_init__(self):
        parsed = urlparse(str(self.base_url or ""))
        private_http = (
            parsed.scheme == "http"
            and self.allow_railway_private_http
            and str(parsed.hostname or "").lower().endswith(".railway.internal")
        )
        if parsed.scheme != "https" and not private_http:
            raise ValueError("https_required_outside_railway_private_network")
        if not parsed.netloc:
            raise ValueError("invalid_fence_url")
        _derive_key(self.root_secret)

    @property
    def endpoint(self) -> str:
        return self.base_url.rstrip("/") + FENCE_PATH

    def read(self) -> dict[str, Any]:
        ts = str(int(time.time()))
        nonce = secrets.token_urlsafe(24)
        sig = sign_fence_request(
            self.root_secret,
            timestamp=ts,
            nonce=nonce,
        )
        req = urllib.request.Request(
            self.endpoint,
            method="GET",
            headers={
                "Accept": "application/json",
                "X-OEMixi-Fence-TS": ts,
                "X-OEMixi-Fence-Nonce": nonce,
                "X-OEMixi-Fence-Sig": sig,
            },
        )
        try:
            with self.opener(req, timeout=float(self.timeout_seconds)) as response:
                raw = response.read(8193)
                if len(raw) > 8192:
                    raise RuntimeError("poller_fence_response_too_large")
        except Exception as exc:
            raise RuntimeError("poller_fence_unavailable") from exc
        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            raise RuntimeError("poller_fence_invalid_json") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("poller_fence_invalid_response")
        return payload

    def require_railway_owner(self) -> dict[str, Any]:
        payload = self.read()
        if payload.get("active_owner") != OWNER_RAILWAY:
            raise RuntimeError("poller_fence_blocks_railway")
        if type(payload.get("generation")) is not int or payload["generation"] < 2:
            raise RuntimeError("poller_fence_invalid_generation")
        return payload


__all__ = [
    "FENCE_PATH",
    "OWNER_WINDOWS",
    "OWNER_RAILWAY",
    "PollerFenceClient",
    "build_fence_router",
    "init_fence",
    "read_fence",
    "transition_to_railway",
]
