#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only warehouse finance history for CONTRACT v1.4 / Internal P3.

The service reads the new warehouse-balance ledger and shared Order Core
snapshots. If an Internal Order v0.1 immutable payment snapshot exists, it is
the authoritative source for C/P/client/payment-route/warehouse order number.

Legacy warehouse_partner_settlements are deliberately not merged.
Search/pagination never affect warehouse-wide financial totals.
"""
from __future__ import annotations

import sqlite3


_INTERNAL_SNAPSHOT_TABLE = "internal_order_payment_snapshots_v01"
_INTERNAL_STATE_TABLE = "internal_order_payment_state_v01"


def _connection(db_file, conn):
    own = conn is None
    c = sqlite3.connect(db_file) if own else conn
    if own:
        c.row_factory = sqlite3.Row
    return c, own


def _table_exists(c, table: str) -> bool:
    return c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def _columns(c, table: str) -> set[str]:
    if not _table_exists(c, table):
        return set()
    return {str(row[1]) for row in c.execute(f"PRAGMA table_info({table})")}


def _summary_conn(c, warehouse_id: int) -> dict:
    warehouse_id = int(warehouse_id)
    posted = int(c.execute(
        "SELECT COALESCE(SUM(amount_rub),0) FROM warehouse_internal_balance_ledger WHERE warehouse_id=?",
        (warehouse_id,),
    ).fetchone()[0] or 0)
    reserved = int(c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_reservations
           WHERE warehouse_id=? AND status='reserved'""",
        (warehouse_id,),
    ).fetchone()[0] or 0)
    accruals = int(c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_ledger
           WHERE warehouse_id=? AND event_type='DIRECT_PAYMENT_MARGIN'""",
        (warehouse_id,),
    ).fetchone()[0] or 0)
    applied_signed = int(c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_ledger
           WHERE warehouse_id=? AND event_type='WAREHOUSE_BALANCE_APPLIED'""",
        (warehouse_id,),
    ).fetchone()[0] or 0)
    corrections = int(c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_ledger
           WHERE warehouse_id=? AND event_type='CORRECTION'""",
        (warehouse_id,),
    ).fetchone()[0] or 0)
    return {
        "warehouse_id": warehouse_id,
        "posted_balance_rub": posted,
        "reserved_balance_rub": reserved,
        "available_balance_rub": posted - reserved,
        "direct_payment_accruals_rub": accruals,
        "applied_balance_rub": abs(applied_signed),
        "corrections_net_rub": corrections,
    }


def summary(db_file, warehouse_id: int, *, conn=None) -> dict:
    c, own = _connection(db_file, conn)
    try:
        return _summary_conn(c, warehouse_id)
    finally:
        if own:
            c.close()


def _internal_snapshot_conn(c, order_id: str):
    if not _table_exists(c, _INTERNAL_SNAPSHOT_TABLE):
        return None
    return c.execute(
        """SELECT order_id,warehouse_id,warehouse_order_number,client_id,
                  customer_amount_rub,partner_amount_rub,payment_route,
                  order_status_snapshot,created_by,created_at
           FROM internal_order_payment_snapshots_v01
           WHERE order_id=?""",
        (str(order_id),),
    ).fetchone()


def _internal_payment_status_conn(c, order_id: str) -> str:
    if not _table_exists(c, _INTERNAL_STATE_TABLE):
        raise ValueError("INTERNAL_PAYMENT_STATE_REQUIRED")
    row = c.execute(
        """SELECT payment_status
           FROM internal_order_payment_state_v01
           WHERE order_id=?""",
        (str(order_id),),
    ).fetchone()
    if row is None or str(row[0] or "") not in {"DUE", "PAID"}:
        raise ValueError("INTERNAL_PAYMENT_STATE_REQUIRED")
    return str(row[0])


def _validate_internal_snapshot_scope_conn(
    c,
    *,
    warehouse_id: int,
    order_id: str,
    snapshot,
) -> None:
    if str(snapshot[0]) != str(order_id):
        raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_ORDER_MISMATCH")
    if int(snapshot[1]) != int(warehouse_id):
        raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_WAREHOUSE_MISMATCH")

    item_cols = _columns(c, "order_items")
    required = {"order_id", "offer_source", "warehouse_id"}
    if not required.issubset(item_cols):
        raise ValueError("INTERNAL_ORDER_SCOPE_DATA_REQUIRED")

    rows = c.execute(
        """SELECT offer_source,warehouse_id
           FROM order_items
           WHERE order_id=?""",
        (str(order_id),),
    ).fetchall()
    if not rows:
        raise ValueError("INTERNAL_ORDER_SCOPE_DATA_REQUIRED")
    for source, line_warehouse_id in rows:
        if str(source or "").strip().lower() != "warehouse":
            raise ValueError("INTERNAL_ORDER_SCOPE_MISMATCH")
        if line_warehouse_id is None or int(line_warehouse_id) != int(warehouse_id):
            raise ValueError("INTERNAL_ORDER_SCOPE_MISMATCH")


def _known_order_ids_conn(c, warehouse_id: int) -> list[str]:
    warehouse_id = int(warehouse_id)
    ids: set[str] = set()
    for table, sql in [
        ("warehouse_internal_balance_ledger",
         "SELECT DISTINCT order_id FROM warehouse_internal_balance_ledger WHERE warehouse_id=?"),
        ("warehouse_internal_balance_reservations",
         "SELECT DISTINCT order_id FROM warehouse_internal_balance_reservations WHERE warehouse_id=?"),
        ("warehouse_direct_payment_confirmations",
         "SELECT DISTINCT order_id FROM warehouse_direct_payment_confirmations WHERE warehouse_id=?"),
        (_INTERNAL_SNAPSHOT_TABLE,
         "SELECT DISTINCT order_id FROM internal_order_payment_snapshots_v01 WHERE warehouse_id=?"),
    ]:
        if _table_exists(c, table):
            ids.update(
                str(row[0]) for row in c.execute(sql, (warehouse_id,)).fetchall()
                if row[0] is not None
            )
    item_cols = _columns(c, "order_items")
    if {"order_id", "offer_source", "warehouse_id"}.issubset(item_cols):
        ids.update(
            str(row[0])
            for row in c.execute(
                """SELECT DISTINCT order_id FROM order_items
                   WHERE offer_source='warehouse' AND warehouse_id=?""",
                (warehouse_id,),
            ).fetchall()
            if row[0] is not None
        )
    return sorted(ids)


def _order_display_conn(c, warehouse_id: int, order_id: str) -> dict:
    order_cols = _columns(c, "orders")
    item_cols = _columns(c, "order_items")
    result = {
        "extremizer_order_number": str(order_id),
        "warehouse_order_number": None,
        "date": None,
        "order_status": None,
        "payment_status": None,
        "client_id": None,
        "customer_amount_rub": None,
        "partner_amount_rub": None,
        "partner_amount_source": None,
        "payment_route": None,
        "internal_payment_snapshot": False,
    }

    if "order_id" in order_cols:
        selected = ["order_id"]
        for col in ("warehouse_order_number", "created_at", "status"):
            if col in order_cols:
                selected.append(col)
        row = c.execute(
            f"SELECT {','.join(selected)} FROM orders WHERE order_id=?",
            (str(order_id),),
        ).fetchone()
        if row:
            mapping = dict(zip(selected, row))
            result["warehouse_order_number"] = mapping.get("warehouse_order_number")
            result["date"] = mapping.get("created_at")
            result["order_status"] = mapping.get("status")

    snapshot = _internal_snapshot_conn(c, order_id)
    if snapshot is not None:
        _validate_internal_snapshot_scope_conn(
            c,
            warehouse_id=warehouse_id,
            order_id=order_id,
            snapshot=snapshot,
        )
        result.update(
            {
                "warehouse_order_number": snapshot[2],
                "client_id": str(snapshot[3]),
                "customer_amount_rub": int(snapshot[4]),
                "partner_amount_rub": int(snapshot[5]),
                "partner_amount_source": "internal_payment_snapshot",
                "payment_route": str(snapshot[6]),
                "payment_status": _internal_payment_status_conn(c, order_id),
                "internal_payment_snapshot": True,
            }
        )
        return result

    required = {"order_id", "offer_source", "warehouse_id", "quantity", "customer_unit_rub"}
    if required.issubset(item_cols):
        total_row = c.execute(
            """SELECT
                 SUM(CASE WHEN customer_unit_rub IS NOT NULL
                          THEN customer_unit_rub * quantity ELSE 0 END),
                 SUM(CASE WHEN customer_unit_rub IS NULL THEN 1 ELSE 0 END),
                 COUNT(*)
               FROM order_items
               WHERE order_id=? AND offer_source='warehouse' AND warehouse_id=?""",
            (str(order_id), int(warehouse_id)),
        ).fetchone()
        if total_row and int(total_row[2] or 0) > 0 and int(total_row[1] or 0) == 0:
            result["customer_amount_rub"] = int(total_row[0] or 0)

    if _table_exists(c, "warehouse_direct_payment_confirmations"):
        row = c.execute(
            """SELECT customer_amount_rub,partner_amount_rub
               FROM warehouse_direct_payment_confirmations
               WHERE warehouse_id=? AND order_id=?""",
            (int(warehouse_id), str(order_id)),
        ).fetchone()
        if row:
            result["customer_amount_rub"] = int(row[0])
            result["partner_amount_rub"] = int(row[1])
            result["partner_amount_source"] = "direct_payment_confirmation"
            return result

    if _table_exists(c, "warehouse_internal_balance_reservations"):
        row = c.execute(
            """SELECT amount_rub
               FROM warehouse_internal_balance_reservations
               WHERE warehouse_id=? AND order_id=?""",
            (int(warehouse_id), str(order_id)),
        ).fetchone()
        if row:
            result["partner_amount_rub"] = int(row[0])
            result["partner_amount_source"] = "warehouse_balance_reservation"
    return result


def _order_effect_conn(c, warehouse_id: int, order_id: str):
    row = c.execute(
        """SELECT COUNT(*),COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_ledger
           WHERE warehouse_id=? AND order_id=?""",
        (int(warehouse_id), str(order_id)),
    ).fetchone()
    return None if int(row[0] or 0) == 0 else int(row[1] or 0)


def order_rows(
    db_file,
    warehouse_id: int,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn=None,
) -> list[dict]:
    """Return warehouse history row data without changing financial meaning.

    For an Internal v0.1 snapshot-backed order, C/P/client/payment status come
    from that immutable domain. For legacy/pre-terms rows, P is never invented.
    """
    c, own = _connection(db_file, conn)
    try:
        ids = _known_order_ids_conn(c, warehouse_id)
        q = str(search or "").strip().lower()
        rows = []
        for order_id in ids:
            display = _order_display_conn(c, warehouse_id, order_id)
            if q and q not in str(order_id).lower() and q not in str(
                display.get("warehouse_order_number") or ""
            ).lower():
                continue
            display["warehouse_id"] = int(warehouse_id)
            display["balance_rub"] = _order_effect_conn(c, warehouse_id, order_id)
            rows.append(display)
        rows.sort(
            key=lambda x: (str(x.get("date") or ""), x["extremizer_order_number"]),
            reverse=True,
        )
        start = max(0, int(offset))
        size = max(1, min(int(limit), 500))
        return rows[start:start + size]
    finally:
        if own:
            c.close()


def ledger_events(
    db_file,
    warehouse_id: int,
    *,
    limit: int = 100,
    offset: int = 0,
    conn=None,
) -> list[dict]:
    c, own = _connection(db_file, conn)
    try:
        size = max(1, min(int(limit), 1000))
        start = max(0, int(offset))
        cur = c.execute(
            """SELECT id,warehouse_id,order_id,event_type,amount_rub,
                      reference_type,reference_id,actual_actor,effective_subject,
                      idempotency_key,created_at
               FROM warehouse_internal_balance_ledger
               WHERE warehouse_id=?
               ORDER BY id DESC LIMIT ? OFFSET ?""",
            (int(warehouse_id), size, start),
        )
        names = [d[0] for d in cur.description]
        return [dict(zip(names, row)) for row in cur.fetchall()]
    finally:
        if own:
            c.close()


def read_history(
    db_file,
    warehouse_id: int,
    *,
    search: str | None = None,
    limit: int = 50,
    offset: int = 0,
    conn=None,
) -> dict:
    """Summary is always global; search/pagination affects only returned rows."""
    c, own = _connection(db_file, conn)
    try:
        return {
            "summary": _summary_conn(c, warehouse_id),
            "rows": order_rows(
                db_file,
                warehouse_id,
                search=search,
                limit=limit,
                offset=offset,
                conn=c,
            ),
        }
    finally:
        if own:
            c.close()
