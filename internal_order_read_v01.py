#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal Order P3 scoped read service v0.1.

This is a server-side read contract only. Authorization contexts are disposable
test inputs and are NOT production authentication.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3

import warehouse_history_v14 as history


CONTRACT_VERSION = "internal-order-read-v0.1"


@dataclass(frozen=True)
class AdminReadContext:
    actual_actor: str
    authorized: bool


@dataclass(frozen=True)
class WarehouseReadContext:
    actual_actor: str
    warehouse_id: int
    authorized: bool


def _text(value, name):
    value=str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def _table_exists(c, table):
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)
    ).fetchone() is not None


def _columns(c, table):
    if not _table_exists(c,table):
        return set()
    return {str(r[1]) for r in c.execute(f"PRAGMA table_info({table})")}


def _validate_context(context, requested_warehouse_id):
    warehouse_id=int(requested_warehouse_id)
    if isinstance(context, AdminReadContext):
        _text(context.actual_actor,"actual_actor")
        if not context.authorized:
            raise PermissionError("ADMIN_READ_NOT_AUTHORIZED")
        return warehouse_id
    if isinstance(context, WarehouseReadContext):
        _text(context.actual_actor,"actual_actor")
        if not context.authorized:
            raise PermissionError("WAREHOUSE_READ_NOT_AUTHORIZED")
        if int(context.warehouse_id)!=warehouse_id:
            raise PermissionError("WAREHOUSE_READ_SCOPE_MISMATCH")
        return warehouse_id
    raise TypeError("unsupported read authorization context")


def _require_snapshot_row(c, warehouse_id, order_id):
    if not _table_exists(c,"internal_order_payment_snapshots_v01"):
        raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
    snap=c.execute(
        """SELECT order_id,warehouse_id,warehouse_order_number,client_id,
                  customer_amount_rub,partner_amount_rub,payment_route,
                  order_status_snapshot,created_at
           FROM internal_order_payment_snapshots_v01
           WHERE order_id=?""",
        (str(order_id),)
    ).fetchone()
    if snap is None:
        raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
    if int(snap[1])!=int(warehouse_id):
        raise PermissionError("INTERNAL_ORDER_WAREHOUSE_SCOPE_MISMATCH")
    return snap


def _payment_status(c, order_id):
    if not _table_exists(c,"internal_order_payment_state_v01"):
        raise ValueError("INTERNAL_PAYMENT_STATE_REQUIRED")
    row=c.execute(
        "SELECT payment_status FROM internal_order_payment_state_v01 WHERE order_id=?",
        (str(order_id),)
    ).fetchone()
    if row is None or str(row[0] or "") not in {"DUE","PAID"}:
        raise ValueError("INTERNAL_PAYMENT_STATE_REQUIRED")
    return str(row[0])


def _order_status(c, order_id):
    cols=_columns(c,"orders")
    if not {"order_id","status"}.issubset(cols):
        raise ValueError("INTERNAL_ORDER_METADATA_REQUIRED")
    row=c.execute("SELECT status FROM orders WHERE order_id=?",(str(order_id),)).fetchone()
    if row is None:
        raise KeyError("internal order not found")
    return str(row[0] or "")


def _oem_rows(c, warehouse_id, order_id):
    cols=_columns(c,"order_items")
    required={"order_id","offer_source","warehouse_id","quantity"}
    if not required.issubset(cols):
        raise ValueError("INTERNAL_ORDER_ITEM_METADATA_REQUIRED")
    selectable=[x for x in ("id","manufacturer","oem","requested_oem","name","quantity") if x in cols]
    if "quantity" not in selectable:
        raise ValueError("INTERNAL_ORDER_ITEM_METADATA_REQUIRED")
    rows=c.execute(
        f"""SELECT {','.join(selectable)}
            FROM order_items
            WHERE order_id=? AND offer_source='warehouse' AND warehouse_id=?
            ORDER BY COALESCE(position,id),id""",
        (str(order_id),int(warehouse_id))
    ).fetchall()
    if not rows:
        raise ValueError("INTERNAL_ORDER_SCOPE_DATA_REQUIRED")
    result=[]
    for row in rows:
        d=dict(zip(selectable,row))
        result.append({
            "order_item_id": d.get("id"),
            "manufacturer": d.get("manufacturer"),
            "oem": d.get("oem"),
            "requested_oem": d.get("requested_oem"),
            "name": d.get("name"),
            "quantity": int(d["quantity"]),
        })
    return result


def read_order(
    db_file,
    *,
    warehouse_id,
    order_id,
    context,
):
    """Strict snapshot-backed order read.

    Missing or mismatched trusted Internal payment data fails closed.
    """
    warehouse_id=_validate_context(context,warehouse_id)
    order_id=_text(order_id,"order_id")
    with sqlite3.connect(db_file) as c:
        snap=_require_snapshot_row(c,warehouse_id,order_id)
        status=_payment_status(c,order_id)
        current_order_status=_order_status(c,order_id)

        # Reuse authoritative history validation/shape. Exact one-order search.
        rows=history.order_rows(
            db_file,warehouse_id,search=order_id,limit=500,offset=0,conn=c
        )
        matching=[r for r in rows if r["extremizer_order_number"]==order_id]
        if len(matching)!=1:
            raise ValueError("INTERNAL_ORDER_READ_MODEL_MISMATCH")
        row=matching[0]
        if (
            int(row["warehouse_id"])!=warehouse_id
            or row["partner_amount_source"]!="internal_payment_snapshot"
            or row["client_id"]!=str(snap[3])
            or int(row["customer_amount_rub"])!=int(snap[4])
            or int(row["partner_amount_rub"])!=int(snap[5])
            or row["payment_status"]!=status
        ):
            raise ValueError("INTERNAL_ORDER_READ_MODEL_MISMATCH")

        return {
            "contract_version": CONTRACT_VERSION,
            "scope": {
                "warehouse_id": warehouse_id,
                "viewer_type": (
                    "admin" if isinstance(context,AdminReadContext) else "warehouse"
                ),
            },
            "order": {
                "extremizer_order_number": order_id,
                "warehouse_order_number": snap[2],
                "warehouse_id": warehouse_id,
                "client_id": str(snap[3]),
                "order_status": current_order_status,
                "payment_status": status,
                "payment_route": str(snap[6]),
                "customer_amount_rub": int(snap[4]),
                "partner_amount_rub": int(snap[5]),
                "partner_amount_source": "internal_payment_snapshot",
                "balance_rub": row["balance_rub"],
            },
            "items": _oem_rows(c,warehouse_id,order_id),
        }


def read_warehouse_history(
    db_file,
    *,
    warehouse_id,
    context,
    search=None,
    limit=50,
    offset=0,
):
    """Scoped warehouse history. Financial summary remains whole-ledger."""
    warehouse_id=_validate_context(context,warehouse_id)
    data=history.read_history(
        db_file,warehouse_id,search=search,limit=limit,offset=offset
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "scope": {
            "warehouse_id": warehouse_id,
            "viewer_type": (
                "admin" if isinstance(context,AdminReadContext) else "warehouse"
            ),
        },
        "summary": data["summary"],
        "rows": data["rows"],
    }


__all__=[
    "CONTRACT_VERSION",
    "AdminReadContext",
    "WarehouseReadContext",
    "read_order",
    "read_warehouse_history",
]
