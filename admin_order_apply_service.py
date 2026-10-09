#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WEB ADMIN 2 preparation Apply service.

The public entry point is intentionally DB-file driven so the first regression
stage can run against an isolated SQLite fixture. No web route calls this
module yet.

Safety properties:
- order must still be confirmed;
- one BEGIN IMMEDIATE covers the full warehouse-order preparation;
- current stock/price are rechecked inside the write transaction;
- only missing reservation quantity is created;
- a repeated Apply is idempotent;
- any blocker rolls back every reservation created by that Apply.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import admin_order_service
import stock_engine


class _ApplyBlocked(RuntimeError):
    def __init__(self, reason: str, **details: Any):
        super().__init__(reason)
        self.reason = reason
        self.details = details


def _blocked_result(
    order_id: str,
    reason: str,
    *,
    dry_run: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "order_id": str(order_id),
        "state": "blocked",
        "changed": False,
        "reason": str(reason),
        "details": dict(details or {}),
        "reservation_ids": [],
        "dry_run": dry_run,
    }


def prepare_order_apply(
    order_id: str,
    db_file: Path | str,
) -> dict[str, Any]:
    """Create only missing warehouse reservations for one confirmed order.

    The initial dry-run keeps the UI/business result aligned with WEB ADMIN 2.
    Every write-sensitive condition is then checked again under BEGIN IMMEDIATE,
    so the dry-run is never trusted as a write authorization by itself.
    """

    order_id = str(order_id or "").strip()
    if not order_id:
        return _blocked_result(order_id, "order_not_found")

    try:
        pre = admin_order_service.prepare_order_dry_run(order_id, db_file)
    except admin_order_service.OrderNotFound:
        return _blocked_result(order_id, "order_not_found")

    if not pre.get("ready_to_apply"):
        return _blocked_result(
            order_id,
            "dry_run_blocked",
            dry_run=pre,
            details={"blockers": list(pre.get("blockers") or [])},
        )

    # Already prepared: do not even open a write transaction.
    if not pre.get("would_change_db"):
        return {
            "ok": True,
            "order_id": order_id,
            "state": "no_change",
            "changed": False,
            "reason": None,
            "details": {},
            "reservation_ids": [],
            "dry_run": pre,
            "post_dry_run": pre,
        }

    created_ids: list[int] = []
    now = datetime.now().astimezone()

    try:
        with sqlite3.connect(db_file, timeout=30) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("BEGIN IMMEDIATE")

            order = conn.execute(
                "SELECT status FROM orders WHERE order_id = ?",
                (order_id,),
            ).fetchone()
            if order is None:
                raise _ApplyBlocked("order_not_found")
            if str(order["status"] or "") != "confirmed":
                raise _ApplyBlocked(
                    "order_status",
                    current_status=str(order["status"] or ""),
                )

            items = conn.execute(
                """
                SELECT
                    id,
                    position,
                    manufacturer,
                    oem,
                    quantity,
                    warehouse_id,
                    price_snapshot_rub
                FROM order_items
                WHERE order_id = ?
                  AND COALESCE(NULLIF(LOWER(TRIM(offer_source)), ''), 'usa')
                      = 'warehouse'
                ORDER BY position, id
                """,
                (order_id,),
            ).fetchall()

            for item in items:
                item_id = int(item["id"])
                warehouse_id = item["warehouse_id"]
                oem = str(item["oem"] or "").strip()
                required = float(item["quantity"] or 0)

                if warehouse_id is None:
                    raise _ApplyBlocked(
                        "warehouse_missing",
                        order_item_id=item_id,
                        oem=oem,
                    )
                warehouse_id = int(warehouse_id)

                if not stock_engine._warehouse_is_active(conn, warehouse_id):
                    raise _ApplyBlocked(
                        "warehouse_inactive",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                    )

                stock = stock_engine._best_snapshot(
                    conn,
                    warehouse_id,
                    oem,
                    now,
                )
                if stock is None:
                    raise _ApplyBlocked(
                        "stock_unknown",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                    )
                if not stock.get("is_fresh"):
                    raise _ApplyBlocked(
                        "stock_stale",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                    )

                raw = stock.get("quantity")
                if raw is None:
                    raise _ApplyBlocked(
                        "stock_unknown",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                    )

                price = stock_engine._best_price_snapshot(
                    conn,
                    warehouse_id,
                    oem,
                    now,
                )
                if price is None or price.get("price_rub") is None:
                    raise _ApplyBlocked(
                        "price_unknown",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                    )

                order_price = item["price_snapshot_rub"]
                current_price = float(price["price_rub"])
                if (
                    order_price is not None
                    and current_price != float(order_price)
                ):
                    raise _ApplyBlocked(
                        "price_changed",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                        order_price_rub=float(order_price),
                        current_price_rub=current_price,
                    )

                observed_at = stock_engine._parse_dt(stock.get("observed_at"))
                blocked_total, active = stock_engine._blocking_quantity(
                    conn,
                    warehouse_id,
                    oem,
                    snapshot_observed_at=observed_at,
                    now=now,
                )

                own_coverage = 0.0
                for reservation in active:
                    if (
                        str(reservation.get("order_id") or "") == order_id
                        and reservation.get("order_item_id") is not None
                        and int(reservation["order_item_id"]) == item_id
                    ):
                        own_coverage += float(reservation.get("quantity") or 0)

                missing = max(required - own_coverage, 0.0)
                if missing <= 0:
                    continue

                available_now = max(float(raw) - float(blocked_total), 0.0)
                if available_now < missing:
                    raise _ApplyBlocked(
                        "insufficient_stock",
                        order_item_id=item_id,
                        warehouse_id=warehouse_id,
                        oem=oem,
                        required_quantity=required,
                        existing_coverage=own_coverage,
                        missing_quantity=missing,
                        available_quantity=available_now,
                    )

                reservation_id = stock_engine._create_reservation(
                    conn,
                    warehouse_id=warehouse_id,
                    oem=oem,
                    quantity=missing,
                    status="reserved",
                    order_id=order_id,
                    order_item_id=item_id,
                    manufacturer=item["manufacturer"],
                    expires_at=None,
                    notes="WEB ADMIN 2 Apply preparation",
                )
                created_ids.append(int(reservation_id))

            conn.commit()

    except _ApplyBlocked as exc:
        return _blocked_result(
            order_id,
            exc.reason,
            dry_run=pre,
            details=exc.details,
        )
    except sqlite3.Error as exc:
        return _blocked_result(
            order_id,
            "sqlite_error",
            dry_run=pre,
            details={"error_type": type(exc).__name__},
        )

    post = admin_order_service.prepare_order_dry_run(order_id, db_file)
    post_ready = bool(post.get("ready_to_apply"))
    post_has_actions = bool(post.get("would_change_db"))

    return {
        "ok": post_ready and not post_has_actions,
        "order_id": order_id,
        "state": (
            "prepared"
            if post_ready and not post_has_actions
            else "postcheck_blocked"
        ),
        "changed": bool(created_ids),
        "reason": (
            None
            if post_ready and not post_has_actions
            else "postcheck_not_ready"
        ),
        "details": {
            "blockers": list(post.get("blockers") or []),
            "remaining_actions": list(post.get("actions") or []),
        },
        "reservation_ids": created_ids,
        "dry_run": pre,
        "post_dry_run": post,
    }
