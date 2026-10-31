"""G1 only: disposable, offline client/RUB debit safety acceptance tests.

NOT a production initializer. Every test uses its own TemporaryDirectory
SQLite database. This file is QA preparation; execution requires QA-RUN GO.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest

from client_finance_adapter import ClientFinanceAdapter
from client_finance_schema import ensure_client_finance_schema
from common_finance_contract import LineFinanceSnapshot, PaymentRoute
from oemixibot_finance import FinanceEngine

ENGINE_FILE = Path(__file__).with_name("oemixibot_finance.py")
PROVEN_ENGINE_SHA256 = "a95a8918afbd7f4be7893ee65919061c09b3ae4c5c8cfffcaf34a37f4b795499"


class G1ClientRUBGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine_sha_before = hashlib.sha256(ENGINE_FILE.read_bytes()).hexdigest()
        if cls.engine_sha_before != PROVEN_ENGINE_SHA256:
            raise AssertionError("reviewed FinanceEngine SHA256 mismatch")

    @classmethod
    def tearDownClass(cls):
        if hashlib.sha256(ENGINE_FILE.read_bytes()).hexdigest() != cls.engine_sha_before:
            raise AssertionError("FinanceEngine changed during G1 tests")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="ext-g1-qa-only-")
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "wallet.db")
        ensure_client_finance_schema(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE dealers(id TEXT PRIMARY KEY, active INTEGER NOT NULL)")
            conn.execute("INSERT INTO dealers(id,active) VALUES ('dealer-qa',1)")
            conn.execute("INSERT INTO dealers(id,active) VALUES ('dealer-inactive',0)")
        self.engine = FinanceEngine(self.db)

    def fund(self, buyer, amount, key=None):
        payment_id = key or ("fund-" + buyer)
        return self.engine.payment(
            buyer_type="client", buyer_id=buyer, currency="RUB",
            amount=amount, payment_id=payment_id, actor="qa-fixture",
            reason="isolated disposable G1 funding", payment_method="test",
            source_amount=amount, source_currency="RUB",
            fx_code="RUB_RUB", fx_rate="1",
        )

    def charge(self, buyer="client-qa", amount="-80", key="g1-charge",
               event_type="ORDER_CHARGE", conn=None):
        return self.engine.post_event(
            buyer_type="client", buyer_id=buyer, currency="RUB",
            event_type=event_type, amount=amount,
            description="G1 disposable debit", reference_type="order",
            reference_id=key, actor="g1-qa", idempotency_key=key,
            reason="G1 adjustment test" if event_type == "ADJUSTMENT_MINUS" else None,
            conn=conn,
        )

    def debits(self, buyer):
        with sqlite3.connect(self.db) as conn:
            return conn.execute(
                "SELECT id,event_type,amount,idempotency_key FROM finance_ledger "
                "WHERE buyer_type='client' AND buyer_id=? AND "
                "wallet_currency='RUB' AND CAST(amount AS REAL)<0 ORDER BY id",
                (buyer,),
            ).fetchall()

    def balance(self, buyer):
        return self.engine.balance(buyer, "client", "RUB")

    def journal_mode(self, value):
        with sqlite3.connect(self.db) as conn:
            actual = conn.execute("PRAGMA journal_mode=" + value).fetchone()[0]
        self.assertEqual(actual.lower(), value.lower())

    def test_sufficient_exact_insufficient_and_zero_funds(self):
        cases = [
            ("sufficient", "120", "-100", True, Decimal("20.00")),
            ("exact", "100", "-100", True, Decimal("0.00")),
            ("insufficient", "99.99", "-100", False, Decimal("99.99")),
            ("zero", None, "-1", False, Decimal("0.00")),
        ]
        for label, initial, debit, allowed, remaining in cases:
            with self.subTest(case=label):
                buyer = "g1-" + label
                if initial is not None:
                    self.fund(buyer, initial)
                if allowed:
                    self.assertIsInstance(self.charge(buyer, debit, label), int)
                    self.assertEqual(len(self.debits(buyer)), 1)
                else:
                    with self.assertRaisesRegex(ValueError, "INSUFFICIENT_AVAILABLE"):
                        self.charge(buyer, debit, label)
                    self.assertEqual(self.debits(buyer), [])
                self.assertEqual(self.balance(buyer), remaining)

    def test_every_negative_client_rub_event_type_is_guarded(self):
        for event_type in ("ORDER_CHARGE", "ADJUSTMENT_MINUS", "DELIVERY_CHARGE"):
            with self.subTest(event_type=event_type):
                buyer = "g1-" + event_type
                self.fund(buyer, "10")
                with self.assertRaisesRegex(ValueError, "INSUFFICIENT_AVAILABLE"):
                    self.charge(buyer, "-11", "blocked-" + event_type, event_type)
                self.assertEqual(self.debits(buyer), [])
                self.charge(buyer, "-10", "exact-" + event_type, event_type)
                self.assertEqual(len(self.debits(buyer)), 1)
                self.assertEqual(self.balance(buyer), Decimal("0.00"))

    def test_adjustment_minus_and_delivery_charge_wrappers_cannot_bypass_guard(self):
        buyer = "g1-wrappers"
        self.fund(buyer, "10")
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_AVAILABLE"):
            self.engine.adjustment_minus(
                buyer_type="client", buyer_id=buyer, currency="RUB",
                amount="11", adjustment_id="g1-adjust", actor="qa", reason="test",
            )
        # Disposable prerequisite: delivery_charge reads dealer_arrivals before
        # reaching FinanceEngine.post_event's client/RUB capacity guard.
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                "CREATE TABLE dealer_arrivals ("
                "id INTEGER PRIMARY KEY, dealer_id TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO dealer_arrivals(id,dealer_id) VALUES (?,?)",
                (100, "dealer-qa"),
            )
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_AVAILABLE"):
            self.engine.delivery_charge(
                buyer_type="client", buyer_id=buyer, currency="RUB",
                amount="11", arrival_id=100, actor="qa", description="test",
            )
        self.assertEqual(self.balance(buyer), Decimal("10.00"))
        self.assertEqual(self.debits(buyer), [])

    def test_invalid_nonfinite_missing_zero_and_wrong_sign_amounts(self):
        for index, amount in enumerate(
            (None, "NaN", "Infinity", "-Infinity", float("nan"),
             float("inf"), "not-a-number", "0", 0, "-0.001", "5")
        ):
            with self.subTest(amount=repr(amount)):
                with self.assertRaises(ValueError):
                    self.charge("g1-invalid", amount, "invalid-" + str(index))
        with self.assertRaises(ValueError):
            self.engine.post_event(
                buyer_type="client", buyer_id="g1-invalid", currency="RUB",
                event_type="PAYMENT", amount="-1", description="bad sign",
                reference_type="payment", reference_id="bad-sign", actor="qa",
                idempotency_key="bad-sign",
            )
        self.assertEqual(self.debits("g1-invalid"), [])

    def test_direct_post_event_duplicate_rejected_before_insufficient_check(self):
        buyer = "g1-direct-replay"
        self.fund(buyer, "100")
        original = self.charge(buyer, "-100", "stable-key")
        self.assertIsInstance(original, int)
        with self.assertRaisesRegex(ValueError, "duplicate idempotency_key"):
            self.charge(buyer, "-100", "stable-key")
        self.assertEqual(len(self.debits(buyer)), 1)
        self.assertEqual(self.balance(buyer), Decimal("0.00"))

    def test_adapter_matching_replay_and_conflicting_replay(self):
        buyer = "g1-adapter-replay"
        self.fund(buyer, "100")
        adapter = ClientFinanceAdapter(self.engine)

        def line(amount):
            return LineFinanceSnapshot(
                order_id="g1-order", order_item_id=1, offer_source="usa",
                payment_route=PaymentRoute.EXTREMIZER_BALANCE,
                amount_rub=amount,
            )

        first = adapter.post_order_charge(
            client_id=buyer, order_id="g1-order", lines=[line(100)], actor="qa",
        )
        self.assertEqual(self.balance(buyer), Decimal("0.00"))
        replay = adapter.post_order_charge(
            client_id=buyer, order_id="g1-order", lines=[line(100)], actor="qa-replay",
        )
        self.assertEqual(replay, first)
        with self.assertRaisesRegex(ValueError, "idempotency key conflict"):
            adapter.post_order_charge(
                client_id=buyer, order_id="g1-order",
                lines=[line(101)], actor="qa-conflict",
            )
        self.assertEqual(len(self.debits(buyer)), 1)

    def test_direct_partner_exempt_and_mixed_route_only_eligible_debit(self):
        buyer = "g1-payment-routes"
        adapter = ClientFinanceAdapter(self.engine)
        direct = LineFinanceSnapshot(
            order_id="g1-routes", order_item_id=2, offer_source="warehouse",
            payment_route=PaymentRoute.DIRECT_PARTNER, amount_rub=900,
        )
        eligible = LineFinanceSnapshot(
            order_id="g1-routes", order_item_id=1, offer_source="usa",
            payment_route=PaymentRoute.EXTREMIZER_BALANCE, amount_rub=75,
        )
        self.assertIsNone(adapter.post_order_charge(
            client_id=buyer, order_id="g1-routes", lines=[direct], actor="qa",
        ))
        self.assertEqual(self.debits(buyer), [])
        self.fund(buyer, "75")
        event_id = adapter.post_order_charge(
            client_id=buyer, order_id="g1-routes",
            lines=[eligible, direct], actor="qa",
        )
        self.assertIsInstance(event_id, int)
        self.assertEqual(self.debits(buyer)[0][2], "-75.00")
        self.assertEqual((eligible.amount_rub, direct.amount_rub), (75, 900))
        self.assertEqual(self.balance(buyer), Decimal("0.00"))

    def test_caller_owned_transaction_and_rollback(self):
        buyer = "g1-rollback"
        self.fund(buyer, "100")
        conn = sqlite3.connect(self.db)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            event_id = self.charge(buyer, "-80", "rollback-event", conn=conn)
            self.assertGreater(event_id, 0)
            self.assertTrue(conn.in_transaction)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM finance_ledger WHERE idempotency_key=?",
                    ("rollback-event",),
                ).fetchone()[0], 1,
            )
            conn.rollback()  # Caller, not FinanceEngine, owns rollback.
            self.assertFalse(conn.in_transaction)
        finally:
            conn.close()
        self.assertEqual(self.balance(buyer), Decimal("100.00"))
        self.assertEqual(self.debits(buyer), [])

    def test_caller_owned_connection_requires_transaction(self):
        buyer = "g1-unprotected"
        self.fund(buyer, "100")
        conn = sqlite3.connect(self.db)
        try:
            with self.assertRaisesRegex(ValueError, "caller-owned transaction"):
                self.charge(buyer, "-80", "no-transaction", conn=conn)
            self.assertFalse(conn.in_transaction)
            self.assertEqual(conn.execute("SELECT 1").fetchone()[0], 1)
        finally:
            conn.close()
        self.assertEqual(self.debits(buyer), [])

    def test_deferred_transaction_writer_lock_proof_wal_and_rollback_journal(self):
        buyer = "g1-deferred"
        self.fund(buyer, "100")
        for mode in ("delete", "wal"):
            with self.subTest(journal_mode=mode):
                self.journal_mode(mode)
                caller = sqlite3.connect(self.db, timeout=0.1)
                contender = sqlite3.connect(self.db, timeout=0.1)
                try:
                    caller.execute("BEGIN DEFERRED")
                    self.charge(buyer, "-20", "upgrade-" + mode, conn=caller)
                    self.assertTrue(caller.in_transaction)
                    # The zero-row UPDATE must acquire the writer lock; a
                    # concurrent BEGIN IMMEDIATE cannot acquire it until rollback.
                    with self.assertRaises(sqlite3.OperationalError):
                        contender.execute("BEGIN IMMEDIATE")
                    caller.rollback()
                    self.assertEqual(self.balance(buyer), Decimal("100.00"))
                finally:
                    if caller.in_transaction:
                        caller.rollback()
                    if contender.in_transaction:
                        contender.rollback()
                    caller.close()
                    contender.close()

    def test_zero_row_lock_failure_wal_and_rollback_journal(self):
        buyer = "g1-lock-failure"
        self.fund(buyer, "100")
        for mode in ("delete", "wal"):
            with self.subTest(journal_mode=mode):
                self.journal_mode(mode)
                holder = sqlite3.connect(self.db, timeout=0.1)
                caller = sqlite3.connect(self.db, timeout=0.1)
                try:
                    holder.execute("BEGIN IMMEDIATE")
                    caller.execute("BEGIN DEFERRED")
                    with self.assertRaises(sqlite3.OperationalError):
                        self.charge(buyer, "-10", "locked-" + mode, conn=caller)
                    self.assertTrue(caller.in_transaction)
                    self.assertEqual(self.debits(buyer), [])
                finally:
                    caller.rollback()
                    holder.rollback()
                    caller.close()
                    holder.close()

    def test_stale_wal_read_snapshot_fails_closed(self):
        buyer = "g1-stale-wal"
        self.fund(buyer, "100")
        self.journal_mode("wal")
        reader = sqlite3.connect(self.db, timeout=0.1)
        try:
            reader.execute("BEGIN DEFERRED")
            reader.execute("SELECT COUNT(*) FROM finance_ledger").fetchone()
            # Independent commit invalidates a previously opened WAL read snapshot.
            self.fund(buyer, "10", "g1-stale-wal-new-payment")
            with self.assertRaises(sqlite3.OperationalError):
                self.charge(buyer, "-20", "stale-snapshot", conn=reader)
            self.assertTrue(reader.in_transaction)
            reader.rollback()
        finally:
            reader.close()
        self.assertEqual(self.debits(buyer), [])
        self.assertEqual(self.balance(buyer), Decimal("110.00"))

    def test_two_connections_compete_for_same_funds(self):
        buyer = "g1-concurrent-wallet"
        self.fund(buyer, "100")
        start = threading.Barrier(2)

        def attempt(i):
            start.wait(timeout=10)
            try:
                return ("posted", self.charge(buyer, "-80", "g1-concurrent-" + str(i)))
            except ValueError as exc:
                return ("rejected", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(attempt, 1)
            b = pool.submit(attempt, 2)
            results = (a.result(timeout=15), b.result(timeout=15))
        self.assertEqual(sorted(x[0] for x in results), ["posted", "rejected"])
        self.assertIn("INSUFFICIENT_AVAILABLE", next(x[1] for x in results if x[0] == "rejected"))
        self.assertEqual(len(self.debits(buyer)), 1)
        self.assertEqual(self.balance(buyer), Decimal("20.00"))

    def test_two_connections_duplicate_key_cannot_double_debit(self):
        buyer = "g1-concurrent-replay"
        self.fund(buyer, "100")
        start = threading.Barrier(2)

        def attempt():
            start.wait(timeout=10)
            try:
                return ("posted", self.charge(buyer, "-60", "g1-shared-key"))
            except ValueError as exc:
                return ("rejected", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(attempt)
            b = pool.submit(attempt)
            results = (a.result(timeout=15), b.result(timeout=15))
        self.assertEqual(sorted(x[0] for x in results), ["posted", "rejected"])
        self.assertIn("duplicate idempotency_key", next(x[1] for x in results if x[0] == "rejected"))
        self.assertEqual(len(self.debits(buyer)), 1)
        self.assertEqual(self.balance(buyer), Decimal("40.00"))

    def test_dealer_usd_debt_and_other_debit_guard_unchanged(self):
        order_id = self.engine.post_event(
            buyer_type="dealer", buyer_id="dealer-qa", currency="USD",
            event_type="ORDER_CHARGE", amount="-50", description="dealer v1.4 debt",
            reference_type="order", reference_id="dealer-order",
            actor="qa", idempotency_key="dealer-debt-g1",
        )
        self.assertGreater(order_id, 0)
        self.assertEqual(self.engine.balance("dealer-qa", "dealer", "USD"), Decimal("-50.00"))
        self.assertFalse(self.engine.dcp_procurement_ready("dealer-qa")["ready"])
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_AVAILABLE"):
            self.engine.adjustment_minus(
                buyer_type="dealer", buyer_id="dealer-qa", currency="USD",
                amount="1", adjustment_id="dealer-adj", actor="qa", reason="test",
            )
        with self.assertRaises(ValueError):
            self.engine.post_event(
                buyer_type="dealer", buyer_id="dealer-inactive", currency="USD",
                event_type="ORDER_CHARGE", amount="-1", description="inactive",
                reference_type="order", reference_id="inactive", actor="qa",
                idempotency_key="dealer-inactive-g1",
            )
        with self.assertRaises(ValueError):
            self.engine.post_event(
                buyer_type="dealer", buyer_id="dealer-qa", currency="RUB",
                event_type="ORDER_CHARGE", amount="-1", description="wrong currency",
                reference_type="order", reference_id="wrong-currency", actor="qa",
                idempotency_key="dealer-wrong-currency-g1",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
