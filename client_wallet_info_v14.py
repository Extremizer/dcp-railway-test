#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Zero-impact client wallet history events for direct warehouse money flow.

Public calls keep legacy self-owned transactions. Passing conn delegates all
transaction ownership to the caller so Internal payment state, partner balance
and the informational client event can share one commit or rollback.
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


def _connection(db_file, conn):
    own = conn is None
    if own:
        ensure_client_finance_schema(db_file)
        c = sqlite3.connect(db_file, timeout=30)
        c.row_factory = sqlite3.Row
    else:
        c = conn
    return c, own


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
    conn=None,
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
    effective_subject = str(effective_subject or "") or None
    key = f"{event_type}:{reference_id}"
    reason = (
        f"warehouse_id={warehouse_id};"
        f"effective_subject={effective_subject or ''}"
    )

    c, own = _connection(db_file, conn)
    ok = False
    try:
        if own:
            c.execute("BEGIN IMMEDIATE")
        prior = c.execute(
            """SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount,
                      description,reference_type,reference_id,order_id,actor,
                      reason,idempotency_key,source_amount,source_currency,
                      fx_code,fx_rate
               FROM finance_ledger WHERE idempotency_key=?""",
            (key,),
        ).fetchone()
        if prior:
            same = (
                prior[1] == "client"
                and str(prior[2]) == client_id
                and prior[3] == "RUB"
                and prior[4] == event_type
                and Decimal(str(prior[5])) == Decimal("0")
                and str(prior[6]) == description
                and str(prior[7]) == "warehouse_direct_money_flow"
                and str(prior[8]) == reference_id
                and str(prior[9]) == order_id
                and str(prior[10]) == actual_actor
                and str(prior[11]) == reason
                and Decimal(str(prior[13])) == external
                and prior[14] == "RUB"
                and prior[15] == "RUB_RUB"
                and str(prior[16]) == "1"
            )
            if not same:
                raise ValueError("informational idempotency conflict")
            ok = True
            return int(prior[0])

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
        ok = True
        return event_id
    finally:
        if own:
            try:
                c.commit() if ok else c.rollback()
            finally:
                c.close()


def record_direct_payment(db_file, **kwargs):
    return record(db_file, event_type=DIRECT_PAYMENT_INFO, **kwargs)


def record_direct_refund(db_file, **kwargs):
    return record(db_file, event_type=DIRECT_REFUND_INFO, **kwargs)
