#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only order service shared by WEB ADMIN and future admin interfaces.

This module intentionally performs no schema initialization and no writes.
Every database connection is opened with SQLite mode=ro.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


ACTIVE_EXECUTION_RESERVATION_STATUSES = {"hold", "reserved", "committed"}


class OrderNotFound(KeyError):
    """Requested client order does not exist."""


def _readonly_uri(db_file: Path | str) -> str:
    path = Path(db_file).expanduser().resolve()
    return path.as_uri() + "?mode=ro"


def _connect(db_file: Path | str) -> sqlite3.Connection:
    conn = sqlite3.connect(_readonly_uri(db_file), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        is not None
    )


def _execution_readiness(
    items: list[dict[str, Any]],
    reservations: list[dict[str, Any]],
) -> dict[str, Any]:
    """Mirror the current confirmed -> executing warehouse coverage gate."""

    coverage: dict[int, float] = {}
    for reservation in reservations:
        if str(reservation.get("status") or "") not in ACTIVE_EXECUTION_RESERVATION_STATUSES:
            continue
        item_id = reservation.get("order_item_id")
        if item_id is None:
            continue
        coverage[int(item_id)] = coverage.get(int(item_id), 0.0) + float(
            reservation.get("quantity") or 0
        )

    warehouse_items = [
        item
        for item in items
        if str(item.get("offer_source") or "usa").strip().lower() == "warehouse"
    ]

    problems: list[dict[str, Any]] = []
    ready_count = 0
    for item in warehouse_items:
        item_id = int(item["id"])
        required = float(item.get("quantity") or 0)
        covered = float(coverage.get(item_id, 0.0))
        ready = covered >= required
        if ready:
            ready_count += 1
        else:
            problems.append(
                {
                    "kind": "reservation_not_covered",
                    "order_item_id": item_id,
                    "position": item.get("position"),
                    "oem": item.get("oem"),
                    "required_quantity": required,
                    "covered_quantity": covered,
                }
            )

    return {
        "ready": not problems,
        "warehouse_item_count": len(warehouse_items),
        "warehouse_ready_count": ready_count,
        "problems": problems,
    }


def get_order(order_id: str, db_file: Path | str) -> dict[str, Any]:
    """Return one complete client-order snapshot without changing the database."""

    order_id = str(order_id or "").strip()
    if not order_id:
        raise OrderNotFound(order_id)

    with _connect(db_file) as conn:
        order_row = conn.execute(
            "SELECT * FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if order_row is None:
            raise OrderNotFound(order_id)

        order = dict(order_row)
        items = _rows(
            conn,
            "SELECT * FROM order_items WHERE order_id = ? ORDER BY position, id",
            (order_id,),
        )

        reservations: list[dict[str, Any]] = []
        if _table_exists(conn, "warehouse_stock_reservations"):
            reservations = _rows(
                conn,
                """
                SELECT
                    r.*,
                    w.internal_name AS warehouse_internal_name,
                    w.public_name AS warehouse_public_name_current,
                    w.city AS warehouse_city
                FROM warehouse_stock_reservations r
                LEFT JOIN warehouses w ON w.id = r.warehouse_id
                WHERE r.order_id = ?
                ORDER BY r.id
                """,
                (order_id,),
            )

        supplier_orders: list[dict[str, Any]] = []
        supplier_items: list[dict[str, Any]] = []
        if _table_exists(conn, "supplier_orders"):
            supplier_orders = _rows(
                conn,
                """
                SELECT
                    so.*,
                    w.internal_name AS warehouse_internal_name,
                    w.public_name AS warehouse_public_name_current
                FROM supplier_orders so
                LEFT JOIN warehouses w ON w.id = so.warehouse_id
                WHERE so.client_order_id = ?
                ORDER BY so.id
                """,
                (order_id,),
            )
        if supplier_orders and _table_exists(conn, "supplier_order_items"):
            supplier_items = _rows(
                conn,
                """
                SELECT soi.*
                FROM supplier_order_items soi
                JOIN supplier_orders so ON so.id = soi.supplier_order_id
                WHERE so.client_order_id = ?
                ORDER BY soi.id
                """,
                (order_id,),
            )

    reservations_by_item: dict[int, list[dict[str, Any]]] = {}
    for reservation in reservations:
        item_id = reservation.get("order_item_id")
        if item_id is not None:
            reservations_by_item.setdefault(int(item_id), []).append(reservation)

    supplier_items_by_item: dict[int, list[dict[str, Any]]] = {}
    for supplier_item in supplier_items:
        item_id = supplier_item.get("order_item_id")
        if item_id is not None:
            supplier_items_by_item.setdefault(int(item_id), []).append(supplier_item)

    enriched_items: list[dict[str, Any]] = []
    for item in items:
        item_copy = dict(item)
        item_id = int(item_copy["id"])
        item_copy["reservations"] = reservations_by_item.get(item_id, [])
        item_copy["supplier_items"] = supplier_items_by_item.get(item_id, [])
        enriched_items.append(item_copy)

    return {
        "order": order,
        "items": enriched_items,
        "reservations": reservations,
        "supplier_orders": supplier_orders,
        "supplier_items": supplier_items,
        "execution_readiness": _execution_readiness(items, reservations),
    }


def list_orders(
    db_file: Path | str,
    *,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Return a compact read-only order queue for WEB ADMIN."""

    limit = max(1, min(int(limit), 500))
    offset = max(0, int(offset))
    clauses: list[str] = []
    params: list[Any] = []

    if status:
        clauses.append("o.status = ?")
        params.append(str(status).strip())

    term = str(search or "").strip()
    if term:
        like = f"%{term}%"
        clauses.append(
            """
            (
                o.order_id LIKE ?
                OR COALESCE(o.customer_name, '') LIKE ?
                OR COALESCE(o.username, '') LIKE ?
                OR EXISTS (
                    SELECT 1
                    FROM order_items oi
                    WHERE oi.order_id = o.order_id
                      AND (
                          COALESCE(oi.oem, '') LIKE ?
                          OR COALESCE(oi.requested_oem, '') LIKE ?
                      )
                )
            )
            """
        )
        params.extend([like, like, like, like, like])

    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    sql = f"""
        SELECT
            o.*,
            (SELECT COUNT(*) FROM order_items oi WHERE oi.order_id = o.order_id)
                AS item_count,
            (
                SELECT COUNT(*)
                FROM order_items oi
                WHERE oi.order_id = o.order_id
                  AND LOWER(COALESCE(oi.offer_source, 'usa')) = 'warehouse'
            ) AS warehouse_item_count
        FROM orders o
        {where}
        ORDER BY o.created_at DESC, o.order_id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])

    with _connect(db_file) as conn:
        return _rows(conn, sql, tuple(params))
