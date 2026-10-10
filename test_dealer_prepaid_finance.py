"""Disposable SQLite regression; no bots, network or production database."""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from client_finance_schema import ensure_client_finance_schema
from common_finance_contract import DEALER_POLICY, CLIENT_POLICY
from oemixibot_finance import FinanceEngine


class TrackedConnection(sqlite3.Connection):
    """Real SQLite transaction with observable ownership/lifecycle calls."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = []
        self.fail_insert = False

    def execute(self, sql, *args, **kwargs):
        if sql == 'BEGIN IMMEDIATE':
            self.calls.append('begin')
        if self.fail_insert and sql.startswith('INSERT INTO finance_ledger'):
            raise sqlite3.OperationalError('injected insert failure')
        return super().execute(sql, *args, **kwargs)

    def rollback(self):
        self.calls.append('rollback')
        return super().rollback()

    def commit(self):
        self.calls.append('commit')
        return super().commit()

    def close(self):
        self.calls.append('close')
        return super().close()


class DealerPrepaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'orders.db')
        ensure_client_finance_schema(self.path)
        with sqlite3.connect(self.path) as c:
            c.executescript("""
                CREATE TABLE dealers(id TEXT PRIMARY KEY, active INTEGER,
                                     credit_limit_usd TEXT);
                INSERT INTO dealers VALUES('dealer',1,'999999999.00');
                CREATE TABLE dealer_orders(id TEXT PRIMARY KEY, dealer_id TEXT);
                CREATE TABLE item_slices(id INTEGER PRIMARY KEY, order_id TEXT,
                                        oem TEXT, line_total_usd TEXT);
                CREATE TABLE dealer_arrivals(id INTEGER PRIMARY KEY, dealer_id TEXT);
                INSERT INTO dealer_arrivals VALUES(1,'dealer');
                CREATE TABLE finance_receivable_terms(source_event_id INTEGER,
                                                       due_date TEXT);
                CREATE TABLE finance_due_date_audit(id INTEGER, new_due_date TEXT);
                CREATE TABLE finance_aging_alert_state(source_event_id INTEGER,
                                                      alert_key TEXT);
                CREATE TABLE finance_credit_limit_audit(id INTEGER, new_limit TEXT);
                INSERT INTO finance_receivable_terms VALUES(1,'2000-01-01');
                INSERT INTO finance_due_date_audit VALUES(1,'2000-01-01');
                INSERT INTO finance_aging_alert_state VALUES(1,'overdue_30_plus');
                INSERT INTO finance_credit_limit_audit VALUES(1,'999999999');
            """)
        self.engine = FinanceEngine(self.path)

    def pay(self, amount=100, key='payment'):
        return self.engine.payment(buyer_type='dealer', buyer_id='dealer',
            currency='USD', amount=amount, payment_id=key, actor='test',
            reason='topup', payment_method='usd', source_amount=amount,
            source_currency='USD')

    def charge(self, amount=60, key='order', conn=None, currency='USD'):
        return self.engine.post_event(buyer_type='dealer', buyer_id='dealer',
            currency=currency, event_type='ORDER_CHARGE', amount=-Decimal(str(amount)),
            description='order', reference_type='order', reference_id=key,
            order_id=key, actor='test', idempotency_key='ORDER_CHARGE:'+key,
            conn=conn)

    def test_policy_and_legacy_limit_cannot_authorize(self):
        self.assertEqual(DEALER_POLICY.wallet_currency, 'USD')
        self.assertFalse(any((DEALER_POLICY.credit_enabled,
            DEALER_POLICY.due_date_enabled, DEALER_POLICY.aging_enabled)))
        self.assertEqual(CLIENT_POLICY.wallet_currency, 'RUB')
        self.assertEqual(self.engine.credit_limit('dealer'), Decimal('0'))
        self.assertEqual(self.engine.available_to_order('dealer'), Decimal('0'))
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.engine.require_order_capacity(buyer_id='dealer', amount=1)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.charge(1)
        self.assertEqual(self.engine.history('dealer'), [])

    def test_payment_charge_refund_adjustment_history_idempotency(self):
        payment = self.pay()
        charge = self.charge()
        refund = self.engine.refund(buyer_type='dealer', buyer_id='dealer',
            currency='USD', amount=10, refund_id='refund', source_event_id=charge,
            actor='test', reason='return')
        self.engine.adjustment(buyer_type='dealer', buyer_id='dealer',
            currency='USD', amount=5, adjustment_id='plus', actor='test', reason='fix')
        self.engine.adjustment_minus(buyer_type='dealer', buyer_id='dealer',
            currency='USD', amount=2, adjustment_id='minus', actor='test', reason='fix')
        self.assertEqual(self.engine.balance('dealer'), Decimal('53'))
        rows = self.engine.history('dealer')
        self.assertEqual(len(rows), 5)
        self.assertEqual(sum(r['amount'] for r in rows), Decimal('53'))
        self.assertEqual(self.engine.event(payment, 'dealer')['event_type'], 'PAYMENT')
        self.assertEqual(self.engine.event(refund, 'dealer')['event_type'], 'REFUND')
        for retry in (self.pay, self.charge):
            with self.assertRaisesRegex(ValueError, 'duplicate idempotency_key'):
                retry()
        self.assertEqual(len(self.engine.history('dealer')), 5)

    def test_insufficient_charge_preserves_caller_transaction(self):
        with sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            c.execute('BEGIN IMMEDIATE')
            c.execute("INSERT INTO dealer_orders VALUES('pending','dealer')")
            with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
                self.charge(1, 'pending', conn=c)
            self.assertTrue(c.in_transaction)
            self.assertIsNotNone(c.execute("SELECT id FROM dealer_orders WHERE id='pending'").fetchone())
            c.rollback()
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM dealer_orders').fetchone()[0], 0)
        self.assertEqual(self.engine.history('dealer'), [])

    def test_inactive_dealer_cannot_charge(self):
        self.pay()
        with sqlite3.connect(self.path) as c:
            c.execute("UPDATE dealers SET active=0 WHERE id='dealer'")
        with self.assertRaisesRegex(ValueError, 'inactive dealer'):
            self.charge(1)
        self.assertEqual(self.engine.balance('dealer'), Decimal('100'))

    def test_owned_failure_explicitly_rolls_back_then_closes(self):
        for insert_error in (False, True):
            with self.subTest(insert_error=insert_error):
                if insert_error:
                    self.pay(100)
                c = sqlite3.connect(self.path, factory=TrackedConnection)
                c.row_factory = sqlite3.Row
                c.fail_insert = insert_error
                error = sqlite3.OperationalError if insert_error else ValueError
                message = 'injected insert failure' if insert_error else 'INSUFFICIENT_AVAILABLE'
                with patch.object(self.engine, '_db', return_value=c):
                    with self.assertRaisesRegex(error, message):
                        self.charge(1)
                self.assertEqual(c.calls, ['begin', 'rollback', 'close'])
                with self.assertRaises(sqlite3.ProgrammingError):
                    c.execute('SELECT 1')
                self.assertFalse(any(r['event_type'] == 'ORDER_CHARGE'
                                     for r in self.engine.history('dealer')))

    def test_caller_failure_never_finishes_or_closes_transaction(self):
        for insert_error in (False, True):
            with self.subTest(insert_error=insert_error):
                c = sqlite3.connect(self.path, factory=TrackedConnection)
                c.row_factory = sqlite3.Row
                try:
                    c.execute('BEGIN IMMEDIATE')
                    c.execute("INSERT INTO dealer_orders VALUES('pending','dealer')")
                    if insert_error:
                        self.engine.post_event(buyer_type='dealer', buyer_id='dealer',
                            currency='USD', event_type='PAYMENT', amount=100,
                            description='topup', reference_type='payment', reference_id='shared',
                            actor='test', idempotency_key='shared-payment', conn=c)
                    c.calls.clear()
                    c.fail_insert = insert_error
                    error = sqlite3.OperationalError if insert_error else ValueError
                    with self.assertRaises(error):
                        self.charge(1, conn=c)
                    self.assertEqual(c.calls, [])
                    self.assertTrue(c.in_transaction)
                    self.assertEqual(c.execute('SELECT COUNT(*) FROM dealer_orders').fetchone()[0], 1)
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM finance_ledger WHERE event_type='ORDER_CHARGE'").fetchone()[0], 0)
                    # Caller can keep working and decides when to rollback.
                    c.execute("UPDATE dealer_orders SET id='still-controlled'")
                    c.rollback()
                    self.assertEqual(c.calls, ['rollback'])
                finally:
                    c.close()
                self.assertEqual(self.engine.history('dealer'), [])

    def test_exact_balance_and_insufficient_balance(self):
        self.pay(10)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.charge('10.01')
        self.charge(10)
        self.assertEqual(self.engine.balance('dealer'), Decimal('0'))
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.charge(1, 'second')

    def test_all_dealer_debits_prepaid_and_usd_only(self):
        with self.assertRaisesRegex(ValueError, 'must be USD'):
            self.charge(1, currency='RUB')
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.engine.adjustment_minus(buyer_type='dealer', buyer_id='dealer',
                currency='USD', amount=1, adjustment_id='x', actor='test', reason='fix')
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.engine.delivery_charge(buyer_type='dealer', buyer_id='dealer',
                currency='USD', amount=1, arrival_id=1, actor='test', description='delivery')

    def test_caller_rollback_and_uncommitted_topup(self):
        with sqlite3.connect(self.path) as c:
            c.row_factory = sqlite3.Row
            c.execute('BEGIN IMMEDIATE')
            self.engine.post_event(buyer_type='dealer', buyer_id='dealer', currency='USD',
                event_type='PAYMENT', amount=100, description='topup', reference_type='payment',
                reference_id='shared', actor='test', idempotency_key='shared-payment', conn=c)
            self.charge(100, conn=c)
            self.assertTrue(c.in_transaction)
            self.assertEqual(self.engine.balance('dealer'), Decimal('0'))
            c.rollback()
        self.assertEqual(self.engine.history('dealer'), [])

    def test_concurrent_debits_cannot_overspend(self):
        self.pay()
        def debit(key):
            try:
                self.charge(70, key)
                return 'charged'
            except ValueError as exc:
                self.assertIn('INSUFFICIENT_AVAILABLE', str(exc))
                return 'rejected'
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertCountEqual(list(pool.map(debit, ['one', 'two'])),
                                  ['charged', 'rejected'])
        self.assertEqual(self.engine.balance('dealer'), Decimal('30'))

    def test_legacy_negative_balance_and_schema_preserved(self):
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO finance_ledger(buyer_type,buyer_id,wallet_currency,"
                "event_type,amount,description,reference_type,reference_id,actor,"
                "idempotency_key,created_at) VALUES('dealer','dealer','USD',"
                "'ORDER_CHARGE','-50','historical','order','old','test','old','2000-01-01')")
            before = {table: c.execute('SELECT * FROM '+table).fetchall() for table in
                ('finance_receivable_terms', 'finance_due_date_audit',
                 'finance_aging_alert_state', 'finance_credit_limit_audit', 'dealers')}
        self.pay(40)
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.charge(1)
        self.pay(20, 'second')
        self.charge(10)
        self.assertEqual(self.engine.balance('dealer'), Decimal('0'))
        with sqlite3.connect(self.path) as c:
            for table, rows in before.items():
                self.assertEqual(c.execute('SELECT * FROM '+table).fetchall(), rows)

    def test_no_credit_column_required_and_no_obsolete_api(self):
        with sqlite3.connect(self.path) as c:
            c.execute('ALTER TABLE dealers DROP COLUMN credit_limit_usd')
        self.pay(10)
        self.charge(10)
        for name in ('set_credit_limit', 'dealer_debt_status', 'credit_limit_history',
            'credit_limit_event', 'ensure_aging_schema', 'set_receivable_due_date',
            'receivable_aging', 'aging_summary', 'ensure_aging_alert_schema',
            'aging_alert_candidates', 'acknowledge_aging_alert'):
            self.assertFalse(hasattr(self.engine, name), name)

    def test_auto_refund_contract_retained(self):
        self.pay(10)
        self.charge(10)
        with sqlite3.connect(self.path) as c:
            c.execute("INSERT INTO dealer_orders VALUES('order','dealer')")
            c.execute("INSERT INTO item_slices VALUES(1,'order','test-oem','10')")
        self.engine.auto_refund(buyer_type='dealer', buyer_id='dealer', currency='USD',
            slice_id=1, reason_code='unavailable', actor='test')
        self.assertEqual(self.engine.balance('dealer'), Decimal('10'))
        with self.assertRaisesRegex(ValueError, 'already auto-refunded'):
            self.engine.auto_refund(buyer_type='dealer', buyer_id='dealer', currency='USD',
                slice_id=1, reason_code='unavailable', actor='test')


if __name__ == '__main__':
    unittest.main()
