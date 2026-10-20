"""Opt-in cloud owner endpoints backed by the EXISTING DCP broker and cache.

Only explicitly mounted by dp_cloud_broker_runtime; the current WEB1 app
and Telegram bot runtime are unchanged. The cloud service uses a dedicated
DP_SYNC_TOKEN key, never the Telegram token. NO admission control endpoint.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

import dp_cloud_producer_ownership as owner
import dp_live_admission as admission
import dp_live_bridge as broker
import dp_result_completion as completion
import dp_sync_protocol as protocol


def signing_key(scoped_token: str) -> bytes:
    if not isinstance(scoped_token, str) or len(scoped_token.strip()) < 32:
        raise ValueError("dedicated_dp_signing_token_required")
    return hashlib.sha256(b"extremizer-dp-sync-v1\0" + scoped_token.encode()).digest()


def build_cloud_router(db_file: str | Path, *, key: bytes) -> APIRouter:
    """Pass immutable DB target and independently provisioned scoped key."""
    if not isinstance(key, bytes) or len(key) != 32:
        raise ValueError("signed_v2_key_required")
    router = APIRouter()
    db_file = Path(db_file)

    async def authenticated(request: Request, path: str, method: str,
                            ts: str | None, sig: str | None,
                            version: str | None, producer: str | None) -> bytes:
        if not owner.enabled():
            raise HTTPException(503, detail={"code": "cloud_owner_disabled"})
        if version != "2":
            raise HTTPException(401, detail={"code": "signed_v2_required"})
        body = await request.body()
        try:
            protocol.verify(key, method, path, ts, producer, body, sig)
        except protocol.ProtocolError as exc:
            raise HTTPException(401, detail={"code": str(exc)}) from None
        return body

    def handle_owner(operation: str, producer_id: str | None, body: bytes):
        if operation not in {"acquire", "renew", "release"}:
            raise HTTPException(404, detail={"code": "operation_not_found"})
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, detail={"code": "invalid_owner_payload"}) from None
        if not isinstance(payload, dict):
            raise HTTPException(400, detail={"code": "invalid_owner_payload"})
        if operation == "acquire":
            if payload:
                raise HTTPException(400, detail={"code": "invalid_owner_payload"})
        elif set(payload) != {"owner_epoch"} or not isinstance(payload["owner_epoch"], str):
            raise HTTPException(400, detail={"code": "invalid_owner_payload"})

        broker.init(db_file)
        with sqlite3.connect(str(db_file), timeout=10) as conn:
            conn.execute("BEGIN IMMEDIATE")
            owner.init(conn)
            if operation in {"acquire", "release"} and admission.is_open(conn):
                raise HTTPException(409, detail={"code": "admission_must_be_closed"})
            if operation == "acquire":
                outcome = owner.acquire(conn, producer_id)
            elif operation == "renew":
                outcome = owner.renew(conn, producer_id, payload["owner_epoch"])
            else:
                outcome = owner.release(conn, producer_id, payload["owner_epoch"])
            conn.commit()
        if outcome["status"] not in {"ACQUIRED", "RENEWED", "RELEASED"}:
            raise HTTPException(409, detail={"code": outcome["status"].lower()})
        return outcome

    @router.post("/internal/dp-cloud/owner/{operation}")
    async def owner_operation(
        operation: str, request: Request,
        x_dp_sync_ts: str | None = Header(default=None),
        x_dp_sync_sig: str | None = Header(default=None),
        x_dp_protocol: str | None = Header(default=None),
        x_dp_producer_id: str | None = Header(default=None),
    ):
        path = f"/internal/dp-cloud/owner/{operation}"
        body = await authenticated(request, path, "POST", x_dp_sync_ts,
                                   x_dp_sync_sig, x_dp_protocol, x_dp_producer_id)
        return handle_owner(operation, x_dp_producer_id, body)

    @router.get("/internal/dp-cloud/next")
    async def next_request(
        request: Request,
        x_dp_sync_ts: str | None = Header(default=None),
        x_dp_sync_sig: str | None = Header(default=None),
        x_dp_protocol: str | None = Header(default=None),
        x_dp_producer_id: str | None = Header(default=None),
        x_dp_owner_epoch: str | None = Header(default=None),
    ):
        await authenticated(request, "/internal/dp-cloud/next", "GET",
                            x_dp_sync_ts, x_dp_sync_sig, x_dp_protocol, x_dp_producer_id)
        if not x_dp_owner_epoch:
            raise HTTPException(409, detail={"code": "owner_epoch_missing"})
        return {"request": broker.claim_next(
            db_file, producer_id=x_dp_producer_id, owner_epoch=x_dp_owner_epoch
        )}

    @router.post("/internal/dp-cloud/result")
    async def complete_request(
        request: Request,
        x_dp_sync_ts: str | None = Header(default=None),
        x_dp_sync_sig: str | None = Header(default=None),
        x_dp_protocol: str | None = Header(default=None),
        x_dp_producer_id: str | None = Header(default=None),
    ):
        body = await authenticated(request, "/internal/dp-cloud/result", "POST",
                                   x_dp_sync_ts, x_dp_sync_sig, x_dp_protocol, x_dp_producer_id)
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            raise HTTPException(400, detail={"code": "invalid_result_payload"}) from None
        if not isinstance(payload, dict) or payload.get("producer_id") != x_dp_producer_id:
            raise HTTPException(409, detail={"code": "claim_fence_mismatch"})
        try:
            return completion.complete_result(db_file, payload)
        except completion.CompletionError as exc:
            raise HTTPException(exc.status_code, detail={"code": exc.code}) from None
        except sqlite3.Error:
            raise HTTPException(500, detail={"code": "atomic_result_transaction_failed"}) from None

    return router
