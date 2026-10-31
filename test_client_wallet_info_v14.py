"""Regression for zero-impact direct warehouse payment/refund history."""
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from client_finance_schema import ensure_client_finance_schema
from oemixibot_finance import FinanceEngine
import client_wallet_info_v14 as info


@contextmanager
def close_disposable_sqlite_connections(db_path):
    """Close connections to this disposable DB before TemporaryDirectory exits."""
    original_connect = sqlite3.connect
    active_connections = set()

    class DisposableConnection(sqlite3.Connection):
        def close(self):
            super().close()
            active_connections.discard(self)

    def tracked_connect(database, *args, **kwargs):
        if str(database) != str(db_path):
            return original_connect(database, *args, **kwargs)
        if "factory" in kwargs:
            raise AssertionError("unexpected disposable SQLite connection factory")
        connection = original_connect(
            database, *args, factory=DisposableConnection, **kwargs
        )
        active_connections.add(connection)
        return connection

    with patch.object(sqlite3, "connect", tracked_connect):
        try:
            yield
        finally:
            for connection in tuple(active_connections):
                connection.close()
            if active_connections:
                raise AssertionError("disposable SQLite connections remain open")


class ClientWalletInfoV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "test.db")
        sqlite_scope = close_disposable_sqlite_connections(self.db)
        sqlite_scope.__enter__()
        self.addCleanup(sqlite_scope.__exit__, None, None, None)
        ensure_client_finance_schema(self.db)
        self.engine = FinanceEngine(self.db)

    def test_direct_payment_is_visible_but_changes_balance_by_zero(self):
        event_id = info.record_direct_payment(
            self.db, client_id="c1", order_id="I12J-1", warehouse_id=1,
            external_amount_rub=19500, reference_id="wh-receipt-1",
            actual_actor="warehouse:user1", effective_subject="warehouse:1",
            description="Direct payment to partner warehouse"
        )
        self.assertEqual(self.engine.balance("c1", "client", "RUB"), Decimal("0.00"))
        row = self.engine.event(event_id, "c1", "client", "RUB")
        self.assertEqual(row["event_type"], info.DIRECT_PAYMENT_INFO)
        self.assertEqual(row["amount"], Decimal("0.00"))
        self.assertEqual(len(self.engine.history("c1", "client", "RUB")), 1)

    def test_direct_refund_is_also_zero_impact(self):
        info.record_direct_refund(
            self.db, client_id="c1", order_id="I12J-1", warehouse_id=1,
            external_amount_rub=19500, reference_id="wh-refund-1",
            actual_actor="warehouse:user1", effective_subject="warehouse:1",
            description="Direct refund from partner warehouse"
        )
        self.assertEqual(self.engine.balance("c1", "client", "RUB"), Decimal("0.00"))

    def test_replay_is_idempotent_and_conflict_is_rejected(self):
        kwargs = dict(
            client_id="c1", order_id="I12J-1", warehouse_id=1,
            external_amount_rub=19500, reference_id="receipt",
            actual_actor="warehouse:user1", effective_subject="warehouse:1",
            description="Direct payment to partner warehouse"
        )
        first = info.record_direct_payment(self.db, **kwargs)
        second = info.record_direct_payment(self.db, **kwargs)
        self.assertEqual(first, second)
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            info.record_direct_payment(
                self.db, **dict(kwargs, external_amount_rub=20000)
            )
        self.assertEqual(len(self.engine.history("c1", "client", "RUB")), 1)

    def test_real_extremizer_wallet_movements_remain_real(self):
        self.engine.post_event(
            buyer_type="client", buyer_id="c1", currency="RUB",
            event_type="PAYMENT", amount=1000, description="real payment",
            reference_type="payment", reference_id="p1", actor="admin",
            idempotency_key="PAYMENT:p1"
        )
        self.engine.post_event(
            buyer_type="client", buyer_id="c1", currency="RUB",
            event_type="ORDER_CHARGE", amount=-600, description="real charge",
            reference_type="order", reference_id="I12J-1", order_id="I12J-1",
            actor="system", idempotency_key="ORDER_CHARGE:I12J-1"
        )
        info.record_direct_payment(
            self.db, client_id="c1", order_id="I12J-2", warehouse_id=1,
            external_amount_rub=500, reference_id="receipt-2",
            actual_actor="warehouse:user1",
            description="Direct payment to warehouse"
        )
        self.assertEqual(self.engine.balance("c1", "client", "RUB"), Decimal("400.00"))


if __name__ == "__main__":
    unittest.main()
