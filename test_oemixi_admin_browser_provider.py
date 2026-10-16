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


    def test_08_real_adapter_enables_controlled_probe_mutation(self):
        import oemixi_admin_polaris_browser_price as price
        evidence={
            "resolved_oem":"2206328",
            "item_category":"Parts",
            "category_source_kind":"validated_public_msrp",
            "category_source_ref":"fixture",
            "category_verified_at":"2026-10-16T18:30:00+00:00",
        }
        adapter=price._real_adapter(
            evidence,
            tabs=lambda:[],
            evaluate=lambda *_:None,
            preflight=lambda:"READY",
            expected="dealer@example.invalid",
        )
        self.assertIs(adapter.allow_mutation,True)


    def test_09_restored_compensated_probe_is_accepted(self):
        import oemixi_admin_polaris_browser_price as price
        evidence={
            "resolved_oem":"2206328",
            "item_category":"Parts",
            "category_source_kind":"validated_public_msrp",
            "category_source_ref":"fixture",
            "category_verified_at":"2026-10-16T18:30:00+00:00",
        }
        probe={
            "status":"ADMIN_PROBE_CART_PRICE_VERIFIED",
            "requested_oem":"2205250","resolved_oem":"2206328",
            "admin_unit_price_usd":"184.07",
            "cart_mutated":True,"cart_restored":True,
            "dealer_price_usd":None,"production_found":False,
        }
        with patch.object(price,"_category",return_value=evidence), patch.object(price,"run_probe",return_value=probe):
            r=price.admin_browser_price("2205250",category_resolver=lambda *_: evidence,adapter=object())
        self.assertEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
        self.assertEqual(r["admin_unit_price_usd"],"184.07")
        self.assertIsNone(r["dealer_price_usd"])
        self.assertIs(r["production_found"],False)

    def test_10_unrestored_mutation_is_rejected(self):
        import oemixi_admin_polaris_browser_price as price
        evidence={
            "resolved_oem":"2206328",
            "item_category":"Parts",
            "category_source_kind":"validated_public_msrp",
            "category_source_ref":"fixture",
            "category_verified_at":"2026-10-16T18:30:00+00:00",
        }
        probe={
            "status":"ADMIN_PROBE_CART_PRICE_VERIFIED",
            "requested_oem":"2205250","resolved_oem":"2206328",
            "admin_unit_price_usd":"184.07",
            "cart_mutated":True,"cart_restored":False,
            "dealer_price_usd":None,"production_found":False,
        }
        with patch.object(price,"_category",return_value=evidence), patch.object(price,"run_probe",return_value=probe):
            r=price.admin_browser_price("2205250",category_resolver=lambda *_: evidence,adapter=object())
        self.assertEqual(r["status"],"DEALER_PRICE_UNVERIFIED")
        self.assertEqual(r["reason"],"UNSAFE_ADMIN_RESULT")
        self.assertIsNone(r["admin_unit_price_usd"])

if __name__=="__main__":
    unittest.main()
