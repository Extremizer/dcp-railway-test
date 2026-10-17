#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warehouse internal balance adapter for CONTRACT v1.4.

This extends the shared finance domain; it is not a second FinanceEngine.
Every public mutator keeps legacy self-owned transaction behavior and also
accepts an optional caller-owned SQLite connection for atomic Order Core flows.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def init(db_file) -> None:
    with sqlite3.connect(db_file) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS warehouse_direct_payment_confirmations(
              confirmation_id TEXT PRIMARY KEY,
              warehouse_id INTEGER NOT NULL,
              order_id TEXT NOT NULL UNIQUE,
              customer_amount_rub INTEGER NOT NULL CHECK(customer_amount_rub >= 0),
              partner_amount_rub INTEGER NOT NULL CHECK(partner_amount_rub >= 0),
              difference_rub INTEGER NOT NULL CHECK(difference_rub >= 0),
              actual_actor TEXT NOT NULL,
              effective_subject TEXT,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS warehouse_internal_balance_ledger(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              warehouse_id INTEGER NOT NULL,
              order_id TEXT NOT NULL,
              event_type TEXT NOT NULL,
              amount_rub INTEGER NOT NULL CHECK(amount_rub != 0),
              reference_type TEXT NOT NULL,
              reference_id TEXT NOT NULL,
              actual_actor TEXT NOT NULL,
              effective_subject TEXT,
              idempotency_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_warehouse_internal_balance_ledger_wh
              ON warehouse_internal_balance_ledger(warehouse_id,id);

            CREATE TABLE IF NOT EXISTS warehouse_internal_balance_reservations(
              reservation_id TEXT PRIMARY KEY,
              warehouse_id INTEGER NOT NULL,
              order_id TEXT NOT NULL UNIQUE,
              amount_rub INTEGER NOT NULL CHECK(amount_rub > 0),
              status TEXT NOT NULL CHECK(status IN ('reserved','applied','released')),
              actual_actor TEXT NOT NULL,
              effective_subject TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_warehouse_balance_reservation_wh_status
              ON warehouse_internal_balance_reservations(warehouse_id,status);

            CREATE TABLE IF NOT EXISTS warehouse_balance_correction_reasons(
              ledger_event_id INTEGER PRIMARY KEY,
              reason TEXT NOT NULL
            );
            """
        )


def _require_text(value: Any, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def _connection(db_file, conn):
    own = conn is None
    if own:
        init(db_file)
        c = sqlite3.connect(db_file, timeout=30)
        c.row_factory = sqlite3.Row
    else:
        c = conn
    return c, own


def _begin_owned(c, own: bool) -> None:
    if own:
        c.execute("BEGIN IMMEDIATE")


def _finish(c, own: bool, *, ok: bool) -> None:
    if not own:
        return
    try:
        c.commit() if ok else c.rollback()
    finally:
        c.close()


def _ledger_sum_conn(c, warehouse_id: int) -> int:
    row = c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_ledger
           WHERE warehouse_id=?""",
        (int(warehouse_id),),
    ).fetchone()
    return int(row[0] or 0)


def _reserved_sum_conn(c, warehouse_id: int) -> int:
    row = c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_internal_balance_reservations
           WHERE warehouse_id=? AND status='reserved'""",
        (int(warehouse_id),),
    ).fetchone()
    return int(row[0] or 0)


def _balance_summary_conn(c, warehouse_id: int) -> dict:
    posted = _ledger_sum_conn(c, warehouse_id)
    reserved = _reserved_sum_conn(c, warehouse_id)
    spent = abs(
        int(
            c.execute(
                """SELECT COALESCE(SUM(amount_rub),0)
                   FROM warehouse_internal_balance_ledger
                   WHERE warehouse_id=?
                     AND event_type='WAREHOUSE_BALANCE_APPLIED'""",
                (int(warehouse_id),),
            ).fetchone()[0]
            or 0
        )
    )
    return {
        "warehouse_id": int(warehouse_id),
        "posted_rub": posted,
        "reserved_rub": reserved,
        "available_rub": posted - reserved,
        "spent_rub": spent,
    }


def balance_summary(db_file, warehouse_id: int, conn=None) -> dict:
    c, own = _connection(db_file, conn)
    try:
        return _balance_summary_conn(c, warehouse_id)
    finally:
        if own:
            c.close()


def confirm_direct_payment_and_accrue(
    db_file,
    *,
    confirmation_id: str,
    warehouse_id: int,
    order_id: str,
    customer_amount_rub: int,
    partner_amount_rub: int,
    actual_actor: str,
    effective_subject: str | None = None,
    conn=None,
) -> dict:
    """Confirm full direct payment and post C-P exactly once.

    When conn is supplied, transaction ownership remains with the caller:
    no BEGIN/COMMIT/ROLLBACK/CLOSE is executed here.
    """
    confirmation_id = _require_text(confirmation_id, "confirmation_id")
    order_id = _require_text(order_id, "order_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    effective_subject = str(effective_subject or "") or None
    warehouse_id = int(warehouse_id)
    customer_amount_rub = int(customer_amount_rub)
    partner_amount_rub = int(partner_amount_rub)
    if customer_amount_rub < 0 or partner_amount_rub < 0:
        raise ValueError("amounts must be non-negative")
    difference = customer_amount_rub - partner_amount_rub
    if difference < 0:
        raise ValueError("NEGATIVE_INTERNAL_MARGIN_NOT_APPROVED")

    c, own = _connection(db_file, conn)
    ok = False
    try:
        _begin_owned(c, own)
        existing = c.execute(
            """SELECT confirmation_id,warehouse_id,order_id,
                      customer_amount_rub,partner_amount_rub,difference_rub,
                      actual_actor,effective_subject
               FROM warehouse_direct_payment_confirmations
               WHERE confirmation_id=? OR order_id=?""",
            (confirmation_id, order_id),
        ).fetchone()
        if existing:
            same = (
                str(existing[0]) == confirmation_id
                and int(existing[1]) == warehouse_id
                and str(existing[2]) == order_id
                and int(existing[3]) == customer_amount_rub
                and int(existing[4]) == partner_amount_rub
                and int(existing[5]) == difference
                and str(existing[6]) == actual_actor
                and existing[7] == effective_subject
            )
            if not same:
                raise ValueError("direct payment confirmation conflict")
            result = {
                "confirmation_id": confirmation_id,
                "warehouse_id": warehouse_id,
                "order_id": order_id,
                "difference_rub": difference,
                "replayed": True,
                "balance": _balance_summary_conn(c, warehouse_id),
            }
            ok = True
            return result

        now = _now()
        c.execute(
            """INSERT INTO warehouse_direct_payment_confirmations(
                 confirmation_id,warehouse_id,order_id,
                 customer_amount_rub,partner_amount_rub,difference_rub,
                 actual_actor,effective_subject,created_at)
               VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                confirmation_id,
                warehouse_id,
                order_id,
                customer_amount_rub,
                partner_amount_rub,
                difference,
                actual_actor,
                effective_subject,
                now,
            ),
        )
        if difference > 0:
            c.execute(
                """INSERT INTO warehouse_internal_balance_ledger(
                     warehouse_id,order_id,event_type,amount_rub,
                     reference_type,reference_id,actual_actor,effective_subject,
                     idempotency_key,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    warehouse_id,
                    order_id,
                    "DIRECT_PAYMENT_MARGIN",
                    difference,
                    "direct_payment_confirmation",
                    confirmation_id,
                    actual_actor,
                    effective_subject,
                    f"DIRECT_PAYMENT_MARGIN:{confirmation_id}",
                    now,
                ),
            )
        result = {
            "confirmation_id": confirmation_id,
            "warehouse_id": warehouse_id,
            "order_id": order_id,
            "difference_rub": difference,
            "replayed": False,
            "balance": _balance_summary_conn(c, warehouse_id),
        }
        ok = True
        return result
    finally:
        _finish(c, own, ok=ok)


def reserve_full_balance(
    db_file,
    *,
    reservation_id: str,
    warehouse_id: int,
    order_id: str,
    required_amount_rub: int,
    actual_actor: str,
    effective_subject: str | None = None,
    conn=None,
) -> dict:
    """Reserve exactly the full partner amount; partial reservation is impossible."""
    reservation_id = _require_text(reservation_id, "reservation_id")
    order_id = _require_text(order_id, "order_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    effective_subject = str(effective_subject or "") or None
    warehouse_id = int(warehouse_id)
    required = int(required_amount_rub)
    if required <= 0:
        raise ValueError("required_amount_rub must be positive")

    c, own = _connection(db_file, conn)
    ok = False
    try:
        _begin_owned(c, own)
        existing = c.execute(
            """SELECT reservation_id,warehouse_id,order_id,amount_rub,status,
                      actual_actor,effective_subject,created_at,updated_at
               FROM warehouse_internal_balance_reservations
               WHERE reservation_id=? OR order_id=?""",
            (reservation_id, order_id),
        ).fetchone()
        if existing:
            same = (
                str(existing[0]) == reservation_id
                and int(existing[1]) == warehouse_id
                and str(existing[2]) == order_id
                and int(existing[3]) == required
                and str(existing[5]) == actual_actor
                and existing[6] == effective_subject
            )
            if not same:
                raise ValueError("warehouse balance reservation conflict")
            result = {
                "reservation_id": str(existing[0]),
                "warehouse_id": int(existing[1]),
                "order_id": str(existing[2]),
                "amount_rub": int(existing[3]),
                "status": str(existing[4]),
                "actual_actor": str(existing[5]),
                "effective_subject": existing[6],
                "created_at": existing[7],
                "updated_at": existing[8],
            }
            ok = True
            return result

        available = _ledger_sum_conn(c, warehouse_id) - _reserved_sum_conn(
            c, warehouse_id
        )
        if available < required:
            raise ValueError(
                f"INSUFFICIENT_WAREHOUSE_BALANCE:{available}:{required}"
            )
        now = _now()
        c.execute(
            """INSERT INTO warehouse_internal_balance_reservations(
                 reservation_id,warehouse_id,order_id,amount_rub,status,
                 actual_actor,effective_subject,created_at,updated_at)
               VALUES(?,?,?,?, 'reserved', ?,?,?,?)""",
            (
                reservation_id,
                warehouse_id,
                order_id,
                required,
                actual_actor,
                effective_subject,
                now,
                now,
            ),
        )
        result = {
            "reservation_id": reservation_id,
            "warehouse_id": warehouse_id,
            "order_id": order_id,
            "amount_rub": required,
            "status": "reserved",
        }
        ok = True
        return result
    finally:
        _finish(c, own, ok=ok)


def apply_reserved_balance(
    db_file,
    *,
    reservation_id: str,
    actual_actor: str,
    conn=None,
) -> dict:
    reservation_id = _require_text(reservation_id, "reservation_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    c, own = _connection(db_file, conn)
    ok = False
    try:
        _begin_owned(c, own)
        row = c.execute(
            """SELECT reservation_id,warehouse_id,order_id,amount_rub,status,
                      actual_actor,effective_subject,created_at,updated_at
               FROM warehouse_internal_balance_reservations
               WHERE reservation_id=?""",
            (reservation_id,),
        ).fetchone()
        if not row:
            raise KeyError("warehouse balance reservation not found")
        data = {
            "reservation_id": str(row[0]),
            "warehouse_id": int(row[1]),
            "order_id": str(row[2]),
            "amount_rub": int(row[3]),
            "status": str(row[4]),
            "actual_actor": str(row[5]),
            "effective_subject": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }
        if data["status"] == "applied":
            ok = True
            return data
        if data["status"] != "reserved":
            raise ValueError("warehouse balance reservation is not active")

        key = f"WAREHOUSE_BALANCE_APPLY:{reservation_id}"
        existing = c.execute(
            """SELECT id,warehouse_id,order_id,event_type,amount_rub,
                      reference_type,reference_id
               FROM warehouse_internal_balance_ledger
               WHERE idempotency_key=?""",
            (key,),
        ).fetchone()
        expected_amount = -data["amount_rub"]
        if existing:
            same = (
                int(existing[1]) == data["warehouse_id"]
                and str(existing[2]) == data["order_id"]
                and str(existing[3]) == "WAREHOUSE_BALANCE_APPLIED"
                and int(existing[4]) == expected_amount
                and str(existing[5]) == "warehouse_balance_reservation"
                and str(existing[6]) == reservation_id
            )
            if not same:
                raise ValueError("warehouse balance application conflict")
        else:
            c.execute(
                """INSERT INTO warehouse_internal_balance_ledger(
                     warehouse_id,order_id,event_type,amount_rub,
                     reference_type,reference_id,actual_actor,effective_subject,
                     idempotency_key,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    data["warehouse_id"],
                    data["order_id"],
                    "WAREHOUSE_BALANCE_APPLIED",
                    expected_amount,
                    "warehouse_balance_reservation",
                    reservation_id,
                    actual_actor,
                    data["effective_subject"],
                    key,
                    _now(),
                ),
            )
        now = _now()
        c.execute(
            """UPDATE warehouse_internal_balance_reservations
               SET status='applied',updated_at=?
               WHERE reservation_id=?""",
            (now, reservation_id),
        )
        result = {
            "reservation_id": reservation_id,
            "warehouse_id": data["warehouse_id"],
            "order_id": data["order_id"],
            "amount_rub": data["amount_rub"],
            "status": "applied",
            "balance": _balance_summary_conn(c, data["warehouse_id"]),
        }
        ok = True
        return result
    finally:
        _finish(c, own, ok=ok)


