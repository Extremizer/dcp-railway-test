#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Thin client/RUB policy adapter over the proven FinanceEngine.

The adapter deliberately does not reproduce dealer finance math and does not
calculate customer prices. It only freezes the client policy around the exact
FinanceEngine snapshot used by COMMON FINANCE.

Not integrated with production checkout yet.
"""

from __future__ import annotations

from decimal import Decimal
import sqlite3
from typing import Iterable

from common_finance_contract import (
    CLIENT_POLICY,
    LineFinanceSnapshot,
    client_usa_financially_ready,
    requires_client_wallet_charge,
)
from oemixibot_finance import FinanceEngine, money


CLIENT_BUYER_TYPE = CLIENT_POLICY.buyer_type
CLIENT_CURRENCY = CLIENT_POLICY.wallet_currency


class ClientFinanceAdapter:
    """Client-only facade that pins FinanceEngine to buyer_type=client/RUB."""

    def __init__(self, engine: FinanceEngine):
        if not isinstance(engine, FinanceEngine):
            raise TypeError("engine must be FinanceEngine")
        self.engine = engine
        self.db_path = engine.db_path

    def _db(self):
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        return c

    @staticmethod
    def _require_text(value, name: str) -> str:
        value = str(value or "").strip()
        if not value:
            raise ValueError(f"{name} required")
        return value

    @staticmethod
    def _same_id(left, right) -> bool:
        return str(left) == str(right)

    @staticmethod
    def _event_by_key(conn, idempotency_key: str):
        return conn.execute(
            "SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount,"
            "reference_type,reference_id,order_id,reason,idempotency_key,"
            "payment_method,source_amount,source_currency,fx_code,fx_rate "
            "FROM finance_ledger WHERE idempotency_key=?",
            (idempotency_key,),
        ).fetchone()

    def _validate_existing_event(
        self,
        row,
        *,
        buyer_id,
        event_type: str,
        amount,
        reference_type: str,
        reference_id,
        order_id=None,
    ) -> int:
        if row is None:
            raise ValueError("idempotency event not found")
        expected = money(amount)
        same = (
            row["buyer_type"] == CLIENT_BUYER_TYPE
            and self._same_id(row["buyer_id"], buyer_id)
            and row["wallet_currency"] == CLIENT_CURRENCY
            and row["event_type"] == event_type
            and money(row["amount"]) == expected
            and row["reference_type"] == reference_type
            and self._same_id(row["reference_id"], reference_id)
            and (
                (row["order_id"] is None and order_id is None)
                or self._same_id(row["order_id"], order_id)
            )
        )
        if not same:
            raise ValueError("idempotency key conflict")
        return int(row["id"])

    def balance(self, client_id):
        return self.engine.balance(
            client_id,
            buyer_type=CLIENT_BUYER_TYPE,
            currency=CLIENT_CURRENCY,
        )

    def usa_financially_ready(self, client_id) -> bool:
        return client_usa_financially_ready(self.balance(client_id))

    def history(self, client_id, *, from_at=None, to_at=None, limit=100):
        return self.engine.history(
            client_id,
            buyer_type=CLIENT_BUYER_TYPE,
            currency=CLIENT_CURRENCY,
            from_at=from_at,
            to_at=to_at,
            limit=limit,
        )

    def event(self, event_id, client_id):
        return self.engine.event(
            event_id,
            client_id,
            buyer_type=CLIENT_BUYER_TYPE,
            currency=CLIENT_CURRENCY,
        )

    def order_charge_total(self, order_id: str, lines: Iterable[LineFinanceSnapshot]):
        order_id = self._require_text(order_id, "order_id")
        total = Decimal("0.00")
        seen = False
        for line in lines:
            seen = True
            if not isinstance(line, LineFinanceSnapshot):
                raise TypeError("lines must contain LineFinanceSnapshot")
            if line.order_id != order_id:
                raise ValueError("line order_id mismatch")
            if isinstance(line.amount_rub, bool) or not isinstance(line.amount_rub, int):
                raise ValueError("amount_rub must be integer RUB")
            if line.amount_rub < 0:
                raise ValueError("amount_rub must be >= 0")
            if requires_client_wallet_charge(line.payment_route):
                total += Decimal(line.amount_rub)
        if not seen:
            raise ValueError("at least one line required")
        return money(total)

    def post_order_charge(
        self,
        *,
        client_id,
        order_id: str,
        lines: Iterable[LineFinanceSnapshot],
        actor: str,
        conn=None,
    ):
        """Post one idempotent client ORDER_CHARGE from persisted line facts.

        When conn is supplied, transaction ownership stays with the caller so a
        future checkout can persist order + line snapshots + charge atomically.
        A zero chargeable total (e.g. all direct_partner) creates no ledger row.
        """

        client_id = self._require_text(client_id, "client_id")
        order_id = self._require_text(order_id, "order_id")
        actor = self._require_text(actor, "actor")
        total = self.order_charge_total(order_id, lines)
        if total == Decimal("0.00"):
            return None

        key = f"ORDER_CHARGE:order:{order_id}"
        charge_amount = -total
        own = conn is None
        c = self._db() if own else conn
        try:
            if own:
                c.execute("BEGIN IMMEDIATE")
            existing = self._event_by_key(c, key)
            if existing is not None:
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type="ORDER_CHARGE",
                    amount=charge_amount,
                    reference_type="order",
                    reference_id=order_id,
                    order_id=order_id,
                )
                if own:
                    c.commit()
                return event_id
            try:
                event_id = self.engine.post_event(
                    buyer_type=CLIENT_BUYER_TYPE,
                    buyer_id=client_id,
                    currency=CLIENT_CURRENCY,
                    event_type="ORDER_CHARGE",
                    amount=charge_amount,
                    description=f"Списание за заказ {order_id}",
                    reference_type="order",
                    reference_id=order_id,
                    order_id=order_id,
                    actor=actor,
                    idempotency_key=key,
                    conn=c,
                )
            except ValueError as exc:
                if str(exc) != "duplicate idempotency_key":
                    raise
                existing = self._event_by_key(c, key)
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type="ORDER_CHARGE",
                    amount=charge_amount,
                    reference_type="order",
                    reference_id=order_id,
                    order_id=order_id,
                )
            if own:
                c.commit()
            return event_id
        except Exception:
            if own:
                c.rollback()
            raise
        finally:
            if own:
                c.close()

    def top_up(
        self,
        *,
        client_id,
        amount_rub,
        payment_id: str,
        actor: str,
        reason: str,
        payment_method: str,
    ):
        """Top up the RUB wallet. RUB->RUB is snapshotted at 1:1."""

        client_id = self._require_text(client_id, "client_id")
        payment_id = self._require_text(payment_id, "payment_id")
        actor = self._require_text(actor, "actor")
        reason = self._require_text(reason, "reason")
        payment_method = self._require_text(payment_method, "payment_method")
        amount = money(amount_rub)
        if amount <= 0:
            raise ValueError("amount_rub must be positive")
        key = f"PAYMENT:payment:{payment_id}"

        with self._db() as c:
            existing = self._event_by_key(c, key)
            if existing is not None:
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type="PAYMENT",
                    amount=amount,
                    reference_type="payment",
                    reference_id=payment_id,
                )
                if not (
                    existing["reason"] == reason
                    and existing["payment_method"] == payment_method
                    and money(existing["source_amount"]) == amount
                    and existing["source_currency"] == "RUB"
                    and existing["fx_code"] == "RUB_RUB"
                    and str(existing["fx_rate"]) == "1"
                ):
                    raise ValueError("idempotency key conflict")
                return event_id
        try:
            return self.engine.payment(
                buyer_type=CLIENT_BUYER_TYPE,
                buyer_id=client_id,
                currency=CLIENT_CURRENCY,
                amount=amount,
                payment_id=payment_id,
                actor=actor,
                reason=reason,
                payment_method=payment_method,
                source_amount=amount,
                source_currency="RUB",
                fx_code="RUB_RUB",
                fx_rate="1",
                description="Пополнение баланса",
            )
        except ValueError as exc:
            if str(exc) != "duplicate idempotency_key":
                raise
            with self._db() as c:
                existing = self._event_by_key(c, key)
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type="PAYMENT",
                    amount=amount,
                    reference_type="payment",
                    reference_id=payment_id,
                )
                if not (
                    existing["reason"] == reason
                    and existing["payment_method"] == payment_method
                    and money(existing["source_amount"]) == amount
                    and existing["source_currency"] == "RUB"
                    and existing["fx_code"] == "RUB_RUB"
                    and str(existing["fx_rate"]) == "1"
                ):
                    raise ValueError("idempotency key conflict")
                return event_id

    def adjustment(
        self,
        *,
        client_id,
        amount_rub,
        adjustment_id: str,
        actor: str,
        reason: str,
    ):
        """Create a new manual +/- balance event; never rewrite an amount."""

        client_id = self._require_text(client_id, "client_id")
        adjustment_id = self._require_text(adjustment_id, "adjustment_id")
        actor = self._require_text(actor, "actor")
        reason = self._require_text(reason, "reason")
        amount = money(amount_rub)
        if amount == 0:
            raise ValueError("amount_rub must be non-zero")
        event_type = "ADJUSTMENT_PLUS" if amount > 0 else "ADJUSTMENT_MINUS"
        key = f"ADJUSTMENT:adjustment:{adjustment_id}"

        with self._db() as c:
            existing = self._event_by_key(c, key)
            if existing is not None:
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type=event_type,
                    amount=amount,
                    reference_type="adjustment",
                    reference_id=adjustment_id,
                )
                if existing["reason"] != reason:
                    raise ValueError("idempotency key conflict")
                return event_id
        try:
            return self.engine.adjustment(
                buyer_type=CLIENT_BUYER_TYPE,
                buyer_id=client_id,
                currency=CLIENT_CURRENCY,
                amount=amount,
                adjustment_id=adjustment_id,
                actor=actor,
                reason=reason,
            )
        except ValueError as exc:
            if str(exc) != "duplicate idempotency_key":
                raise
            with self._db() as c:
                existing = self._event_by_key(c, key)
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type=event_type,
                    amount=amount,
                    reference_type="adjustment",
                    reference_id=adjustment_id,
                )
                if existing["reason"] != reason:
                    raise ValueError("idempotency key conflict")
                return event_id

    def refund(
        self,
        *,
        client_id,
        amount_rub,
        refund_id: str,
        source_event_id,
        actor: str,
        reason: str,
    ):
        """Manual client refund against one refundable debit event."""

        client_id = self._require_text(client_id, "client_id")
        refund_id = self._require_text(refund_id, "refund_id")
        actor = self._require_text(actor, "actor")
        reason = self._require_text(reason, "reason")
        amount = money(amount_rub)
        if amount <= 0:
            raise ValueError("amount_rub must be positive")
        key = f"REFUND:refund:{refund_id}"

        with self._db() as c:
            existing = self._event_by_key(c, key)
            if existing is not None:
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type="REFUND",
                    amount=amount,
                    reference_type="ledger_event",
                    reference_id=str(source_event_id),
                )
                if existing["reason"] != reason:
                    raise ValueError("idempotency key conflict")
                return event_id
        try:
            return self.engine.refund(
                buyer_type=CLIENT_BUYER_TYPE,
                buyer_id=client_id,
                currency=CLIENT_CURRENCY,
                amount=amount,
                refund_id=refund_id,
                source_event_id=source_event_id,
                actor=actor,
                reason=reason,
            )
        except ValueError as exc:
            if str(exc) != "duplicate idempotency_key":
                raise
            with self._db() as c:
                existing = self._event_by_key(c, key)
                event_id = self._validate_existing_event(
                    existing,
                    buyer_id=client_id,
                    event_type="REFUND",
                    amount=amount,
                    reference_type="ledger_event",
                    reference_id=str(source_event_id),
                )
                if existing["reason"] != reason:
                    raise ValueError("idempotency key conflict")
                return event_id

    def ensure_reason_audit_schema(self):
        with self._db() as c:
            c.execute(
                """CREATE TABLE IF NOT EXISTS finance_reason_audit(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_event_id INTEGER NOT NULL,
                    old_reason TEXT,
                    new_reason TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY(source_event_id) REFERENCES finance_ledger(id)
                )"""
            )

    def edit_manual_reason(
        self,
        *,
        client_id,
        source_event_id,
        new_reason: str,
        actor: str,
        idempotency_key: str,
    ):
        """Edit only reason text for PAYMENT/manual +/- and append an audit row."""

        client_id = self._require_text(client_id, "client_id")
        new_reason = self._require_text(new_reason, "new_reason")
        actor = self._require_text(actor, "actor")
        key = self._require_text(idempotency_key, "idempotency_key")
        try:
            source_event_id = int(source_event_id)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid source_event_id") from exc
        if source_event_id < 1:
            raise ValueError("invalid source_event_id")

        self.ensure_reason_audit_schema()
        c = self._db()
        try:
            c.execute("BEGIN IMMEDIATE")
            prior = c.execute(
                "SELECT id,source_event_id,new_reason FROM finance_reason_audit "
                "WHERE idempotency_key=?",
                (key,),
            ).fetchone()
            if prior is not None:
                if (
                    int(prior["source_event_id"]) != source_event_id
                    or prior["new_reason"] != new_reason
                ):
                    raise ValueError("idempotency key conflict")
                c.commit()
                return int(prior["id"])

            row = c.execute(
                "SELECT id,buyer_type,buyer_id,wallet_currency,event_type,amount,"
                "description,reason,created_at FROM finance_ledger WHERE id=?",
                (source_event_id,),
            ).fetchone()
            if row is None:
                raise ValueError("source event not found")
            if (
                row["buyer_type"] != CLIENT_BUYER_TYPE
                or not self._same_id(row["buyer_id"], client_id)
                or row["wallet_currency"] != CLIENT_CURRENCY
            ):
                raise ValueError("source event owner/currency mismatch")
            if row["event_type"] not in {
                "PAYMENT",
                "ADJUSTMENT_PLUS",
                "ADJUSTMENT_MINUS",
            }:
                raise ValueError("source event reason is not editable")
            old_reason = row["reason"]
            if str(old_reason or "") == new_reason:
                raise ValueError("reason unchanged")

            cur = c.execute(
                "INSERT INTO finance_reason_audit("
                "source_event_id,old_reason,new_reason,actor,idempotency_key,created_at"
                ") VALUES(?,?,?,?,?,strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (source_event_id, old_reason, new_reason, actor, key),
            )
            c.execute(
                "UPDATE finance_ledger SET reason=? WHERE id=?",
                (new_reason, source_event_id),
            )
            c.commit()
            return cur.lastrowid
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()


__all__ = ["ClientFinanceAdapter"]
