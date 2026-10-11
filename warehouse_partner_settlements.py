#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Independent warehouse-partner settlement ledger.

This module is intentionally separate from Client FinanceEngine and from
Supplier Order logistics. It snapshots the financial relationship between
Extremizer and a partner warehouse for one warehouse order item.

Sign convention from Extremizer's point of view:
  negative balance -> Extremizer owes the warehouse;
  positive balance -> warehouse owes Extremizer;
  zero             -> settlement is closed.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any

from common_finance_contract import FulfillmentRoute, PaymentRoute

SETTLEMENT_MODES = {"pending_terms", "payable", "receivable", "none"}
SETTLEMENT_STATUSES = {
    "pending_terms",
    "ready",
    "open",
    "settled",
    "no_settlement",
    "cancelled",
}
PAYMENT_DIRECTIONS = {"to_partner", "from_partner"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def init(db_file) -> None:
    with sqlite3.connect(db_file) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS warehouse_partner_settlements(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              warehouse_id INTEGER NOT NULL,
              order_id TEXT NOT NULL,
              order_item_id INTEGER NOT NULL UNIQUE,
              supplier_order_id INTEGER,
              payment_route TEXT NOT NULL,
              fulfillment_route TEXT NOT NULL,
              customer_total_rub INTEGER NOT NULL CHECK(customer_total_rub >= 0),
              settlement_mode TEXT NOT NULL DEFAULT 'pending_terms',
              partner_amount_rub INTEGER,
              terms_source TEXT,
              notes TEXT,
              status TEXT NOT NULL DEFAULT 'pending_terms',
              accrued_at TEXT,
              settled_at TEXT,
              cancelled_at TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              CHECK(settlement_mode IN (
                'pending_terms','payable','receivable','none'
              )),
              CHECK(status IN (
                'pending_terms','ready','open','settled',
                'no_settlement','cancelled'
              )),
              CHECK(partner_amount_rub IS NULL OR partner_amount_rub >= 0)
            );

            CREATE INDEX IF NOT EXISTS idx_partner_settlement_warehouse
              ON warehouse_partner_settlements(warehouse_id,status);
            CREATE INDEX IF NOT EXISTS idx_partner_settlement_order
              ON warehouse_partner_settlements(order_id);

            CREATE TABLE IF NOT EXISTS warehouse_partner_ledger(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              warehouse_id INTEGER NOT NULL,
              settlement_id INTEGER NOT NULL,
              order_id TEXT NOT NULL,
              order_item_id INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              amount_rub INTEGER NOT NULL,
              description TEXT NOT NULL,
              reference_type TEXT NOT NULL,
              reference_id TEXT NOT NULL,
              actor TEXT NOT NULL,
              idempotency_key TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              FOREIGN KEY(settlement_id)
                REFERENCES warehouse_partner_settlements(id)
            );

            CREATE INDEX IF NOT EXISTS idx_partner_ledger_warehouse
              ON warehouse_partner_ledger(warehouse_id,created_at);
            CREATE INDEX IF NOT EXISTS idx_partner_ledger_settlement
              ON warehouse_partner_ledger(settlement_id,id);

            CREATE TABLE IF NOT EXISTS warehouse_partner_settlement_events(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              settlement_id INTEGER NOT NULL,
              event_type TEXT NOT NULL,
              actor TEXT NOT NULL,
              details_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL,
              FOREIGN KEY(settlement_id)
                REFERENCES warehouse_partner_settlements(id)
            );

            CREATE INDEX IF NOT EXISTS idx_partner_settlement_events
              ON warehouse_partner_settlement_events(settlement_id,id);
            """
        )


def _row_dict(row):
    return dict(row) if row is not None else None


def _event_conn(c, settlement_id: int, event_type: str, actor: str, details=None):
    c.execute(
        """INSERT INTO warehouse_partner_settlement_events(
             settlement_id,event_type,actor,details_json,created_at)
           VALUES(?,?,?,?,?)""",
        (
            int(settlement_id),
            str(event_type),
            str(actor or "system"),
            _json(details or {}),
            _now(),
        ),
    )


def _balance_conn(c, settlement_id: int) -> int:
    row = c.execute(
        """SELECT COALESCE(SUM(amount_rub),0)
           FROM warehouse_partner_ledger
           WHERE settlement_id=?""",
        (int(settlement_id),),
    ).fetchone()
    return int(row[0] or 0)


def balance(db_file, settlement_id: int) -> int:
    with sqlite3.connect(db_file) as c:
        return _balance_conn(c, settlement_id)


def get(db_file, settlement_id: int) -> dict:
    with sqlite3.connect(db_file) as c:
        c.row_factory = sqlite3.Row
        row = c.execute(
            """SELECT s.*,w.public_name AS warehouse_public_name,
                      oi.oem,oi.name,oi.quantity
               FROM warehouse_partner_settlements s
               LEFT JOIN warehouses w ON w.id=s.warehouse_id
               LEFT JOIN order_items oi ON oi.id=s.order_item_id
               WHERE s.id=?""",
            (int(settlement_id),),
        ).fetchone()
        if not row:
            raise KeyError("warehouse partner settlement not found")
        result = dict(row)
        result["balance_rub"] = _balance_conn(c, settlement_id)
        return result


def _order_item_snapshot_conn(c, order_item_id: int) -> dict:
    c.row_factory = sqlite3.Row
    columns = {
        row[1] for row in c.execute("PRAGMA table_info(order_items)").fetchall()
    }
    fulfillment_expr = (
        "COALESCE(fulfillment_route,'through_extremizer')"
        if "fulfillment_route" in columns
        else "'through_extremizer'"
    )
    row = c.execute(
        f"""SELECT id,order_id,offer_source,warehouse_id,
                   payment_route,{fulfillment_expr} AS fulfillment_route,
                   quantity,customer_unit_rub,price_snapshot_rub,oem,name
            FROM order_items
            WHERE id=?""",
        (int(order_item_id),),
    ).fetchone()
    if not row:
        raise KeyError("order item not found")
    x = dict(row)
    if str(x["offer_source"] or "").strip().lower() != "warehouse":
        raise ValueError("partner settlement applies only to warehouse order items")
    if x["warehouse_id"] is None:
        raise ValueError("warehouse order item has no warehouse_id")

    payment_route = PaymentRoute(
        str(x["payment_route"] or PaymentRoute.EXTREMIZER_BALANCE.value)
    ).value
    fulfillment_route = FulfillmentRoute(
        str(
            x["fulfillment_route"]
            or FulfillmentRoute.THROUGH_EXTREMIZER.value
        )
    ).value

    qty = int(x["quantity"] or 0)
    if qty <= 0:
        raise ValueError("warehouse order item quantity must be positive")

    unit = x["customer_unit_rub"]
    if unit is None:
        unit = x["price_snapshot_rub"]
    if unit is None:
        raise ValueError("customer RUB price snapshot is missing")
    customer_total = int(round(float(unit))) * qty
    if customer_total < 0:
        raise ValueError("customer total must not be negative")

    supplier = c.execute(
        """SELECT soi.supplier_order_id
           FROM supplier_order_items soi
           WHERE soi.order_item_id=?
           ORDER BY soi.id
           LIMIT 1""",
        (int(order_item_id),),
    ).fetchone()
    x.update(
        {
            "payment_route": payment_route,
            "fulfillment_route": fulfillment_route,
            "customer_total_rub": customer_total,
            "supplier_order_id": int(supplier[0]) if supplier else None,
        }
    )
    return x


def ensure_for_order_item(
    db_file,
    order_item_id: int,
    *,
    actor: str = "system",
) -> dict:
    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        snapshot = _order_item_snapshot_conn(c, order_item_id)
        existing = c.execute(
            """SELECT * FROM warehouse_partner_settlements
               WHERE order_item_id=?""",
            (int(order_item_id),),
        ).fetchone()
        if existing:
            existing = dict(existing)
            invariant = (
                int(existing["warehouse_id"]) == int(snapshot["warehouse_id"])
                and str(existing["order_id"]) == str(snapshot["order_id"])
                and str(existing["payment_route"]) == snapshot["payment_route"]
                and str(existing["fulfillment_route"])
                == snapshot["fulfillment_route"]
                and int(existing["customer_total_rub"])
                == int(snapshot["customer_total_rub"])
            )
            if not invariant:
                raise ValueError(
                    "existing partner settlement snapshot conflicts with order item"
                )
            c.rollback()
            return get(db_file, int(existing["id"]))

        now = _now()
        cur = c.execute(
            """INSERT INTO warehouse_partner_settlements(
                 warehouse_id,order_id,order_item_id,supplier_order_id,
                 payment_route,fulfillment_route,customer_total_rub,
                 settlement_mode,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,'pending_terms','pending_terms',?,?)""",
            (
                int(snapshot["warehouse_id"]),
                str(snapshot["order_id"]),
                int(order_item_id),
                snapshot["supplier_order_id"],
                snapshot["payment_route"],
                snapshot["fulfillment_route"],
                int(snapshot["customer_total_rub"]),
                now,
                now,
            ),
        )
        settlement_id = int(cur.lastrowid)
        _event_conn(
            c,
            settlement_id,
            "settlement_created",
            actor,
            {
                "payment_route": snapshot["payment_route"],
                "fulfillment_route": snapshot["fulfillment_route"],
                "customer_total_rub": int(snapshot["customer_total_rub"]),
            },
        )
        c.commit()
    return get(db_file, settlement_id)


def sync_pending_from_orders(db_file, *, actor: str = "admin") -> dict:
    """Explicitly register warehouse order items that have no settlement row.

    This is intentionally an explicit admin action, never a GET-side effect and
    never an automatic logistics transition.
    """
    init(db_file)
    with sqlite3.connect(db_file) as c:
        c.row_factory = sqlite3.Row
        ids = [
            int(row["id"])
            for row in c.execute(
                """SELECT oi.id
                   FROM order_items oi
                   LEFT JOIN warehouse_partner_settlements s
                     ON s.order_item_id=oi.id
                   WHERE oi.offer_source='warehouse'
                     AND oi.warehouse_id IS NOT NULL
                     AND s.id IS NULL
                   ORDER BY oi.id"""
            ).fetchall()
        ]

    created = 0
    errors = []
    for order_item_id in ids:
        try:
            ensure_for_order_item(db_file, order_item_id, actor=actor)
            created += 1
        except Exception as exc:
            errors.append(
                {"order_item_id": order_item_id, "error": str(exc)}
            )
    return {
        "found": len(ids),
        "created": created,
        "errors": errors,
    }


def set_terms(
    db_file,
    settlement_id: int,
    *,
    mode: str,
    partner_amount_rub: int | None,
    terms_source: str,
    actor: str,
    notes: str | None = None,
) -> dict:
    mode = str(mode or "").strip().lower()
    if mode not in {"payable", "receivable", "none"}:
        raise ValueError("invalid settlement mode")
    terms_source = str(terms_source or "").strip()
    actor = str(actor or "").strip()
    if not terms_source:
        raise ValueError("terms_source required")
    if not actor:
        raise ValueError("actor required")

    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM warehouse_partner_settlements WHERE id=?",
            (int(settlement_id),),
        ).fetchone()
        if not row:
            raise KeyError("warehouse partner settlement not found")
        row = dict(row)
        if row["status"] not in {"pending_terms", "ready"}:
            raise ValueError("settlement terms are locked after accrual/closure")

        payment_route = PaymentRoute(row["payment_route"])
        if mode == "payable":
            if payment_route is not PaymentRoute.EXTREMIZER_BALANCE:
                raise ValueError(
                    "payable mode requires client payment to Extremizer"
                )
            amount = int(partner_amount_rub or 0)
            if amount <= 0:
                raise ValueError("partner payable amount must be positive")
            status = "ready"
        elif mode == "receivable":
            if payment_route is not PaymentRoute.DIRECT_PARTNER:
                raise ValueError(
                    "receivable mode requires direct client payment to partner"
                )
            amount = int(partner_amount_rub or 0)
            if amount <= 0:
                raise ValueError("partner receivable amount must be positive")
            status = "ready"
        else:
            amount = 0
            status = "no_settlement"

        same = (
            row["settlement_mode"] == mode
            and int(row["partner_amount_rub"] or 0) == amount
            and str(row["terms_source"] or "") == terms_source
            and str(row["notes"] or "") == str(notes or "")
            and row["status"] == status
        )
        if same:
            c.rollback()
            return get(db_file, settlement_id)

        now = _now()
        c.execute(
            """UPDATE warehouse_partner_settlements
               SET settlement_mode=?,partner_amount_rub=?,
                   terms_source=?,notes=?,status=?,
                   settled_at=CASE WHEN ?='no_settlement' THEN ? ELSE NULL END,
                   updated_at=?
               WHERE id=?""",
            (
                mode,
                amount,
                terms_source,
                notes,
                status,
                status,
                now,
                now,
                int(settlement_id),
            ),
        )
        _event_conn(
            c,
            settlement_id,
            "terms_set",
            actor,
            {
                "mode": mode,
                "partner_amount_rub": amount,
                "terms_source": terms_source,
                "status": status,
            },
        )
        c.commit()
    return get(db_file, settlement_id)


def _ledger_by_key(c, key: str):
    c.row_factory = sqlite3.Row
    return c.execute(
        "SELECT * FROM warehouse_partner_ledger WHERE idempotency_key=?",
        (str(key),),
    ).fetchone()


def _insert_ledger_conn(
    c,
    *,
    settlement: dict,
    event_type: str,
    amount_rub: int,
    description: str,
    reference_type: str,
    reference_id: str,
    actor: str,
    idempotency_key: str,
) -> int:
    existing = _ledger_by_key(c, idempotency_key)
    if existing:
        same = (
            int(existing["settlement_id"]) == int(settlement["id"])
            and str(existing["event_type"]) == str(event_type)
            and int(existing["amount_rub"]) == int(amount_rub)
            and str(existing["reference_type"]) == str(reference_type)
            and str(existing["reference_id"]) == str(reference_id)
        )
        if not same:
            raise ValueError("partner ledger idempotency key conflict")
        return int(existing["id"])

    cur = c.execute(
        """INSERT INTO warehouse_partner_ledger(
             warehouse_id,settlement_id,order_id,order_item_id,
             event_type,amount_rub,description,reference_type,reference_id,
             actor,idempotency_key,created_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            int(settlement["warehouse_id"]),
            int(settlement["id"]),
            str(settlement["order_id"]),
            int(settlement["order_item_id"]),
            str(event_type),
            int(amount_rub),
            str(description),
            str(reference_type),
            str(reference_id),
            str(actor),
            str(idempotency_key),
            _now(),
        ),
    )
    return int(cur.lastrowid)


