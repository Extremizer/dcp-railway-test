#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Disposable regression for client_finance_adapter.py.

Runs only on a temporary SQLite DB. It must never point at production data.
"""

from __future__ import annotations

from decimal import Decimal
import hashlib
from pathlib import Path
import sqlite3
import tempfile

from client_finance_adapter import ClientFinanceAdapter
from common_finance_contract import LineFinanceSnapshot, PaymentRoute
from oemixibot_finance import FinanceEngine

HERE = Path(__file__).resolve().parent
ENGINE = HERE / "oemixibot_finance.py"
PROVEN_ENGINE_SHA256 = "3380ce87a75201b79f93c469b8517c9705c58633294a3a15d6f1c70a9f9c3981"


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def schema(db_path):
    with sqlite3.connect(db_path) as c:
        c.executescript(
            """
            PRAGMA foreign_keys=ON;
            CREATE TABLE finance_ledger(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                buyer_type TEXT NOT NULL,
                buyer_id TEXT NOT NULL,
                wallet_currency TEXT NOT NULL,
                event_type TEXT NOT NULL,
                amount TEXT NOT NULL,
                description TEXT NOT NULL,
                reference_type TEXT NOT NULL,
                reference_id TEXT NOT NULL,
                order_id TEXT,
                item_slice_id INTEGER,
                arrival_id INTEGER,
                actor TEXT NOT NULL,
                reason TEXT,
                idempotency_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                payment_method TEXT,
                source_amount TEXT,
                source_currency TEXT,
                fx_code TEXT,
                fx_rate TEXT
            );
            CREATE TABLE disposable_orders(
                order_id TEXT PRIMARY KEY,
                note TEXT
            );
            """
        )


def ledger_rows(db_path):
    with sqlite3.connect(db_path) as c:
        c.row_factory = sqlite3.Row
        return [dict(r) for r in c.execute("SELECT * FROM finance_ledger ORDER BY id")]


def run():
    before_sha = hashlib.sha256(ENGINE.read_bytes()).hexdigest()
    require(before_sha == PROVEN_ENGINE_SHA256, "proven FinanceEngine SHA mismatch before")

    with tempfile.TemporaryDirectory(prefix="client-finance-") as td:
        db = Path(td) / "finance.db"
        schema(db)
        engine = FinanceEngine(str(db))
        client = ClientFinanceAdapter(engine)
        cid = "client-7005635854"

        # 1. Empty wallet: balance zero and USA blocked.
        require(client.balance(cid) == Decimal("0.00"), "initial balance")
        require(client.usa_financially_ready(cid) is False, "zero balance must block USA")

        # 2. Mixed order: only extremizer_balance lines debit the wallet.
        lines = [
            LineFinanceSnapshot("E06J-1001", 1, "usa", PaymentRoute.EXTREMIZER_BALANCE, 10000),
            LineFinanceSnapshot("E06J-1001", 2, "warehouse", PaymentRoute.EXTREMIZER_BALANCE, 5000),
            LineFinanceSnapshot("E06J-1001", 3, "warehouse", PaymentRoute.DIRECT_PARTNER, 7000),
        ]
        require(client.order_charge_total("E06J-1001", lines) == Decimal("15000.00"), "mixed total")
        charge_id = client.post_order_charge(
            client_id=cid,
            order_id="E06J-1001",
            lines=lines,
            actor="regression",
        )
        require(client.balance(cid) == Decimal("-15000.00"), "mixed debit")
        require(client.usa_financially_ready(cid) is False, "negative balance must block USA")

        # 3. Exact retry is idempotent; no second charge.
        retry_id = client.post_order_charge(
            client_id=cid,
            order_id="E06J-1001",
            lines=lines,
            actor="regression-retry",
        )
        require(retry_id == charge_id, "retry must return original event")
        require(len(ledger_rows(db)) == 1, "retry must not add ledger row")
        require(client.balance(cid) == Decimal("-15000.00"), "retry must not change balance")

        # 4. Same idempotency key with different financial meaning is rejected.
        conflicting = [
            LineFinanceSnapshot("E06J-1001", 1, "usa", PaymentRoute.EXTREMIZER_BALANCE, 9999),
        ]
        try:
            client.post_order_charge(
                client_id=cid,
                order_id="E06J-1001",
                lines=conflicting,
                actor="regression",
            )
        except ValueError as exc:
            require(str(exc) == "idempotency key conflict", "wrong idempotency conflict")
        else:
            raise AssertionError("conflicting retry accepted")
        require(client.balance(cid) == Decimal("-15000.00"), "conflict changed balance")

        # 5. Direct-partner-only order creates no wallet event.
        direct = [
            LineFinanceSnapshot("E06J-1002", 10, "warehouse", PaymentRoute.DIRECT_PARTNER, 8000),
        ]
        require(client.post_order_charge(
            client_id=cid,
            order_id="E06J-1002",
            lines=direct,
            actor="regression",
        ) is None, "direct partner should not debit wallet")
        require(len(ledger_rows(db)) == 1, "direct partner created ledger row")

        # 6. Future shared transaction: simulated checkout rollback removes order + charge.
        rollback_lines = [
            LineFinanceSnapshot("E06J-ROLLBACK", 20, "usa", PaymentRoute.EXTREMIZER_BALANCE, 3000),
        ]
        c = sqlite3.connect(db)
        c.row_factory = sqlite3.Row
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute("INSERT INTO disposable_orders(order_id,note) VALUES(?,?)", ("E06J-ROLLBACK", "test"))
            client.post_order_charge(
                client_id=cid,
                order_id="E06J-ROLLBACK",
                lines=rollback_lines,
                actor="regression",
                conn=c,
            )
            raise RuntimeError("simulate checkout failure")
        except RuntimeError:
            c.rollback()
        finally:
            c.close()
        with sqlite3.connect(db) as c:
            require(c.execute("SELECT COUNT(*) FROM disposable_orders WHERE order_id='E06J-ROLLBACK'").fetchone()[0] == 0, "order rollback failed")
            require(c.execute("SELECT COUNT(*) FROM finance_ledger WHERE order_id='E06J-ROLLBACK'").fetchone()[0] == 0, "charge rollback failed")
        require(client.balance(cid) == Decimal("-15000.00"), "rollback changed balance")

        # 7. RUB top-up is 1:1 and idempotent.
        payment_id = client.top_up(
            client_id=cid,
            amount_rub=16000,
            payment_id="PAY-1001",
            actor="manager",
            reason="Оплата клиента",
            payment_method="manual_rub",
        )
        require(client.balance(cid) == Decimal("1000.00"), "top-up balance")
        require(client.usa_financially_ready(cid) is True, "positive balance must release USA")
        payment_retry = client.top_up(
            client_id=cid,
            amount_rub=16000,
            payment_id="PAY-1001",
            actor="manager-retry",
            reason="Оплата клиента",
            payment_method="manual_rub",
        )
        require(payment_retry == payment_id, "payment retry id")
        require(client.balance(cid) == Decimal("1000.00"), "payment retry changed balance")
        try:
            client.top_up(
                client_id=cid,
                amount_rub=16000,
                payment_id="PAY-1001",
                actor="manager",
                reason="Другая причина",
                payment_method="manual_rub",
            )
        except ValueError as exc:
            require(str(exc) == "idempotency key conflict", "payment semantic conflict")
        else:
            raise AssertionError("payment id reused with different reason")
        with sqlite3.connect(db) as c:
            r = c.execute("SELECT payment_method,source_amount,source_currency,fx_code,fx_rate FROM finance_ledger WHERE id=?", (payment_id,)).fetchone()
        require(r == ("manual_rub", "16000.00", "RUB", "RUB_RUB", "1"), "RUB payment snapshot")

        # 8. Manual +/- corrections are new ledger events and idempotent.
        plus_id = client.adjustment(
            client_id=cid,
            amount_rub=500,
            adjustment_id="ADJ-PLUS-1",
            actor="manager",
            reason="Корректировка +",
        )
        minus_id = client.adjustment(
            client_id=cid,
            amount_rub=-200,
            adjustment_id="ADJ-MINUS-1",
            actor="manager",
            reason="Корректировка -",
        )
        require(client.balance(cid) == Decimal("1300.00"), "adjustment balance")
        require(client.adjustment(
            client_id=cid,
            amount_rub=500,
            adjustment_id="ADJ-PLUS-1",
            actor="retry",
            reason="Корректировка +",
        ) == plus_id, "adjustment retry")
        require(client.balance(cid) == Decimal("1300.00"), "adjustment retry changed balance")
        try:
            client.adjustment(
                client_id=cid,
                amount_rub=500,
                adjustment_id="ADJ-PLUS-1",
                actor="manager",
                reason="Другая причина",
            )
        except ValueError as exc:
            require(str(exc) == "idempotency key conflict", "adjustment semantic conflict")
        else:
            raise AssertionError("adjustment id reused with different reason")

        # 9. Reason edit changes only reason and appends audit; retry is safe.
        before_event = client.event(minus_id, cid)
        audit_id = client.edit_manual_reason(
            client_id=cid,
            source_event_id=minus_id,
            new_reason="Исправленная причина",
            actor="manager",
            idempotency_key="REASON-EDIT-1",
        )
        after_event = client.event(minus_id, cid)
        for field in ("amount", "event_type", "created_at", "description"):
            require(after_event[field] == before_event[field], f"reason edit changed {field}")
        require(after_event["reason"] == "Исправленная причина", "reason not changed")
        require(client.edit_manual_reason(
            client_id=cid,
            source_event_id=minus_id,
            new_reason="Исправленная причина",
            actor="manager-retry",
            idempotency_key="REASON-EDIT-1",
        ) == audit_id, "reason edit retry")
        with sqlite3.connect(db) as c:
            c.row_factory = sqlite3.Row
            a = c.execute("SELECT * FROM finance_reason_audit WHERE id=?", (audit_id,)).fetchone()
        require(a["old_reason"] == "Корректировка -", "audit old reason")
        require(a["new_reason"] == "Исправленная причина", "audit new reason")
        require(a["actor"] == "manager", "audit actor")

        # 10. Manual refund uses proven engine safeguards and is idempotent.
        refund_id = client.refund(
            client_id=cid,
            amount_rub=1000,
            refund_id="REF-1001",
            source_event_id=charge_id,
            actor="manager",
            reason="Частичный возврат",
        )
        require(client.balance(cid) == Decimal("2300.00"), "refund balance")
        require(client.refund(
            client_id=cid,
            amount_rub=1000,
            refund_id="REF-1001",
            source_event_id=charge_id,
            actor="retry",
            reason="Частичный возврат",
        ) == refund_id, "refund retry")
        require(client.balance(cid) == Decimal("2300.00"), "refund retry changed balance")
        try:
            client.refund(
                client_id=cid,
                amount_rub=1000,
                refund_id="REF-1001",
                source_event_id=charge_id,
                actor="manager",
                reason="Другая причина",
            )
        except ValueError as exc:
            require(str(exc) == "idempotency key conflict", "refund semantic conflict")
        else:
            raise AssertionError("refund id reused with different reason")

        # 11. History/event are pinned to client/RUB and expose no dealer credit path.
        hist = client.history(cid, limit=100)
        require(len(hist) == 5, f"unexpected history count: {len(hist)}")
        require(all(r["buyer_type"] == "client" and r["wallet_currency"] == "RUB" for r in hist), "history policy leak")
        require(not hasattr(client, "credit_limit"), "client adapter must not expose credit")
        require(not hasattr(client, "aging_summary"), "client adapter must not expose aging")
        require(not hasattr(client, "auto_refund"), "client AUTO_REFUND must remain closed until client slice mapping exists")

        # 12. No accidental production-ish files: temp DB vanishes with context.
        require(str(db).startswith(td), "DB is not temporary")

    after_sha = hashlib.sha256(ENGINE.read_bytes()).hexdigest()
    require(after_sha == before_sha == PROVEN_ENGINE_SHA256, "FinanceEngine changed during tests")
    print("PASS client/RUB adapter disposable regression")
    print("FinanceEngine SHA256:", after_sha)


if __name__ == "__main__":
    run()
