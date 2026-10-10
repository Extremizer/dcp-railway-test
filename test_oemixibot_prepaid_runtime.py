"""Actual uploaded runtime against disposable SQLite; no polling/network."""
import ast
import asyncio
from decimal import Decimal
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import oemixibot_app as app
from oemixibot_domain import ItemSlice, ItemStatus
from oemixibot_finance import FinanceEngine
from oemixibot_store import OemixiStore
from oemixibot_telegram import DealerAccess, price_card, cart_message


class RuntimePrepaidTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = OemixiStore(str(Path(self.tmp.name) / 'runtime.db'))
        self.store.init()
        self.store.add_dealer('d', 101, 'Test Dealer', coefficient=1)
        self.finance = FinanceEngine(self.store.path)
        for name, value in [('STORE', self.store), ('FINANCE', self.finance),
                            ('ACCESS', DealerAccess(self.store))]:
            p = patch.object(app, name, value)
            p.start()
            self.addCleanup(p.stop)

    def pay(self, value=100, key='p'):
        return self.finance.payment(buyer_type='dealer', buyer_id='d', currency='USD',
            amount=value, payment_id=key, actor='test', reason='topup', payment_method='usd',
            source_amount=value, source_currency='USD')

    def update(self, data, user=101):
        q = SimpleNamespace(data=data, answer=AsyncMock(), edit_message_text=AsyncMock())
        return SimpleNamespace(callback_query=q, effective_user=SimpleNamespace(id=user)), q

    def test_runtime_import_and_no_removed_api_or_ui(self):
        self.assertIsInstance(app.FINANCE, FinanceEngine)
        source = Path(app.__file__).read_text(encoding='utf-8-sig')
        tree = ast.parse(source)
        forbidden = {'credit_limit', 'dealer_debt_status', 'set_credit_limit',
            'credit_limit_history', 'credit_limit_event', 'ensure_aging_schema',
            'set_receivable_due_date', 'receivable_aging', 'aging_summary',
            'ensure_aging_alert_schema', 'aging_alert_candidates', 'acknowledge_aging_alert'}
        self.assertFalse({n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)} & forbidden)
        for token in ('admdebt', 'admaging', 'admagingsum', 'admin_credit', 'admin_due',
                      'creditconfirm', 'credithistory', 'Кредитный лимит', 'aging_alert'):
            self.assertNotIn(token, source)
        for keyboard in (app._admin_root_keyboard(app._admin_dealers()),
                         app._admin_finance_keyboard('d'), app._finance_filters_keyboard()):
            for row in keyboard.inline_keyboard:
                for button in row:
                    self.assertNotIn('credit', button.callback_data or '')
                    self.assertNotIn('aging', button.callback_data or '')

    def test_balance_and_finance_history_callbacks(self):
        self.pay(42)
        dealer = app.ACCESS.dealer_for_telegram(101)
        for text in (app._balance_text(dealer), app._admin_finance_text('d')):
            self.assertIn('Баланс: $42.00', text)
            self.assertIn('Доступно для заказа: $42.00', text)
            self.assertNotIn('Кредит', text)
        for data in ('balance', 'finhome', 'finhist:all'):
            update, q = self.update(data)
            asyncio.run(app.callback(update, SimpleNamespace(user_data={})))
            self.assertTrue(q.edit_message_text.called)
        self.assertEqual(self.finance.history('d')[0]['event_type'], 'PAYMENT')

    def test_checkout_callback_rejects_then_accepts_prepaid(self):
        self.store.cart_add('d', 'TEST-OEM', 60, 'Ski-Doo', 'part', 'Accessories')
        self.pay(50)
        update, q = self.update('checkout')
        asyncio.run(app.callback(update, SimpleNamespace(user_data={})))
        self.assertIn('Недостаточно предоплаченного USD-баланса', q.edit_message_text.call_args.args[0])
        self.assertEqual(len(self.store.cart_view('d')['items']), 1)
        with self.store.db() as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM dealer_orders').fetchone()[0], 0)
        self.pay(20, 'extra')
        update, q = self.update('checkout')
        asyncio.run(app.callback(update, SimpleNamespace(user_data={})))
        self.assertIn('подтверждён', q.edit_message_text.call_args.args[0])
        self.assertEqual(self.store.cart_view('d')['items'], [])
        self.assertEqual(self.finance.balance('d'), Decimal('10'))
        self.assertEqual(sum(r['event_type']=='ORDER_CHARGE' for r in self.finance.history('d')), 1)

    def test_order_refund_and_adjustments_preserved(self):
        self.pay(100)
        self.store.cart_add('d', 'TEST-OEM', 60)
        self.store.checkout_cart('d', 'test-order')
        self.assertFalse(self.store.confirm_order_and_charge('test-order'))
        with self.store.db() as c:
            item = c.execute('SELECT id FROM item_slices').fetchone()[0]
        self.store.set_unavailable(item)
        self.assertEqual(self.finance.balance('d'), Decimal('100'))
        self.finance.adjustment(buyer_type='dealer', buyer_id='d', currency='USD',
            amount=5, adjustment_id='plus', actor='test', reason='fix')
        self.finance.adjustment_minus(buyer_type='dealer', buyer_id='d', currency='USD',
            amount=2, adjustment_id='minus', actor='test', reason='fix')
        self.store.cart_add('d', 'OTHER', 10)
        self.store.checkout_cart('d', 'second-order')
        event = next(r for r in self.finance.history('d') if r['order_id']=='second-order')
        self.finance.refund(buyer_type='dealer', buyer_id='d', currency='USD', amount=10,
            refund_id='manual', source_event_id=event['id'], actor='test', reason='return')
        self.assertEqual(self.finance.balance('d'), Decimal('103'))
        self.assertTrue({'PAYMENT','ORDER_CHARGE','AUTO_REFUND','REFUND','ADJUSTMENT_PLUS',
                         'ADJUSTMENT_MINUS'} <= {r['event_type'] for r in self.finance.history('d')})

    def test_delivery_shared_ledger_atomic_and_idempotent(self):
        with self.store.db() as c:
            c.execute("INSERT INTO usa_shipments(code,delivery_method,created_at) VALUES('TEST','air','test')")
            c.execute("INSERT INTO dealer_arrivals(shipment_id,dealer_id,created_at) VALUES(1,'d','test')")
        with self.assertRaisesRegex(ValueError, 'INSUFFICIENT_AVAILABLE'):
            self.store.bill_arrival(1, 1, 2, 10)
        with self.store.db() as c:
            self.assertEqual(c.execute('SELECT state FROM dealer_arrivals').fetchone()[0], 'unbilled')
        self.pay(20)
        self.store.bill_arrival(1, 1, 2, 10)
        self.assertEqual(self.finance.balance('d'), Decimal('10'))
        with self.assertRaisesRegex(ValueError, 'duplicate idempotency_key'):
            self.store.bill_arrival(1, 1, 2, 10)
        with self.store.db() as c:
            self.assertIsNone(c.execute("SELECT name FROM sqlite_master WHERE name='wallet_events'").fetchone())
        self.assertEqual(len(self.finance.history('d')), 2)

    def test_admin_topup_and_debit_callbacks_preserved(self):
        method = {'code':'usd', 'name':'USD', 'payment_currency':'USD'}
        state = {'dealer_id':'d', 'method':method, 'amount':'100', 'reason':'received',
                 'step':'confirm', 'payment_id':'admin-p'}
        update, q = self.update('admtopup:confirm:d', user=999)
        with patch.object(app, 'is_telegram_admin', return_value=True), \
             patch.object(self.finance, 'payment_methods', return_value=[method]):
            asyncio.run(app.callback(update, SimpleNamespace(user_data={'admin_topup':state})))
        self.assertEqual(self.finance.balance('d'), Decimal('100'))
        state = {'dealer_id':'d', 'amount':'10', 'reason':'fix', 'step':'preview',
                 'adjustment_id':'admin-a'}
        update, q = self.update('admdebit:confirm', user=999)
        with patch.object(app, 'is_telegram_admin', return_value=True):
            asyncio.run(app.callback(update, SimpleNamespace(user_data={'admin_debit':state})))
        self.assertEqual(self.finance.balance('d'), Decimal('90'))

    def test_obsolete_admin_callback_cannot_execute(self):
        for data in ('admfin:credit:d', 'admfin:creditconfirm:d', 'admaging:confirm:d',
                     'admagingsum:all:0', 'admdebt:credit:0'):
            update, q = self.update(data, user=999)
            with patch.object(app, 'is_telegram_admin', return_value=True):
                asyncio.run(app.callback(update, SimpleNamespace(user_data={})))
            self.assertIn('ADMIN-раздел не найден', q.edit_message_text.call_args.args[0])
        self.assertEqual(self.finance.history('d'), [])

    def test_unrelated_domain_cart_and_access(self):
        self.assertIsNone(app.ACCESS.dealer_for_telegram(102))
        self.store.cart_add('d', 'TEST', 10, qty=2)
        self.store.cart_set_qty('d', 'TEST', 3)
        self.assertIn('ИТОГО: $30.00', cart_message(self.store.cart_view('d')))
        self.assertIn('$10.00', price_card('Ski-Doo', 'TEST', 'part', 10, 1))
        item = ItemSlice('test', 'd', 'TEST', 3, ItemStatus.ACCEPTED)
        moved, rest = item.split(1)
        self.assertEqual((moved.qty,rest.qty), (1,2))

    def test_registered_callbacks_exclude_obsolete_features(self):
        from telegram.ext import CallbackQueryHandler, CommandHandler
        runtime = app.build_application(token='123456:synthetic-test-token')
        callbacks = [h for group in runtime.handlers.values() for h in group
                     if isinstance(h, CallbackQueryHandler)]
        self.assertEqual(len(callbacks), 1)
        for data in ('admfin:credit:d', 'admfin:creditconfirm:d', 'admdebt',
                     'admaging:due:1:d', 'admagingsum:all:0'):
            self.assertIsNone(callbacks[0].pattern.match(data))
        for data in ('checkout', 'balance', 'finhome', 'admfin:history:d',
                     'admtopup:confirm:d', 'admdebit:confirm', 'receive_yes:1'):
            self.assertIsNotNone(callbacks[0].pattern.match(data))
        commands = {name for group in runtime.handlers.values() for h in group
                    if isinstance(h, CommandHandler) for name in h.commands}
        self.assertEqual(commands, {'start','cart','orders','balance','admin'})
        if runtime.job_queue is not None:
            self.assertEqual(runtime.job_queue.jobs(), ())

    def test_historical_credit_and_wallet_data_remain_unused(self):
        with self.store.db() as c:
            c.execute('ALTER TABLE dealers ADD COLUMN credit_limit_usd TEXT')
            c.execute("UPDATE dealers SET credit_limit_usd='1000000'")
            c.execute('CREATE TABLE wallet_events(id INTEGER, amount_usd REAL)')
            c.execute('INSERT INTO wallet_events VALUES(1,1000000)')
        self.store.init()
        self.store.add_dealer('second', 102, 'Second')
        self.store.cart_add('d','TEST',1)
        with self.assertRaisesRegex(ValueError,'INSUFFICIENT_AVAILABLE'):
            self.store.checkout_cart('d','no-credit-order')
        with self.store.db() as c:
            self.assertEqual(c.execute("SELECT credit_limit_usd FROM dealers WHERE id='d'").fetchone()[0], '1000000')
            self.assertEqual(tuple(c.execute('SELECT * FROM wallet_events').fetchone()), (1,1000000))


if __name__ == '__main__':
    unittest.main()
