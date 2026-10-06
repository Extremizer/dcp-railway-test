#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Atomic client checkout persistence orchestration.

This module owns only the SQLite transaction that joins order persistence with
the client wallet charge. It intentionally does not send Telegram messages,
clear carts, create warehouse reservations, or call external services.
"""

from __future__ import annotations

import sqlite3

from client_finance_adapter import ClientFinanceAdapter
from client_order_finance_bridge import post_persisted_order_charge


def _require_text(value, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def persist_client_checkout_atomic(
    *,
    db_path,
    save_order_fn,
    finance_adapter: ClientFinanceAdapter,
    order_id: str,
    user,
    cart: dict,
    delivery_preference: str | None = None,
    origin: str = "telegram",
    actor: str = "checkout",
):
    """Persist one client order and its wallet charge in one transaction.

    Transaction ownership lives here:
      BEGIN IMMEDIATE
        -> save_order_fn(..., conn=conn)
        -> finance bridge from persisted line snapshots
      COMMIT

    Any exception rolls back both order persistence and finance writes.

    External side effects intentionally remain outside this function.
    """

    if not callable(save_order_fn):
        raise TypeError("save_order_fn must be callable")
    if not isinstance(finance_adapter, ClientFinanceAdapter):
        raise TypeError("finance_adapter must be ClientFinanceAdapter")

    order_id = _require_text(order_id, "order_id")
    actor = _require_text(actor, "actor")
    if user is None or not hasattr(user, "id"):
        raise ValueError("user.id required")
    client_id = _require_text(user.id, "user.id")
    if not isinstance(cart, dict) or not cart:
        raise ValueError("cart must be a non-empty dict")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        conn.execute("BEGIN IMMEDIATE")

        save_order_fn(
            order_id,
            user,
            cart,
            delivery_preference=delivery_preference,
            origin=origin,
            conn=conn,
        )

        finance_event_id = post_persisted_order_charge(
            finance_adapter,
            conn=conn,
            client_id=client_id,
            order_id=order_id,
            actor=actor,
        )

        conn.commit()
        return {
            "order_id": order_id,
            "client_id": client_id,
            "finance_event_id": finance_event_id,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = ["persist_client_checkout_atomic"]
