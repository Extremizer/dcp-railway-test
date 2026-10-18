"""Isolated contract regression for Theme 4 blocker fixes; no production access."""
import os, sqlite3, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import unified_analytics_producer as producer
import unified_analytics_outbox as outbox

class BlockerFixTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.db=str(Path(self.temp.name)/"producer.sqlite")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE existing_owner (id INTEGER)")
        outbox.initialize(self.db)
        producer.initialize_dealer_provenance(self.db)
    def tearDown(self):
        self.temp.cleanup()
    def test_token_owner_and_cart_order_exact_provenance(self):
        token=producer.register_dealer_card(self.db,dealer_id="D1",
            telegram_user_id=42,chat_id=42,message_id=7,oem="3211070")
        self.assertTrue(token.startswith("ua"))
        self.assertIsNone(producer.dealer_token_offer(self.db,token=token,dealer_id="D2",telegram_user_id=42))
        self.assertIsNone(producer.dealer_token_offer(self.db,token=token,dealer_id="D1",telegram_user_id=43))
        self.assertEqual(producer.dealer_token_offer(self.db,token=token,dealer_id="D1",telegram_user_id=42)[0],"3211070")
        delivery=producer.dealer_card_delivery(self.db,token=token,dealer_id="D1",telegram_user_id=42,oem="3211070")
        self.assertTrue(producer.dealer_cart_record(self.db,dealer_id="D1",oem="3211070",delivery_key=delivery,telegram_user_id=42))
        self.assertTrue(producer.stage_dealer_placed_order(self.db,dealer_id="D1",telegram_user_id=42,order_id="E-1",cart_oems=["3211070"]))
        with sqlite3.connect(self.db) as c:
            rows=c.execute("SELECT payload_json FROM analytics_delivery_outbox ORDER BY delivery_key").fetchall()
            self.assertEqual(len(rows),2)
            self.assertIn('"action":"CART"',rows[0][0])
            self.assertIn('"action":"ORDER_PLACED"',rows[1][0])
        self.assertFalse(producer.stage_dealer_placed_order(self.db,dealer_id="D1",telegram_user_id=42,order_id="E-2",cart_oems=["3211070"]))
    def test_transport_rejects_arbitrary_http_and_no_secret(self):
        event={"source_bot":"pricing","oem":"3211070"}
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_BASE_URL":"http://example.com",
             "EXTREMIZER_ANALYTICS_PRICING_SECRET":"x"*48}):
            self.assertFalse(producer._transmit(event))
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_BASE_URL":"http://127.0.0.1",
             "EXTREMIZER_ANALYTICS_PRICING_SECRET":"x"*48}):
            self.assertFalse(producer._transmit(event))
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_BASE_URL":"https://example.com",
             "EXTREMIZER_ANALYTICS_PRICING_SECRET":""}):
            self.assertFalse(producer._transmit(event))

if __name__=="__main__":unittest.main()