def accrue(db_file, settlement_id: int, *, actor: str) -> dict:
    actor = str(actor or "").strip()
    if not actor:
        raise ValueError("actor required")
    init(db_file)

    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM warehouse_partner_settlements WHERE id=?",
            (int(settlement_id),),
        ).fetchone()
        if not row:
            raise KeyError("warehouse partner settlement not found")
        settlement = dict(row)

        if settlement["status"] in {"open", "settled"}:
            existing = _ledger_by_key(
                c, f"PARTNER_ACCRUAL:{int(settlement_id)}"
            )
            if existing:
                c.rollback()
                return get(db_file, settlement_id)
        if settlement["status"] != "ready":
            raise ValueError("settlement is not ready for accrual")

        mode = settlement["settlement_mode"]
        amount = int(settlement["partner_amount_rub"] or 0)
        if mode == "payable":
            signed = -amount
            event_type = "ACCRUAL_PAYABLE"
            description = (
                f"Начислено к оплате складу по заказу {settlement['order_id']}"
            )
        elif mode == "receivable":
            signed = amount
            event_type = "ACCRUAL_RECEIVABLE"
            description = (
                f"Начислено к получению от склада по заказу "
                f"{settlement['order_id']}"
            )
        else:
            raise ValueError("settlement mode does not create an accrual")

        _insert_ledger_conn(
            c,
            settlement=settlement,
            event_type=event_type,
            amount_rub=signed,
            description=description,
            reference_type="settlement",
            reference_id=str(settlement_id),
            actor=actor,
            idempotency_key=f"PARTNER_ACCRUAL:{int(settlement_id)}",
        )
        now = _now()
        c.execute(
            """UPDATE warehouse_partner_settlements
               SET status='open',accrued_at=COALESCE(accrued_at,?),
                   settled_at=NULL,updated_at=?
               WHERE id=?""",
            (now, now, int(settlement_id)),
        )
        _event_conn(
            c,
            settlement_id,
            "accrued",
            actor,
            {"amount_rub": signed, "mode": mode},
        )
        c.commit()
    return get(db_file, settlement_id)


