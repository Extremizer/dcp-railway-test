#!/usr/bin/env python3
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import stock_engine
import warehouse_store

DEFAULT_DB = Path("extremizer_orders.db")
RESERVED_STALE_HOURS = 24


def _now():
    return datetime.now().astimezone()


def _parse(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_now().tzinfo)
    return dt
def _problem(kind, severity, title, **extra):
    item = {"kind": kind, "severity": severity, "title": title}
    item.update(extra)
    return item


def collect_warehouse_problems(db_file=DEFAULT_DB):
    warehouse_store.init_warehouse_db(db_file)
    problems = []
    now = _now()
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        orders = conn.execute(
            "SELECT order_id,status FROM orders "
            "WHERE status IN ('confirmed','executing')"
        ).fetchall()
        reservations = conn.execute(
            "SELECT r.*, w.internal_name,w.public_name,w.stock_sync_mode "
            "FROM warehouse_stock_reservations r "
            "JOIN warehouses w ON w.id=r.warehouse_id "
            "WHERE r.status IN ('hold','reserved','committed')"
        ).fetchall()
        sources = conn.execute(
            "SELECT s.*,w.internal_name FROM warehouse_sources s "
            "JOIN warehouses w ON w.id=s.warehouse_id "
            "WHERE w.active=1 AND s.enabled=1 AND s.last_error IS NOT NULL "
            "AND TRIM(s.last_error)<>''"
        ).fetchall()
        for order in orders:
            rows = conn.execute(
                "SELECT status FROM warehouse_stock_reservations "
                "WHERE order_id=? AND status IN "
                "('hold','reserved','committed','absorbed')",
                (order["order_id"],),
            ).fetchall()
            statuses = {str(x[0]) for x in rows}
            if order["status"] == "confirmed" and not statuses:
                problems.append(_problem(
                    "confirmed_no_warehouse", "warning",
                    "Подтверждён, но склад не выбран",
                    order_id=order["order_id"],
                ))
            if order["status"] == "executing" and not (
                {"committed", "absorbed"} & statuses
            ):
                problems.append(_problem(
                    "executing_no_committed", "critical",
                    "Выполняется, но нет committed-резерва",
                    order_id=order["order_id"],
                ))

        for source in sources:
            problems.append(_problem(
                "source_error", "warning",
                "Ошибка обновления источника",
                warehouse_id=source["warehouse_id"],
                warehouse_name=source["internal_name"],
                source_type=source["source_type"],
                detail=str(source["last_error"]),
            ))
        for row in reservations:
            created = _parse(row["created_at"])
            expires = _parse(row["expires_at"])
            hanging = (
                row["status"] == "hold" and expires and expires <= now
            ) or (
                row["status"] == "reserved" and created
                and created <= now - timedelta(hours=RESERVED_STALE_HOURS)
            )
            if hanging:
                problems.append(_problem(
                    "hanging_reservation", "warning",
                    "Зависший hold/reserved",
                    order_id=row["order_id"],
                    reservation_id=row["id"],
                    warehouse_id=row["warehouse_id"],
                    warehouse_name=row["internal_name"],
                    oem=row["oem"],
                    reservation_status=row["status"],
                ))

            state = stock_engine.get_available_stock(
                int(row["warehouse_id"]), str(row["oem"]), db_file
            )
            if state.get("raw_quantity") is None:
                problems.append(_problem(
                    "stock_unknown", "warning", "Остаток неизвестен",
                    order_id=row["order_id"], reservation_id=row["id"],
                    warehouse_name=row["internal_name"], oem=row["oem"],
                ))
            elif not state.get("is_fresh"):
                problems.append(_problem(
                    "stock_stale", "warning", "Остаток устарел",
                    order_id=row["order_id"], reservation_id=row["id"],
                    warehouse_name=row["internal_name"], oem=row["oem"],
                ))
            if float(state.get("deficit_quantity") or 0) > 0:
                problems.append(_problem(
                    "stock_insufficient", "critical",
                    "Остатка недостаточно для активных резервов",
                    order_id=row["order_id"], reservation_id=row["id"],
                    warehouse_name=row["internal_name"], oem=row["oem"],
                    deficit_quantity=state["deficit_quantity"],
                ))

            if (
                row["status"] == "committed"
                and row["stock_sync_mode"] == "snapshot_absorbs_committed"
            ):
                committed = _parse(row["committed_at"])
                newer = conn.execute(
                    "SELECT MAX(observed_at) FROM warehouse_stock_current "
                    "WHERE warehouse_id=? AND oem=?",
                    (row["warehouse_id"], row["oem"]),
                ).fetchone()[0]
                observed = _parse(newer)
                if committed and observed and observed > committed:
                    problems.append(_problem(
                        "snapshot_mismatch", "critical",
                        "Несогласованность после snapshot",
                        order_id=row["order_id"], reservation_id=row["id"],
                        warehouse_name=row["internal_name"], oem=row["oem"],
                    ))
    return problems


def problems_for_order(order_id, db_file=DEFAULT_DB):
    return [
        p for p in collect_warehouse_problems(db_file)
        if str(p.get("order_id") or "") == str(order_id)
    ]
def problem_counts(db_file=DEFAULT_DB):
    problems = collect_warehouse_problems(db_file)
    counts = {}
    for item in problems:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {"total": len(problems), "by_kind": counts, "items": problems}
