#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warehouse internal balance adapter for CONTRACT v1.4.

This is an extension of the existing partner-finance domain, not a second
FinanceEngine. It models only the owner-approved internal warehouse balance:
direct-payment margin accrual, full-coverage reservation, application and
append-only corrections.

No production wiring is performed by this module.
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
            """
        )


def _require_text(value: Any, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


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


def balance_summary(db_file, warehouse_id: int) -> dict:
    init(db_file)
    with sqlite3.connect(db_file) as c:
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
) -> dict:
    """Atomically confirm full direct payment and post C-P once.

    The caller must supply authoritative, already confirmed order amounts.
    A negative margin is intentionally rejected because CONTRACT v1.4 leaves
    negative Internal margin as an open owner decision.
    """
    confirmation_id = _require_text(confirmation_id, "confirmation_id")
    order_id = _require_text(order_id, "order_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    warehouse_id = int(warehouse_id)
    customer_amount_rub = int(customer_amount_rub)
    partner_amount_rub = int(partner_amount_rub)
    if customer_amount_rub < 0 or partner_amount_rub < 0:
        raise ValueError("amounts must be non-negative")
    difference = customer_amount_rub - partner_amount_rub
    if difference < 0:
        raise ValueError("NEGATIVE_INTERNAL_MARGIN_NOT_APPROVED")

    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            """SELECT * FROM warehouse_direct_payment_confirmations
               WHERE confirmation_id=? OR order_id=?""",
            (confirmation_id, order_id),
        ).fetchone()
        if existing:
            same = (
                str(existing["confirmation_id"]) == confirmation_id
                and int(existing["warehouse_id"]) == warehouse_id
                and str(existing["order_id"]) == order_id
                and int(existing["customer_amount_rub"]) == customer_amount_rub
                and int(existing["partner_amount_rub"]) == partner_amount_rub
                and int(existing["difference_rub"]) == difference
            )
            if not same:
                raise ValueError("direct payment confirmation conflict")
            c.rollback()
            return {
                "confirmation_id": confirmation_id,
                "warehouse_id": warehouse_id,
                "order_id": order_id,
                "difference_rub": difference,
                "replayed": True,
                "balance": balance_summary(db_file, warehouse_id),
            }

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
                str(effective_subject or "") or None,
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
                    str(effective_subject or "") or None,
                    f"DIRECT_PAYMENT_MARGIN:{confirmation_id}",
                    now,
                ),
            )
        c.commit()

    return {
        "confirmation_id": confirmation_id,
        "warehouse_id": warehouse_id,
        "order_id": order_id,
        "difference_rub": difference,
        "replayed": False,
        "balance": balance_summary(db_file, warehouse_id),
    }


def reserve_full_balance(
    db_file,
    *,
    reservation_id: str,
    warehouse_id: int,
    order_id: str,
    required_amount_rub: int,
    actual_actor: str,
    effective_subject: str | None = None,
) -> dict:
    """Reserve only the full partner amount; partial reservation is impossible."""
    reservation_id = _require_text(reservation_id, "reservation_id")
    order_id = _require_text(order_id, "order_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    warehouse_id = int(warehouse_id)
    required = int(required_amount_rub)
    if required <= 0:
        raise ValueError("required_amount_rub must be positive")

    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        existing = c.execute(
            """SELECT * FROM warehouse_internal_balance_reservations
               WHERE reservation_id=? OR order_id=?""",
            (reservation_id, order_id),
        ).fetchone()
        if existing:
            same = (
                str(existing["reservation_id"]) == reservation_id
                and int(existing["warehouse_id"]) == warehouse_id
                and str(existing["order_id"]) == order_id
                and int(existing["amount_rub"]) == required
            )
            if not same:
                raise ValueError("warehouse balance reservation conflict")
            c.rollback()
            return dict(existing)

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
                str(effective_subject or "") or None,
                now,
                now,
            ),
        )
        c.commit()
    return {
        "reservation_id": reservation_id,
        "warehouse_id": warehouse_id,
        "order_id": order_id,
        "amount_rub": required,
        "status": "reserved",
    }


def apply_reserved_balance(
    db_file,
    *,
    reservation_id: str,
    actual_actor: str,
) -> dict:
    reservation_id = _require_text(reservation_id, "reservation_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            """SELECT * FROM warehouse_internal_balance_reservations
               WHERE reservation_id=?""",
            (reservation_id,),
        ).fetchone()
        if not row:
            raise KeyError("warehouse balance reservation not found")
        row = dict(row)
        if row["status"] == "applied":
            c.rollback()
            return row
        if row["status"] != "reserved":
            raise ValueError("warehouse balance reservation is not active")

        key = f"WAREHOUSE_BALANCE_APPLY:{reservation_id}"
        existing = c.execute(
            """SELECT id FROM warehouse_internal_balance_ledger
               WHERE idempotency_key=?""",
            (key,),
        ).fetchone()
        if not existing:
            c.execute(
                """INSERT INTO warehouse_internal_balance_ledger(
                     warehouse_id,order_id,event_type,amount_rub,
                     reference_type,reference_id,actual_actor,effective_subject,
                     idempotency_key,created_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    int(row["warehouse_id"]),
                    str(row["order_id"]),
                    "WAREHOUSE_BALANCE_APPLIED",
                    -int(row["amount_rub"]),
                    "warehouse_balance_reservation",
                    reservation_id,
                    actual_actor,
                    row["effective_subject"],
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
        c.commit()

    return {
        "reservation_id": reservation_id,
        "warehouse_id": int(row["warehouse_id"]),
        "order_id": str(row["order_id"]),
        "amount_rub": int(row["amount_rub"]),
        "status": "applied",
        "balance": balance_summary(db_file, int(row["warehouse_id"])),
    }


def release_reserved_balance(
    db_file,
    *,
    reservation_id: str,
    actual_actor: str,
) -> dict:
    reservation_id = _require_text(reservation_id, "reservation_id")
    _require_text(actual_actor, "actual_actor")
    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM warehouse_internal_balance_reservations WHERE reservation_id=?",
            (reservation_id,),
        ).fetchone()
        if not row:
            raise KeyError("warehouse balance reservation not found")
        row = dict(row)
        if row["status"] == "released":
            c.rollback()
            return row
        if row["status"] != "reserved":
            raise ValueError("applied warehouse balance cannot be released")
        c.execute(
            """UPDATE warehouse_internal_balance_reservations
               SET status='released',updated_at=?
               WHERE reservation_id=?""",
            (_now(), reservation_id),
        )
        c.commit()
    row["status"] = "released"
    return row


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
) -> int:
    """Append-only correction; never rewrites prior accrual/application rows."""
    correction_id = _require_text(correction_id, "correction_id")
    order_id = _require_text(order_id, "order_id")
    actual_actor = _require_text(actual_actor, "actual_actor")
    reason = _require_text(reason, "reason")
    amount = int(amount_rub)
    if amount == 0:
        raise ValueError("correction amount must be non-zero")
    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.execute("BEGIN IMMEDIATE")
        key = f"WAREHOUSE_BALANCE_CORRECTION:{correction_id}"
        existing = c.execute(
            """SELECT id,warehouse_id,order_id,amount_rub
               FROM warehouse_internal_balance_ledger
               WHERE idempotency_key=?""",
            (key,),
        ).fetchone()
        if existing:
            if (
                int(existing[1]) != int(warehouse_id)
                or str(existing[2]) != order_id
                or int(existing[3]) != amount
            ):
                raise ValueError("warehouse balance correction conflict")
            c.rollback()
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
                str(effective_subject or "") or None,
                key,
                _now(),
            ),
        )
        event_id = int(cur.lastrowid)
        c.execute(
            """CREATE TABLE IF NOT EXISTS warehouse_balance_correction_reasons(
                 ledger_event_id INTEGER PRIMARY KEY,
                 reason TEXT NOT NULL
               )"""
        )
        c.execute(
            """INSERT INTO warehouse_balance_correction_reasons(
                 ledger_event_id,reason) VALUES(?,?)""",
            (event_id, reason),
        )
        c.commit()
        return event_id
