#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only order service shared by WEB ADMIN and future admin interfaces.

This module intentionally performs no schema initialization and no writes.
Every database connection is opened with SQLite mode=ro.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
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



def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _reservation_blocks_readonly(
    reservation: dict[str, Any],
    *,
    now: datetime,
    snapshot_observed_at: datetime | None,
    sync_mode: str,
) -> bool:
    status = str(reservation.get("status") or "")
    if status == "hold":
        expires = _parse_dt(reservation.get("expires_at"))
        return expires is None or expires > now
    if status == "reserved":
        return True
    if status != "committed":
        return False
    if sync_mode != "snapshot_absorbs_committed":
        return True
    committed_at = _parse_dt(reservation.get("committed_at"))
    if committed_at is None or snapshot_observed_at is None:
        return True
    return committed_at > snapshot_observed_at


def _best_stock_snapshot_readonly(
    conn: sqlite3.Connection,
    warehouse_id: int,
    oem: str,
    now: datetime,
) -> dict[str, Any] | None:
    rows = _rows(
        conn,
        """
        SELECT
            c.*,
            s.priority AS source_priority
        FROM warehouse_stock_current c
        JOIN warehouse_sources s
          ON s.warehouse_id = c.warehouse_id
         AND s.source_type = c.source_type
        WHERE c.warehouse_id = ?
          AND c.oem = ?
          AND s.enabled = 1
        ORDER BY s.priority ASC, c.observed_at DESC
        """,
        (warehouse_id, oem.strip()),
    )
    if not rows:
        return None

    fresh: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for row in rows:
        expires = _parse_dt(row.get("expires_at"))
        (fresh if expires is None or expires > now else stale).append(row)

    def rank(row: dict[str, Any]):
        status = str(row.get("status") or "")
        quantity = row.get("quantity")
        if quantity is not None and status in {"in_stock", "out_of_stock"}:
            info_rank = 0
        elif status == "quantity_unknown":
            info_rank = 1
        elif status == "not_found":
            info_rank = 2
        else:
            info_rank = 3
        observed = _parse_dt(row.get("observed_at"))
        return (
            info_rank,
            int(row.get("source_priority") or 999),
            -(observed.timestamp() if observed else 0.0),
        )

    chosen = dict(min(fresh if fresh else stale, key=rank))
    chosen["is_fresh"] = bool(fresh)
    return chosen


def _best_price_snapshot_readonly(
    conn: sqlite3.Connection,
    warehouse_id: int,
    oem: str,
    now: datetime,
) -> dict[str, Any] | None:
    rows = _rows(
        conn,
        """
        SELECT
            c.*,
            s.priority AS source_priority
        FROM warehouse_stock_current c
        JOIN warehouse_sources s
          ON s.warehouse_id = c.warehouse_id
         AND s.source_type = c.source_type
        WHERE c.warehouse_id = ?
          AND c.oem = ?
          AND s.enabled = 1
          AND c.price_rub IS NOT NULL
        ORDER BY s.priority ASC, c.observed_at DESC
        """,
        (warehouse_id, oem.strip()),
    )
    fresh = []
    for row in rows:
        expires = _parse_dt(row.get("expires_at"))
        if expires is None or expires > now:
            fresh.append(row)
    if not fresh:
        return None

    def rank(row: dict[str, Any]):
        observed = _parse_dt(row.get("observed_at"))
        return (
            int(row.get("source_priority") or 999),
            -(observed.timestamp() if observed else 0.0),
        )

    return dict(min(fresh, key=rank))


