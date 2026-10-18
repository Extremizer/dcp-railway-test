"""Exercise the real OEMixi Telegram callback with analytics ON and OFF."""
import asyncio
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch
import oemixibot_app as app
import unified_analytics_producer as producer
import unified_analytics_outbox as outbox

OFFER={"oem":"3211070","dl_usd":100.0,"manufacturer":"Polaris","name":"Part","item_type":"parts"}

class StoreFixture:
    def __init__(self,path):
        self.path=path;self.items={};self.fail_add=False;self.fail_checkout=False
    def cart_view(self,dealer):
        items=[{"oem":oem,"qty":qty,"dl_usd":100.0,"dlp_usd":100.0} for oem,qty in self.items.items()]
        return {"items":items,"total_usd":sum(r["qty"]*100 for r in items)}
    def cart_add(self,dealer,oem,*a):
        if self.fail_add:raise ValueError("dealer unavailable")
        self.items[oem]=self.items.get(oem,0)+1
    def cart_set_qty(self,dealer,oem,qty):
        if qty<=0:self.items.pop(oem,None)
        else:self.items[oem]=qty
    def db(self):
        return sqlite3.connect(self.path)
    def checkout_cart(self,dealer,oid,*,order_analytics=None):
        if self.fail_checkout:raise ValueError("INSUFFICIENT_AVAILABLE:0:100")
        items=[{"oem":oem} for oem in self.items]
        with sqlite3.connect(self.path) as conn:
            conn.execute("INSERT INTO dealer_orders VALUES(?)",(oid,))
            if order_analytics:order_analytics(conn,dealer,oid,items)
        self.items.clear()
        return 100.0

class RealCallbackTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.path=str(Path(self.t.name)/"existing.sqlite")
        with sqlite3.connect(self.path) as c:
            c.execute("CREATE TABLE dealer_orders(id TEXT PRIMARY KEY)")
        outbox.initialize(self.path)
        producer.initialize_dealer_provenance(self.path)
        self.store=StoreFixture(self.path)
        self.ps=patch.object(app,"STORE",self.store)
        self.pd=patch.object(app,"_dealer",return_value={"id":"D1","price_coefficient":1})
        self.ps.start();self.pd.start()
    def tearDown(self):
        self.ps.stop();self.pd.stop();self.t.cleanup()
    def call(self,data,*,user=42,offers=None):
        query=NS(data=data,answer=AsyncMock(),edit_message_text=AsyncMock())
        tasks=[]
        def create_task(coro):
            # Run queued background delivery on completion, never against network.
            tasks.append(coro)
        ctx=NS(user_data=offers or {},application=NS(create_task=create_task))
        update=NS(callback_query=query,effective_user=NS(id=user))
        async def exercise():
            await app.callback(update,ctx)
            for coro in tasks:
                coro.close()
        asyncio.run(exercise())
        return query
    def token(self):
        token=producer.register_dealer_card(self.path,dealer_id="D1",telegram_user_id=42,
            chat_id=42,message_id=12,oem=OFFER["oem"])
        return token,{"offer_token:"+token:dict(OFFER)}
    def test_enabled_token_and_wrong_user(self):
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_ENABLE":"1"}):
            token,offers=self.token()
            self.call("add:"+token,offers=offers,user=43)
            self.assertEqual(self.store.items,{})
            with patch.object(app,"_dealer",return_value={"id":"D2","price_coefficient":1}):
                self.call("add:"+token,offers=offers,user=42)
            self.assertEqual(self.store.items,{})
            self.call("add:"+token,offers=offers,user=42)
            self.assertEqual(self.store.items,{"3211070":1})
            with sqlite3.connect(self.path) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM analytics_dealer_cart_provenance").fetchone()[0],1)
    def test_legacy_flag_off_and_add_failure(self):
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_ENABLE":"0"}):
            self.call("add:3211070",offers={"offer:3211070":dict(OFFER)})
            self.assertEqual(self.store.items,{"3211070":1})
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_ENABLE":"1"}):
            self.store.items.clear()
            token,offers=self.token();self.store.fail_add=True
            with self.assertRaises(ValueError):
                self.call("add:"+token,offers=offers)
            with sqlite3.connect(self.path) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM analytics_dealer_cart_provenance").fetchone()[0],0)
    def test_checkout_insufficient_balance_then_durable_order(self):
        with patch.dict(os.environ,{"EXTREMIZER_UNIFIED_ANALYTICS_ENABLE":"1"}):
            token,offers=self.token()
            self.call("add:"+token,offers=offers)
            self.store.fail_checkout=True
            self.call("checkout")
            with sqlite3.connect(self.path) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_orders").fetchone()[0],0)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM analytics_delivery_outbox WHERE delivery_key LIKE '%:order:%'").fetchone()[0],0)
            self.store.fail_checkout=False
            with patch.object(producer,"flush_pending",return_value={"delivered":0}):
                self.call("checkout")
            with sqlite3.connect(self.path) as c:
                self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_orders").fetchone()[0],1)
                self.assertEqual(c.execute("SELECT COUNT(*) FROM analytics_delivery_outbox WHERE delivery_key LIKE '%:order:%'").fetchone()[0],1)
if __name__=="__main__":unittest.main()