def record_payment(
    db_file,
    settlement_id: int,
    *,
    direction: str,
    amount_rub: int,
    payment_ref: str,
    actor: str,
) -> dict:
    direction = str(direction or "").strip().lower()
    if direction not in PAYMENT_DIRECTIONS:
        raise ValueError("invalid partner payment direction")
    amount = int(amount_rub or 0)
    if amount <= 0:
        raise ValueError("payment amount must be positive")
    payment_ref = str(payment_ref or "").strip()
    actor = str(actor or "").strip()
    if not payment_ref:
        raise ValueError("payment_ref required")
    if not actor:
        raise ValueError("actor required")

    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM warehouse_partner_settlements WHERE id=?",
            (int(settlement_id),),
        ).fetchone()
        if not row:
            raise KeyError("warehouse partner settlement not found")
        settlement = dict(row)

        key = f"PARTNER_PAYMENT:{direction}:{payment_ref}"
        existing = _ledger_by_key(c, key)
        if existing:
            if int(existing["settlement_id"]) != int(settlement_id):
                raise ValueError("partner payment reference conflict")
            c.rollback()
            return get(db_file, settlement_id)

        current = _balance_conn(c, settlement_id)
        if current == 0:
            raise ValueError("settlement balance is already zero")
        if current < 0:
            if direction != "to_partner":
                raise ValueError("negative balance requires payment to partner")
            if amount > abs(current):
                raise ValueError("payment exceeds payable balance")
            signed = amount
            event_type = "PAYMENT_TO_PARTNER"
        else:
            if direction != "from_partner":
                raise ValueError(
                    "positive balance requires payment from partner"
                )
            if amount > current:
                raise ValueError("payment exceeds receivable balance")
            signed = -amount
            event_type = "PAYMENT_FROM_PARTNER"

        _insert_ledger_conn(
            c,
            settlement=settlement,
            event_type=event_type,
            amount_rub=signed,
            description=f"Платёж по взаиморасчёту {payment_ref}",
            reference_type="payment",
            reference_id=payment_ref,
            actor=actor,
            idempotency_key=key,
        )
        new_balance = current + signed
        now = _now()
        new_status = "settled" if new_balance == 0 else "open"
        c.execute(
            """UPDATE warehouse_partner_settlements
               SET status=?,settled_at=CASE WHEN ?='settled' THEN ? ELSE NULL END,
                   updated_at=?
               WHERE id=?""",
            (
                new_status,
                new_status,
                now,
                now,
                int(settlement_id),
            ),
        )
        _event_conn(
            c,
            settlement_id,
            "payment_recorded",
            actor,
            {
                "direction": direction,
                "amount_rub": amount,
                "payment_ref": payment_ref,
                "balance_before": current,
                "balance_after": new_balance,
            },
        )
        c.commit()
    return get(db_file, settlement_id)