def release_reserved_balance(
    db_file,
    *,
    reservation_id: str,
    actual_actor: str,
    conn=None,
) -> dict:
    reservation_id = _require_text(reservation_id, "reservation_id")
    _require_text(actual_actor, "actual_actor")
    c, own = _connection(db_file, conn)
    ok = False
    try:
        _begin_owned(c, own)
        row = c.execute(
            """SELECT reservation_id,warehouse_id,order_id,amount_rub,status,
                      actual_actor,effective_subject,created_at,updated_at
               FROM warehouse_internal_balance_reservations
               WHERE reservation_id=?""",
            (reservation_id,),
        ).fetchone()
        if not row:
            raise KeyError("warehouse balance reservation not found")
        data = {
            "reservation_id": str(row[0]),
            "warehouse_id": int(row[1]),
            "order_id": str(row[2]),
            "amount_rub": int(row[3]),
            "status": str(row[4]),
            "actual_actor": str(row[5]),
            "effective_subject": row[6],
            "created_at": row[7],
            "updated_at": row[8],
        }
        if data["status"] == "released":
            ok = True
            return data
        if data["status"] != "reserved":
            raise ValueError("applied warehouse balance cannot be released")
        c.execute(
            """UPDATE warehouse_internal_balance_reservations
               SET status='released',updated_at=?
               WHERE reservation_id=?""",
            (_now(), reservation_id),
        )
        data["status"] = "released"
        ok = True
        return data
    finally:
        _finish(c, own, ok=ok)


