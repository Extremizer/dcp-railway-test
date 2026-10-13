#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-impact client wallet history events for direct warehouse money flow.

These rows are informational only. They use the existing shared finance_ledger
so existing history readers can display them, but amount is always exactly 0
and therefore cannot change the client RUB wallet balance.
"""
from __future__ import annotations

from decimal import Decimal
import sqlite3

from client_finance_schema import ensure_client_finance_schema


DIRECT_PAYMENT_INFO = "INFO_DIRECT_WAREHOUSE_PAYMENT"
DIRECT_REFUND_INFO = "INFO_DIRECT_WAREHOUSE_REFUND"
_ALLOWED = {DIRECT_PAYMENT_INFO, DIRECT_REFUND_INFO}


def _text(value, name):
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def record(
    db_file,
    *,
    client_id,
    order_id,
    warehouse_id,
    event_type,
    external_amount_rub,
    reference_id,
    actual_actor,
    description,
    effective_subject=None,
):
    client_id = _text(client_id, "client_id")
    order_id = _text(order_id, "order_id")
    reference_id = _text(reference_id, "reference_id")
    actual_actor = _text(actual_actor, "actual_actor")
    description = _text(description, "description")
    event_type = _text(event_type, "event_type")
    if event_type not in _ALLOWED:
        raise ValueError("invalid informational event_type")
    warehouse_id = int(warehouse_id)
    external = Decimal(str(external_amount_rub))
    if not external.is_finite() or external <= 0:
        raise ValueError("external_amount_rub must be positive")
    ensure_client_finance_schema(db_file)
    key = f"{event_type}:{reference_id}"
    reason = (
        f"warehouse_id={warehouse_id};"
        f"effective_subject={str(effective_subject or '')}"
    )
    with sqlite3.connect(db_file, timeout=30) as c:
        c.row_factory = sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        prior = c.execute(
            "SELECT * FROM finance_ledger WHERE idempotency_key=?", (key,)
        ).fetchone()
        if prior:
            same = (
                prior["buyer_type"] == "client"
                and str(prior["buyer_id"]) == client_id
                and prior["wallet_currency"] == "RUB"
                and prior["event_type"] == event_type
                and Decimal(str(prior["amount"])) == Decimal("0")
                and str(prior["order_id"]) == order_id
                and str(prior["reference_id"]) == reference_id
                and Decimal(str(prior["source_amount"])) == external
                and prior["source_currency"] == "RUB"
            )
            if not same:
                raise ValueError("informational idempotency conflict")
            c.rollback()
            return int(prior["id"])

        cur = c.execute(
            """INSERT INTO finance_ledger(
                 buyer_type,buyer_id,wallet_currency,event_type,amount,
                 description,reference_type,reference_id,order_id,
                 actor,reason,idempotency_key,created_at,
                 source_amount,source_currency,fx_code,fx_rate)
               VALUES('client',?,'RUB',?,'0.00',?,
                      'warehouse_direct_money_flow',?,?,?,
                      ?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'),
                      ?,'RUB','RUB_RUB','1')""",
            (
                client_id,
                event_type,
                description,
                reference_id,
                order_id,
                actual_actor,
                reason,
                key,
                str(external),
            ),
        )
        event_id = int(cur.lastrowid)
        c.commit()
        return event_id


def record_direct_payment(db_file, **kwargs):
    return record(db_file, event_type=DIRECT_PAYMENT_INFO, **kwargs)


def record_direct_refund(db_file, **kwargs):
    return record(db_file, event_type=DIRECT_REFUND_INFO, **kwargs)
