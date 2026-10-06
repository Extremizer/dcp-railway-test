#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Bridge persisted client order snapshots into COMMON FINANCE.

This module does not calculate customer prices. It only converts already
persisted order_items facts into LineFinanceSnapshot objects and delegates the
wallet mutation to ClientFinanceAdapter.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from client_finance_adapter import ClientFinanceAdapter
from common_finance_contract import LineFinanceSnapshot, PaymentRoute


def _require_text(value, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def _integer_snapshot(value, name: str, *, positive: bool = False) -> int:
    if value is None:
        raise ValueError(f"missing {name} snapshot")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid {name} snapshot") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"invalid {name} snapshot")
    integer = int(number)
    if positive and integer <= 0:
        raise ValueError(f"invalid {name} snapshot")
    if not positive and integer < 0:
        raise ValueError(f"invalid {name} snapshot")
    return integer


def load_order_line_finance_snapshots(conn, order_id: str):
    """Read immutable finance facts from already persisted order_items."""

    order_id = _require_text(order_id, "order_id")
    rows = conn.execute(
        """
        SELECT
            id,
            order_id,
            offer_source,
            payment_route,
            quantity,
            customer_unit_rub
        FROM order_items
        WHERE order_id = ?
        ORDER BY position, id
        """,
        (order_id,),
    ).fetchall()
    if not rows:
        raise ValueError("order has no items")

    snapshots = []
    for row in rows:
        line_order_id = str(row[1])
        if line_order_id != order_id:
            raise ValueError("line order_id mismatch")

        offer_source = str(row[2] or "").strip().lower()
        if offer_source not in {"usa", "warehouse"}:
            raise ValueError("invalid offer_source snapshot")

        try:
            payment_route = PaymentRoute(str(row[3] or "").strip())
        except ValueError as exc:
            raise ValueError("invalid payment_route snapshot") from exc

        quantity = _integer_snapshot(row[4], "quantity", positive=True)
        customer_unit_rub = _integer_snapshot(
            row[5],
            "customer_unit_rub",
            positive=False,
        )

        snapshots.append(
            LineFinanceSnapshot(
                order_id=order_id,
                order_item_id=int(row[0]),
                offer_source=offer_source,
                payment_route=payment_route,
                amount_rub=customer_unit_rub * quantity,
            )
        )

    return snapshots


def post_persisted_order_charge(
    adapter: ClientFinanceAdapter,
    *,
    conn,
    client_id,
    order_id: str,
    actor: str,
):
    """Charge one persisted order inside the caller-owned transaction."""

    if not isinstance(adapter, ClientFinanceAdapter):
        raise TypeError("adapter must be ClientFinanceAdapter")

    order_id = _require_text(order_id, "order_id")
    client_id = _require_text(client_id, "client_id")
    actor = _require_text(actor, "actor")

    owner = conn.execute(
        "SELECT telegram_user_id FROM orders WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if owner is None:
        raise ValueError("order not found")
    if str(owner[0]) != client_id:
        raise ValueError("order owner mismatch")

    lines = load_order_line_finance_snapshots(conn, order_id)
    return adapter.post_order_charge(
        client_id=client_id,
        order_id=order_id,
        lines=lines,
        actor=actor,
        conn=conn,
    )


__all__ = [
    "load_order_line_finance_snapshots",
    "post_persisted_order_charge",
]
