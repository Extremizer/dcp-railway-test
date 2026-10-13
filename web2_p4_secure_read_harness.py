#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated WEB2 P4 protected-GET proof.

TEST HARNESS ONLY. Not imported by production web_app.py and does not register
public runtime routes.
"""
from __future__ import annotations

from collections.abc import Iterable

from fastapi import FastAPI, HTTPException, Query, Request

import supplier_admin_auth
from internal_order_read_v01 import (
    AdminReadContext,
    read_order,
    read_warehouse_history,
)
from web2_finance_read_model_adapter import (
    ReadModelContractError,
    adapt_warehouse_history,
    to_fixture_dict,
)


def build_test_app(
    db_file: str,
    *,
    allowed_admin_warehouse_ids: Iterable[int],
) -> FastAPI:
    allowed = frozenset(int(x) for x in allowed_admin_warehouse_ids)
    app = FastAPI(
        title="WEB2 P4 Secure Read Proof",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    def require_admin(request: Request) -> None:
        # Reuse the existing signed WEB ADMIN session. Browser role/identity
        # headers or query fields are deliberately ignored.
        try:
            supplier_admin_auth.require_web_admin_access(
                session_cookie=request.cookies.get(
                    supplier_admin_auth.WEB_ADMIN_COOKIE
                )
            )
        except PermissionError:
            raise HTTPException(status_code=401, detail="unauthorized")

    def require_exact_admin_scope(warehouse_id: int) -> int:
        warehouse_id = int(warehouse_id)
        if warehouse_id not in allowed:
            raise HTTPException(status_code=403, detail="scope denied")
        return warehouse_id

    @app.get("/_test/web2/admin/warehouses/{warehouse_id}/history")
    def admin_history(
        request: Request,
        warehouse_id: int,
        search: str | None = Query(default=None, max_length=128),
        limit: int = Query(default=50, ge=1, le=100),
        offset: int = Query(default=0, ge=0, le=100000),
    ):
        require_admin(request)
        warehouse_id = require_exact_admin_scope(warehouse_id)
        context = AdminReadContext(
            actual_actor="web_admin_session",
            authorized=True,
        )
        try:
            history = read_warehouse_history(
                db_file,
                warehouse_id=warehouse_id,
                context=context,
                search=search,
                limit=limit,
                offset=offset,
            )
            strict_orders = {}
            for row in history["rows"]:
                if row.get("internal_payment_snapshot") is True:
                    order_id = row["extremizer_order_number"]
                    strict_orders[order_id] = read_order(
                        db_file,
                        warehouse_id=warehouse_id,
                        order_id=order_id,
                        context=context,
                    )
            model = adapt_warehouse_history(
                history,
                strict_orders_by_id=strict_orders,
                # No authoritative scoped Client Directory exists yet.
                client_display_by_id=None,
            )
        except (PermissionError, ReadModelContractError):
            # Never leak internal contract/scope details to the caller.
            raise HTTPException(status_code=403, detail="read denied")
        except (KeyError, ValueError):
            raise HTTPException(status_code=409, detail="read unavailable")

        payload = to_fixture_dict(model)
        return {
            "contractVersion": history["contract_version"],
            "scope": {
                "warehouseId": warehouse_id,
                "viewerType": "admin",
            },
            "page": {
                "limit": limit,
                "offset": offset,
                # P3 does not expose authoritative matched_count.
                "matchedCount": None,
            },
            **payload,
        }

    @app.get("/_test/web2/sklad/warehouses/{warehouse_id}/history")
    def sklad_history(request: Request, warehouse_id: int):
        # Real warehouse employee authentication/membership is not established.
        # Do not reinterpret ADMIN cookie or browser-supplied role as SKLAD auth.
        raise HTTPException(status_code=403, detail="warehouse auth unavailable")

    return app


__all__ = ["build_test_app"]