def post_correction(
    db_file,
    *,
    correction_id: str,
    warehouse_id: int,
    order_id: str,
    amount_rub: int,
    actual_actor: str,
    reason: str,
    effective_subject: str | None = None,
    conn=None,
) -> int:
    """Append-only correction; never rewrites prior accrual/application rows."""
    correction_id = _require_text(correction_id, "correction_id")
    order_id = _require_text(order_id, "order_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    reason = _require_text(reason, "reason")
    amount = int(amount_rub)
    if amount == 0:
        raise ValueError("correction amount must be non-zero")
    effective_subject = str(effective_subject or "") or None

    c, own = _connection(db_file, conn)
    ok = False
    try:
        _begin_owned(c, own)
        key = f"WAREHOUSE_BALANCE_CORRECTION:{correction_id}"
        existing = c.execute(
            """SELECT l.id,l.warehouse_id,l.order_id,l.event_type,l.amount_rub,
                      l.reference_type,l.reference_id,l.actual_actor,
                      l.effective_subject,r.reason
               FROM warehouse_internal_balance_ledger l
               LEFT JOIN warehouse_balance_correction_reasons r
                 ON r.ledger_event_id=l.id
               WHERE l.idempotency_key=?""",
            (key,),
        ).fetchone()
        if existing:
            same = (
                int(existing[1]) == int(warehouse_id)
                and str(existing[2]) == order_id
                and str(existing[3]) == "CORRECTION"
                and int(existing[4]) == amount
                and str(existing[5]) == "correction"
                and str(existing[6]) == correction_id
                and str(existing[7]) == actual_actor
                and existing[8] == effective_subject
                and existing[9] == reason
            )
            if not same:
                raise ValueError("warehouse balance correction conflict")
            ok = True
            return int(existing[0])
        cur = c.execute(
            """INSERT INTO warehouse_internal_balance_ledger(
                 warehouse_id,order_id,event_type,amount_rub,
                 reference_type,reference_id,actual_actor,effective_subject,
                 idempotency_key,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (
                int(warehouse_id),
                order_id,
                "CORRECTION",
                amount,
                "correction",
                correction_id,
                actual_actor,
                effective_subject,
                key,
                _now(),
            ),
        )
        event_id = int(cur.lastrowid)
        c.execute(
            """INSERT INTO warehouse_balance_correction_reasons(
                 ledger_event_id,reason) VALUES(?,?)""",
            (event_id, reason),
        )
        ok = True
        return event_id
    finally:
        _finish(c, own, ok=ok)