def prepare_order_dry_run(
    order_id: str,
    db_file: Path | str,
) -> dict[str, Any]:
    """Describe preparation actions without changing the database.

    This is intentionally implemented only with mode=ro SQLite connections.
    It mirrors current warehouse snapshot/reservation semantics closely enough
    to tell the admin what would be changed by a later apply step.
    """

    snapshot = get_order(order_id, db_file)
    order = snapshot["order"]
    items = snapshot["items"]
    now = datetime.now().astimezone()

    rows: list[dict[str, Any]] = []
    blockers: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []

    if str(order.get("status") or "") != "confirmed":
        blockers.append(
            {
                "kind": "order_status",
                "message": "Подготовка к исполнению разрешена только для подтверждённого заказа.",
            }
        )

    with closing(_connect(db_file)) as conn:
        for item in items:
            source = str(item.get("offer_source") or "usa").strip().lower()
            base = {
                "order_item_id": int(item["id"]),
                "position": item.get("position"),
                "oem": str(item.get("oem") or ""),
                "source": source,
                "required_quantity": float(item.get("quantity") or 0),
            }

            if source != "warehouse":
                rows.append(
                    {
                        **base,
                        "state": "ok",
                        "message": "Позиция США: складской резерв не требуется.",
                        "planned_action": "none",
                    }
                )
                continue

            warehouse_id = item.get("warehouse_id")
            if warehouse_id is None:
                problem = {
                    **base,
                    "kind": "warehouse_missing",
                    "message": "Для складской позиции не выбран склад.",
                }
                blockers.append(problem)
                rows.append({**problem, "state": "blocked", "planned_action": "none"})
                continue

            warehouse_id = int(warehouse_id)
            warehouse = conn.execute(
                """
                SELECT id, public_name, active, deleted_at, stock_sync_mode
                FROM warehouses WHERE id = ?
                """,
                (warehouse_id,),
            ).fetchone()
            if warehouse is None or not int(warehouse["active"] or 0) or warehouse["deleted_at"] is not None:
                problem = {
                    **base,
                    "warehouse_id": warehouse_id,
                    "kind": "warehouse_inactive",
                    "message": "Выбранный склад отсутствует или неактивен.",
                }
                blockers.append(problem)
                rows.append({**problem, "state": "blocked", "planned_action": "none"})
                continue

            oem = str(item.get("oem") or "").strip()
            stock = _best_stock_snapshot_readonly(conn, warehouse_id, oem, now)
            price = _best_price_snapshot_readonly(conn, warehouse_id, oem, now)
            sync_mode = str(warehouse["stock_sync_mode"] or "manual")

            own_coverage = 0.0
            blocked_total = 0.0
            stock_observed_at = _parse_dt(stock.get("observed_at")) if stock else None
            for reservation in item.get("reservations") or []:
                if _reservation_blocks_readonly(
                    reservation,
                    now=now,
                    snapshot_observed_at=stock_observed_at,
                    sync_mode=sync_mode,
                ):
                    own_coverage += float(reservation.get("quantity") or 0)

            all_active = _rows(
                conn,
                """
                SELECT *
                FROM warehouse_stock_reservations
                WHERE warehouse_id = ?
                  AND oem = ?
                  AND status IN ('hold', 'reserved', 'committed')
                ORDER BY created_at, id
                """,
                (warehouse_id, oem),
            )
            for reservation in all_active:
                if _reservation_blocks_readonly(
                    reservation,
                    now=now,
                    snapshot_observed_at=stock_observed_at,
                    sync_mode=sync_mode,
                ):
                    blocked_total += float(reservation.get("quantity") or 0)

            required = float(item.get("quantity") or 0)
            missing_reserve = max(required - own_coverage, 0.0)
            raw_qty = (
                float(stock["quantity"])
                if stock is not None and stock.get("quantity") is not None
                else None
            )
            available_now = (
                max(raw_qty - blocked_total, 0.0)
                if raw_qty is not None
                else None
            )
            available_for_order = (
                max(raw_qty - max(blocked_total - own_coverage, 0.0), 0.0)
                if raw_qty is not None
                else None
            )

            row = {
                **base,
                "warehouse_id": warehouse_id,
                "warehouse_public_name": warehouse["public_name"],
                "stock_status": stock.get("status") if stock else "unknown",
                "stock_is_fresh": bool(stock and stock.get("is_fresh")),
                "stock_observed_at": stock.get("observed_at") if stock else None,
                "stock_expires_at": stock.get("expires_at") if stock else None,
                "raw_quantity": raw_qty,
                "blocked_quantity": blocked_total,
                "available_quantity": available_now,
                "available_for_order": available_for_order,
                "existing_coverage": own_coverage,
                "missing_reserve": missing_reserve,
                "order_price_rub": item.get("price_snapshot_rub"),
                "current_price_rub": price.get("price_rub") if price else None,
                "price_source_type": price.get("source_type") if price else None,
            }

            problems = []
            if stock is None:
                problems.append("stock_unknown")
            elif not stock.get("is_fresh"):
                problems.append("stock_stale")
            elif raw_qty is None:
                problems.append("stock_unknown")
            elif available_for_order is None or available_for_order < required:
                problems.append("insufficient_stock")

            order_price = item.get("price_snapshot_rub")
            current_price = price.get("price_rub") if price else None
            if current_price is None:
                problems.append("price_unknown")
            elif order_price is not None and float(current_price) != float(order_price):
                problems.append("price_changed")

            if problems:
                row["state"] = "blocked"
                row["problems"] = problems
                row["planned_action"] = "none"
                blockers.append(
                    {
                        **base,
                        "warehouse_id": warehouse_id,
                        "kind": problems[0],
                        "problems": problems,
                        "message": ", ".join(problems),
                    }
                )
            elif missing_reserve > 0:
                row["state"] = "action"
                row["planned_action"] = "reserve_missing"
                row["planned_quantity"] = missing_reserve
                actions.append(
                    {
                        **base,
                        "warehouse_id": warehouse_id,
                        "action": "reserve_missing",
                        "quantity": missing_reserve,
                    }
                )
            else:
                row["state"] = "ok"
                row["planned_action"] = "none"

            rows.append(row)

    return {
        "order_id": str(order_id),
        "order_status": str(order.get("status") or ""),
        "ready_to_apply": not blockers,
        "would_change_db": bool(actions),
        "rows": rows,
        "actions": actions,
        "blockers": blockers,
    }

def get_order(order_id: str, db_file: Path | str) -> dict[str, Any]:
    """Return one complete client-order snapshot without changing the database."""

    order_id = str(order_id or "").strip()
    if not order_id:
        raise OrderNotFound(order_id)

    with closing(_connect(db_file)) as conn:
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

    with closing(_connect(db_file)) as conn:
        return _rows(conn, sql, tuple(params))
