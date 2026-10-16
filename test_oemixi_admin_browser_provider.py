import asyncio,unittest
from types import SimpleNamespace
from unittest.mock import patch

import oemixibot_app as app

class DummyApplication:
    def __init__(self):
        self.bot_data={}
        self.running=False

class ProviderTests(unittest.TestCase):
    def test_01_bind_provider(self):
        a=DummyApplication()
        fn=lambda oem:{"status":"X"}
        self.assertIs(app.bind_admin_polaris_price_resolver(a,fn),fn)
        self.assertIs(a.bot_data[app._ADMIN_POLARIS_PRICE_RESOLVER_KEY],fn)

    def test_02_bind_requires_callable(self):
        with self.assertRaises(ValueError):
            app.bind_admin_polaris_price_resolver(DummyApplication(),None)

    def test_03_bind_after_start_denied(self):
        a=DummyApplication();a.running=True
        with self.assertRaises(ValueError):
            app.bind_admin_polaris_price_resolver(a,lambda o:None)

    def test_04_provider_result_used(self):
        a=DummyApplication()
        a.bot_data[app._ADMIN_POLARIS_PRICE_RESOLVER_KEY]=lambda o:{
            "status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
            "manufacturer":"Polaris","item_category":"Parts",
            "requested_oem":o,"resolved_oem":"2206328",
            "admin_unit_price_usd":"184.07",
            "dealer_price_usd":None,"production_found":False,
            "source":"polaris:authenticated_dealer_cart:normal_browser",
        }
        ctx=SimpleNamespace(application=a)
        r=asyncio.run(app._resolve_admin_oem_offer("2205250",ctx))
        self.assertEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
        self.assertEqual(r["admin_unit_price_usd"],"184.07")
        self.assertIsNone(r["dealer_price_usd"])
        self.assertIs(r["production_found"],False)

    def test_05_provider_format(self):
        text=app._admin_offer_text("2205250",{
            "status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
            "manufacturer":"Polaris","item_category":"Parts",
            "resolved_oem":"2206328","admin_unit_price_usd":"184.07",
            "source":"polaris:authenticated_dealer_cart:normal_browser",
        })
        self.assertIn("Дилерская цена: $184.07",text)
        self.assertIn("Замена OEM: 2205250 → 2206328",text)

    def test_06_invalid_provider_result_fail_closed(self):
        a=DummyApplication()
        a.bot_data[app._ADMIN_POLARIS_PRICE_RESOLVER_KEY]=lambda o:None
        ctx=SimpleNamespace(application=a)
        r=asyncio.run(app._resolve_admin_oem_offer("2205250",ctx))
        self.assertEqual(r["status"],"DEALER_PRICE_UNVERIFIED")
        self.assertIsNone(r["dealer_price_usd"])
        self.assertIs(r["production_found"],False)

    def test_07_provider_exception_fail_closed(self):
        def boom(o): raise RuntimeError("x")
        a=DummyApplication()
        a.bot_data[app._ADMIN_POLARIS_PRICE_RESOLVER_KEY]=boom
        ctx=SimpleNamespace(application=a)
        with patch.object(app.logging,"exception"):
            r=asyncio.run(app._resolve_admin_oem_offer("2205250",ctx))
        self.assertEqual(r["status"],"DEALER_PRICE_UNVERIFIED")
        self.assertIsNone(r["admin_unit_price_usd"])

if __name__=="__main__":
    unittest.main()
