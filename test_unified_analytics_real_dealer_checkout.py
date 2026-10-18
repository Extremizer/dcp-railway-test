"""Real OEMixi SQLite checkout: order, finance and analytics outbox are atomic."""
import sqlite3,tempfile,unittest
from pathlib import Path
from oemixibot_store import OemixiStore
from oemixibot_finance import FinanceEngine
import unified_analytics_outbox as outbox
import unified_analytics_producer as producer

class DealerRealCheckout(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.tmp.name)/"dealer.sqlite")
        self.store=OemixiStore(self.path)
        self.store.init();self.store.ensure_cart_schema()
        self.store.add_dealer("D1",42,"Synthetic",coefficient=1.0)
        outbox.initialize(self.path);producer.initialize_dealer_provenance(self.path)
        self.token=producer.register_dealer_card(self.path,dealer_id="D1",
            telegram_user_id=42,chat_id=42,message_id=9,oem="3211070")
        self.delivery=producer.dealer_card_delivery(self.path,token=self.token,
            dealer_id="D1",telegram_user_id=42,oem="3211070")
        self.store.cart_add("D1","3211070",100.0,"Polaris","Part","parts",1)
        producer.dealer_cart_record(self.path,dealer_id="D1",oem="3211070",
            delivery_key=self.delivery,telegram_user_id=42)
    def tearDown(self):self.tmp.cleanup()
    def hook(self,conn,dealer,order,items):
        if not producer.stage_dealer_placed_order(self.path,dealer_id=dealer,
                telegram_user_id=42,order_id=order,
                cart_oems=[r["oem"] for r in items],conn=conn):
            raise RuntimeError("analytics_order_lineage_missing")
    def test_insufficient_balance_leaves_cart_and_no_order_event(self):
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_AVAILABLE"):
            self.store.checkout_cart("D1","E-FAIL",order_analytics=self.hook)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_orders").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM analytics_delivery_outbox WHERE delivery_key LIKE '%:order:%'").fetchone()[0],0)
        self.assertEqual(self.store.cart_view("D1")["items"][0]["qty"],1)
    def test_rollback_hook_keeps_order_charge_cart_consistent(self):
        from oemixibot_finance import FinanceEngine
        FinanceEngine(self.path).post_event(buyer_type="dealer",buyer_id="D1",
            currency="USD",event_type="PAYMENT",amount=200,
            description="synthetic deposit",reference_type="test",
            reference_id="TOPUP-1",actor="test",idempotency_key="TOPUP-1")
        def crash(*args):raise RuntimeError("analytics_staging_failure")
        with self.assertRaisesRegex(RuntimeError,"analytics_staging_failure"):
            self.store.checkout_cart("D1","E-ROLLBACK",order_analytics=crash)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_orders").fetchone()[0],0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM finance_ledger WHERE event_type='ORDER_CHARGE'").fetchone()[0],0)
        self.assertEqual(self.store.cart_view("D1")["items"][0]["qty"],1)
        self.assertEqual(float(FinanceEngine(self.path).balance("D1")),200.0)
        self.assertEqual(self.store.checkout_cart("D1","E-SUCCESS",order_analytics=self.hook),100.0)
        with sqlite3.connect(self.path) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_orders").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM finance_ledger WHERE event_type='ORDER_CHARGE'").fetchone()[0],1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM analytics_delivery_outbox WHERE delivery_key LIKE '%:order:%'").fetchone()[0],1)
        self.assertEqual(self.store.cart_view("D1")["items"],[])
        self.assertEqual(float(FinanceEngine(self.path).balance("D1")),100.0)
if __name__=="__main__":unittest.main()