def record_adjustment(
    db_file,
    settlement_id: int,
    *,
    signed_amount_rub: int,
    reason: str,
    reference_id: str,
    actor: str,
) -> dict:
    signed = int(signed_amount_rub or 0)
    if signed == 0:
        raise ValueError("adjustment must be non-zero")
    reason = str(reason or "").strip()
    reference_id = str(reference_id or "").strip()
    actor = str(actor or "").strip()
    if not reason or not reference_id or not actor:
        raise ValueError("reason, reference_id and actor required")

    init(db_file)
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        row = c.execute(
            "SELECT * FROM warehouse_partner_settlements WHERE id=?",
            (int(settlement_id),),
        ).fetchone()
        if not row:
            raise KeyError("warehouse partner settlement not found")
        settlement = dict(row)
        if settlement["status"] not in {"open", "settled"}:
            raise ValueError("adjustment requires an accrued settlement")

        key = f"PARTNER_ADJUSTMENT:{reference_id}"
        existing = _ledger_by_key(c, key)
        if existing:
            if (
                int(existing["settlement_id"]) != int(settlement_id)
                or int(existing["amount_rub"]) != signed
            ):
                raise ValueError("partner adjustment reference conflict")
            c.rollback()
            return get(db_file, settlement_id)

        before = _balance_conn(c, settlement_id)
        _insert_ledger_conn(
            c,
            settlement=settlement,
            event_type="ADJUSTMENT",
            amount_rub=signed,
            description=reason,
            reference_type="adjustment",
            reference_id=reference_id,
            actor=actor,
            idempotency_key=key,
        )
        after = before + signed
        now = _now()
        status = "settled" if after == 0 else "open"
        c.execute(
            """UPDATE warehouse_partner_settlements
               SET status=?,settled_at=CASE WHEN ?='settled' THEN ? ELSE NULL END,
                   updated_at=?
               WHERE id=?""",
            (status, status, now, now, int(settlement_id)),
        )
        _event_conn(
            c,
            settlement_id,
            "adjustment_recorded",
            actor,
            {
                "signed_amount_rub": signed,
                "reason": reason,
                "reference_id": reference_id,
                "balance_before": before,
                "balance_after": after,
            },
        )
        c.commit()
    return get(db_file, settlement_id)


