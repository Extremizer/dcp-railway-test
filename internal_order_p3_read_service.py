#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal Order P3 scoped read-service contract.

Isolated server-side composition only:
- no public HTTP route;
- no authentication implementation;
- no writes;
- no FinanceEngine/OrderEngine duplication.

Authorization context and Order Core metadata are trusted test inputs until
real server authorization/projection wiring is approved.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import warehouse_history_v14 as warehouse_history


READ_CONTRACT_VERSION = "internal-order-p3-read-v0.1"
READ_WAREHOUSE_HISTORY = "WAREHOUSE_HISTORY_READ"
ROLE_ADMIN = "admin"
ROLE_RF_WAREHOUSE = "rf_warehouse_user"
_ALLOWED_ROLES = {ROLE_ADMIN, ROLE_RF_WAREHOUSE}


def _text(value, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


@dataclass(frozen=True)
class WarehouseReadAuthorizationContext:
    """Trusted-server authorization facts for the isolated P3 contract.

    This object is intentionally NOT authentication. Real runtime code must
    construct equivalent facts only after session, capability, membership,
    resource-scope and Acting-As intersection checks have succeeded.
    """

    actual_actor: str
    effective_subject: str
    effective_role: str
    warehouse_id: int
    permissions: tuple[str, ...]
    authorized: bool
    acting_as: bool = False


@dataclass(frozen=True)
class ScopedOrderMetadata:
    """Authoritative role-scoped Order Core projection required by WEB2."""

    order_id: str
    warehouse_id: int
    client_id: str
    client_display: str
    oems: tuple[str, ...]
    status: str


def _authorize(
    context: WarehouseReadAuthorizationContext,
    requested_warehouse_id: int,
) -> None:
    if not isinstance(context, WarehouseReadAuthorizationContext):
        raise TypeError("auth_context must be WarehouseReadAuthorizationContext")
    _text(context.actual_actor, "actual_actor")
    _text(context.effective_subject, "effective_subject")
    role = _text(context.effective_role, "effective_role")
    if not context.authorized:
        raise PermissionError("WAREHOUSE_HISTORY_NOT_AUTHORIZED")
    if role not in _ALLOWED_ROLES:
        raise PermissionError("WAREHOUSE_HISTORY_ROLE_NOT_ALLOWED")
    if READ_WAREHOUSE_HISTORY not in set(context.permissions):
        raise PermissionError("WAREHOUSE_HISTORY_CAPABILITY_REQUIRED")
    if int(context.warehouse_id) != int(requested_warehouse_id):
        raise PermissionError("WAREHOUSE_HISTORY_SCOPE_MISMATCH")


def _normalize_metadata(
    metadata: Mapping[str, ScopedOrderMetadata],
) -> dict[str, ScopedOrderMetadata]:
    if not isinstance(metadata, Mapping):
        raise TypeError("order_metadata must be a mapping")
    result: dict[str, ScopedOrderMetadata] = {}
    for key, item in metadata.items():
        if not isinstance(item, ScopedOrderMetadata):
            raise TypeError("order_metadata values must be ScopedOrderMetadata")
        key_text = _text(key, "order_metadata key")
        order_id = _text(item.order_id, "metadata.order_id")
        if key_text != order_id:
            raise ValueError("ORDER_METADATA_KEY_MISMATCH")
        if key_text in result:
            raise ValueError("ORDER_METADATA_DUPLICATE")
        result[key_text] = item
    return result


def _all_history_rows(db_file, warehouse_id: int) -> list[dict]:
    """Read every scoped row in bounded chunks without changing global totals."""
    rows: list[dict] = []
    offset = 0
    while True:
        batch = warehouse_history.order_rows(
            db_file,
            warehouse_id,
            search=None,
            limit=500,
            offset=offset,
        )
        rows.extend(batch)
        if len(batch) < 500:
            break
        offset += len(batch)
    return rows


def _enrich_row(
    row: dict,
    metadata: dict[str, ScopedOrderMetadata],
    warehouse_id: int,
) -> dict:
    order_id = _text(row.get("extremizer_order_number"), "extremizer_order_number")
    meta = metadata.get(order_id)
    if meta is None:
        raise ValueError(f"ORDER_METADATA_REQUIRED:{order_id}")
    if _text(meta.order_id, "metadata.order_id") != order_id:
        raise ValueError("ORDER_METADATA_ORDER_MISMATCH")
    if int(meta.warehouse_id) != int(warehouse_id):
        raise PermissionError("ORDER_METADATA_WAREHOUSE_SCOPE_MISMATCH")

    meta_client_id = _text(meta.client_id, "metadata.client_id")
    snapshot_client_id = str(row.get("client_id") or "").strip()
    if snapshot_client_id and snapshot_client_id != meta_client_id:
        raise ValueError("INTERNAL_CLIENT_LINK_MISMATCH")

    enriched = dict(row)
    enriched["client_id"] = snapshot_client_id or meta_client_id
    enriched["client_display"] = _text(meta.client_display, "metadata.client_display")
    enriched["oems"] = tuple(_text(oem, "metadata.oem") for oem in meta.oems)
    enriched["order_status"] = _text(meta.status, "metadata.status")
    return enriched


def _matches(row: dict, search: str | None) -> bool:
    q = str(search or "").strip().lower()
    if not q:
        return True
    values = [
        row.get("date"),
        row.get("extremizer_order_number"),
        row.get("warehouse_order_number"),
        row.get("client_id"),
        row.get("client_display"),
        row.get("order_status"),
        *(row.get("oems") or ()),
    ]
    return any(q in str(value or "").lower() for value in values)


def read_scoped_warehouse_history(
    db_file,
    *,
    requested_warehouse_id: int,
    auth_context: WarehouseReadAuthorizationContext,
    order_metadata: Mapping[str, ScopedOrderMetadata],
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Return the versioned ADMIN/SKLAD warehouse-history read contract.

    Finance values come only from warehouse_history_v14. Order status/client/OEM
    values come only from the supplied authoritative scoped Order Core
    projection. This function performs no mutation.
    """
    warehouse_id = int(requested_warehouse_id)
    _authorize(auth_context, warehouse_id)
    metadata = _normalize_metadata(order_metadata)

    global_summary = warehouse_history.summary(db_file, warehouse_id)
    enriched = [
        _enrich_row(row, metadata, warehouse_id)
        for row in _all_history_rows(db_file, warehouse_id)
    ]
    filtered = [row for row in enriched if _matches(row, search)]

    start = max(0, int(offset))
    size = max(1, min(int(limit), 500))
    page_rows = filtered[start:start + size]

    return {
        "contract_version": READ_CONTRACT_VERSION,
        "scope": {
            "warehouse_id": warehouse_id,
            "effective_role": str(auth_context.effective_role),
            "effective_subject": str(auth_context.effective_subject),
            "acting_as": bool(auth_context.acting_as),
        },
        "summary": global_summary,
        "rows": page_rows,
        "page": {
            "offset": start,
            "limit": size,
            "matched_count": len(filtered),
        },
    }