def list_settlements(db_file, *, warehouse_id: int | None = None) -> list[dict]:
    where = ""
    params = []
    if warehouse_id is not None:
        where = "WHERE s.warehouse_id=?"
        params.append(int(warehouse_id))
    with sqlite3.connect(db_file) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            f"""SELECT s.*,w.public_name AS warehouse_public_name,
                       oi.oem,oi.name,oi.quantity,
                       COALESCE((
                         SELECT SUM(l.amount_rub)
                         FROM warehouse_partner_ledger l
                         WHERE l.settlement_id=s.id
                       ),0) AS balance_rub
                FROM warehouse_partner_settlements s
                LEFT JOIN warehouses w ON w.id=s.warehouse_id
                LEFT JOIN order_items oi ON oi.id=s.order_item_id
                {where}
                ORDER BY s.updated_at DESC,s.id DESC""",
            params,
        ).fetchall()
        return [dict(x) for x in rows]


def warehouse_summary(db_file) -> list[dict]:
    with sqlite3.connect(db_file) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT w.id AS warehouse_id,w.public_name,
                      COALESCE((
                        SELECT SUM(l.amount_rub)
                        FROM warehouse_partner_ledger l
                        WHERE l.warehouse_id=w.id
                      ),0) AS balance_rub,
                      COALESCE((
                        SELECT COUNT(*)
                        FROM warehouse_partner_settlements s
                        WHERE s.warehouse_id=w.id
                          AND s.status='pending_terms'
                      ),0) AS pending_terms,
                      COALESCE((
                        SELECT COUNT(*)
                        FROM warehouse_partner_settlements s
                        WHERE s.warehouse_id=w.id
                          AND s.status='open'
                      ),0) AS open_settlements
               FROM warehouses w
               WHERE w.deleted_at IS NULL
               ORDER BY w.priority,w.public_name"""
        ).fetchall()
        return [dict(x) for x in rows]


def ledger(db_file, *, warehouse_id: int | None = None, limit: int = 200):
    limit = max(1, min(int(limit), 1000))
    where = ""
    params: list[Any] = []
    if warehouse_id is not None:
        where = "WHERE l.warehouse_id=?"
        params.append(int(warehouse_id))
    params.append(limit)
    with sqlite3.connect(db_file) as c:
        c.row_factory = sqlite3.Row
        rows = c.execute(
            f"""SELECT l.*,w.public_name AS warehouse_public_name
                FROM warehouse_partner_ledger l
                LEFT JOIN warehouses w ON w.id=l.warehouse_id
                {where}
                ORDER BY l.id DESC
                LIMIT ?""",
            params,
        ).fetchall()
        return [dict(x) for x in rows]
