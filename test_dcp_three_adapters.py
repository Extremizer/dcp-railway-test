"""Actual three adapter bodies through one mock gateway and signed HTTP producer.

No bot startup or browser. OEMixi gateway injection is an isolated proposal,
not evidence that an installed runtime already uses it.
"""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import logging
import math
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock
import unittest

import manufacturer_first_ux as mfgux
from polaris_dealer_cart_completion_contract import SOURCE
from dcp_isolated_category_fixture import seed_test_category_authority
import dcp_price_gateway as gateway
import dp_live_bridge as bridge
import dp_live_health as health
import dp_claim_recovery as recovery
from test_dp_result_completion import CompletionFixture, completion, headers as producer_headers

ROOT=Path(__file__).resolve().parent


def actual_functions(filename,names,namespace):
    nodes=[n for n in ast.parse((ROOT/filename).read_text()).body
           if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    assert {n.name for n in nodes}==set(names)
    exec(compile(ast.Module(body=nodes,type_ignores=[]),filename,"exec"),namespace)
    return namespace


class ThreeAdapterTests(CompletionFixture):
    def setUp(self):
        super().setUp()
        seed_test_category_authority(self.db, ("Polaris", "2206328"))
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM dealer_price_cache")
            completion._write_identity(c,"Polaris","2206328","2206328","known identity","fixture",health._now())
            c.execute("UPDATE oem_catalog_cache SET name='public catalog',msrp_usd=999 WHERE manufacturer='Polaris' AND current_oem='2206328'")
        self.browser=SimpleNamespace(get_dealer_price_dict=Mock(side_effect=AssertionError("direct DCP lookup forbidden")))
        self.finder=SimpleNamespace(manufacturer_alias=lambda m:bridge._request_key(m,"2206328")[0],
                                    normalize_oem=lambda o:bridge._request_key("Polaris",o)[1])

    def adapters(self,lookup):
        probnik=actual_functions("probnik_app.py",{"_identity_and_price","_customer_price_rub","_db_tables"},{
            "ORDERS_DB":self.db,"DP_MAX_AGE_HOURS":168,"finder":self.finder,
            "sqlite3":sqlite3,"math":math,"normalize_oem":self.finder.normalize_oem,"mfgux":mfgux,
            "PRICE_COEFFICIENT":1,"USD_RUB_RATE":100,
            "dp_live_bridge":SimpleNamespace(request_live_dp=lambda db,m,o,**kw:lookup(m,o)),
        })
        procenka=actual_functions("extremizer_bot.py",{
            "enrich_found_result_with_dealer_price","get_dealer_price_cache",
            "upsert_dealer_price_cache","_dealer_price_cache_key"},{
            "ORDERS_DB_FILE":self.db,"DP_CACHE_MAX_AGE_HOURS":168,"DCP_CACHE_ONLY":True,
            "datetime":datetime,"asyncio":asyncio,"sqlite3":sqlite3,"finder":self.finder,
            "init_orders_db":lambda:None,"log":logging.getLogger("isolated-dcp-test"),
            "dp_live_bridge":SimpleNamespace(request_live_dp=lambda db,m,o,**kw:lookup(m,o)),
        })
        procenka["upsert_dealer_price_cache"]=Mock(side_effect=AssertionError("consumer DP write forbidden"))
        dealer=actual_functions("oemixibot_catalog.py",{"resolve_identity","resolve_offer"},{
            "finder":self.finder,"oem_reference_service":SimpleNamespace(lookup_oem=lambda o:{}),
            "oem_identity_service":SimpleNamespace(lookup=lambda o:{"manufacturer":"Polaris","item_type":"parts"}),
            "oemixibot_dcp_price":self.browser,"confirm_identity":Mock(),
        })
        runtime=actual_functions('oemixibot_app.py',
            {'bind_shared_price_gateway','_resolve_runtime_offer','oem_message'}, {
                'SharedDealerPriceAdapter':gateway.SharedDealerPriceAdapter,
                '_SHARED_PRICE_ADAPTER_KEY':'oemixi_shared_dealer_price_adapter',
                'ContextTypes':SimpleNamespace(DEFAULT_TYPE=object),'Update':object,
                'asyncio':asyncio,'resolve_offer':dealer['resolve_offer'],
                '_dealer':lambda update:{'id':'mock-dealer','price_coefficient':1},
                'normalize_oem':self.finder.normalize_oem,'logging':logging,'mfgux':mfgux,
                'STORE':SimpleNamespace(cart_view=lambda d:{'items':[],'total_usd':0}),
                'price_card':lambda m,o,n,p,*a:'dealer USD '+str(p),
                'InlineKeyboardButton':lambda *a,**kw:None,
                'InlineKeyboardMarkup':lambda rows:rows,
            })
        application=SimpleNamespace(bot_data={})
        runtime['bind_shared_price_gateway'](application,lookup,dealer_max_age_hours=168)
        context=SimpleNamespace(application=application,user_data={"manufacturer":"Polaris"})
        self.dealer_context=context
        self.dealer_reply=AsyncMock()
        update=SimpleNamespace(effective_user=None,
            effective_message=SimpleNamespace(text='2206328',reply_text=self.dealer_reply))
        async def dealer_handler():
            await runtime['oem_message'](update,context)
            return context.user_data.get('offer:2206328') or {'status':'NO_PRICE','dl_usd':None}
        # Preserve raw statuses on non-FOUND replies while invoking the actual handler.
        original=runtime['_resolve_runtime_offer']
        async def recording(oem,ctx,manufacturer=None):
            value=await original(oem,ctx,manufacturer=manufacturer);self.dealer_result=value;return value
        runtime['_resolve_runtime_offer']=recording
        def resolve_dealer():
            asyncio.run(dealer_handler());return self.dealer_result
        return (
            lambda:probnik["_identity_and_price"]("2206328", "Polaris"),
            lambda:asyncio.run(procenka["enrich_found_result_with_dealer_price"]({
                "status":"FOUND","catalog":"Parts","manufacturer":"Polaris","oem":"2206328","msrp_usd":999})),
            resolve_dealer,
        )

    def payload(self, claim, **changes):
        value=super().payload(claim)
        value.update(manufacturer="Polaris",query_oem="2206328",matched_sku="2206328",
                     current_oem="2206328",source=SOURCE)
        value["dealer_cart_proof"]={
            "status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
            "dealer_authenticated":True,"dealer_context_verified":True,
            "trusted_preflight":True,"cart_restored":True,"production_found":False,
            "dealer_price_usd":None,"requested_oem":"2206328","resolved_oem":"2206328",
            "matched_sku":"2206328","currency":"USD","quantity":1,
            "unit_price_usd":31.25,"subtotal_usd":31.25,
            "dealer_identity_proof_ref":"synthetic://three-adapter-cart",
            "category_source_kind":claim["category_source_kind"],
            "category_source_ref":claim["category_source_ref"],
            "category_verified_at":claim["category_verified_at"],
        }
        return {**value,**changes}

    def claim(self):
        return super().claim("Polaris","2206328")

    def complete_verified_cache(self):
        claim=self.claim()
        response=self.post(self.payload(claim))
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()["server_cache_committed"])

    def pipeline(self,status,*,expected_requests=1):
        all_arrived=threading.Event();barrier=threading.Barrier(3);errors=[];claims=[]
        def live_request(db,m,o,**kwargs):
            barrier.wait(timeout=4)
            all_arrived.set()
            return bridge.request_live_dp(db,m,o,wait_seconds=4)
        def lookup(m,o,*,max_age_hours=168):
            return gateway.request_price(self.db,m,o,wait_seconds=4,live_request=live_request,max_age_hours=max_age_hours)
        def producer():
            try:
                if not all_arrived.wait(timeout=4):raise AssertionError("real adapters did not reach gateway")
                deadline=time.monotonic()+3
                claim=None
                while claim is None and time.monotonic()<deadline:
                    response=self.client.get("/internal/dp-sync/next",headers=producer_headers("GET","/internal/dp-sync/next"))
                    if response.status_code!=200:raise AssertionError(response.text)
                    claim=response.json()["request"]
                    if claim is None:time.sleep(0.01)
                if claim is None:raise AssertionError("producer did not receive one claim")
                claims.append(claim)
                self.assertIsNone(bridge.claim_next(self.db,producer_id="other-worker"))
                response=self.post(self.payload(claim,status=status))
                if response.status_code!=200:raise AssertionError(response.text)
            except BaseException as exc:errors.append(exc)
        worker=threading.Thread(target=producer)
        worker.start()
        with ThreadPoolExecutor(max_workers=3) as pool:
            results=[f.result(timeout=7) for f in [pool.submit(fn) for fn in self.adapters(lookup)]]
        worker.join(timeout=6)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors,[])
        self.assertEqual(len(claims),1)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dp_live_requests").fetchone()[0],expected_requests)
            self.assertEqual(c.execute("SELECT amount FROM finance_ledger").fetchone()[0],100)
            self.assertEqual(c.execute("SELECT marker FROM orders").fetchone()[0],"unchanged")
        self.browser.get_dealer_price_dict.assert_not_called()
        return results,claims[0]

    def test_three_real_adapters_one_live_lookup_usd_and_rub_preserved(self):
        results,_=self.pipeline("FOUND")
        self.assertEqual(results[0]["dp_usd"],31.25)
        self.assertEqual(results[0]["customer_rub"],3200)
        self.assertEqual(results[1]["_dealer_price_usd"],31.25)
        self.assertEqual(results[2]["dl_usd"],31.25)
        self.assertEqual(self.dealer_context.user_data['offer:2206328']['dl_usd'],31.25)
        self.assertIn('USD 31.25',self.dealer_reply.call_args.args[0])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache").fetchall(),[(31.25,)])

    def test_all_adapters_cloudflare_stop_and_never_use_msrp(self):
        results,_=self.pipeline("CLOUDFLARE")
        self.assertIsNone(results[0]["dp_usd"])
        self.assertNotIn("_dealer_price_usd",results[1])
        self.assertIsNone(results[2]["dl_usd"])
        self.assertEqual(results[1]["_dealer_price_status"],"CLOUDFLARE")
        self.assertEqual(results[2]["status"],"CLOUDFLARE")
        self.assertIsNone(bridge.claim_next(self.db))
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_price_cache").fetchone()[0],0)
        count=len(self.snapshot()['dp_live_requests'])
        def lookup(m,o,*,max_age_hours=168):
            return gateway.request_price(self.db,m,o,wait_seconds=0,max_age_hours=max_age_hours)
        later=[fn() for fn in self.adapters(lookup)]
        self.assertIsNone(later[0]['dp_usd'])
        self.assertEqual(later[1]['_dealer_price_status'],'CLOUDFLARE')
        self.assertEqual(later[2]['status'],'CLOUDFLARE')
        self.assertEqual(len(self.snapshot()['dp_live_requests']),count)
        self.browser.get_dealer_price_dict.assert_not_called()

    def test_all_adapters_auth_required_stop(self):
        results,_=self.pipeline("AUTH_REQUIRED")
        self.assertIsNone(results[0]["dp_usd"])
        self.assertNotIn("_dealer_price_usd",results[1])
        self.assertEqual(results[2]["status"],"AUTH_REQUIRED")
        self.assertIsNone(bridge.claim_next(self.db))

    def test_all_runtime_adapters_closed_miss_has_no_browser_fallback(self):
        from dcp_admission_test_support import approve_mode
        approve_mode(self.db,'CLOSED')
        before=self.snapshot()
        def lookup(m,o,*,max_age_hours=168):
            return gateway.request_price(self.db,m,o,wait_seconds=0,max_age_hours=max_age_hours)
        results=[fn() for fn in self.adapters(lookup)]
        self.assertIsNone(results[0]['dp_usd'])
        self.assertNotIn('_dealer_price_usd',results[1])
        self.assertEqual(results[2]['status'],'ADMISSION_CLOSED')
        self.browser.get_dealer_price_dict.assert_not_called()
        self.assertEqual(self.snapshot(),before)

    def test_runtime_dealer_gateway_error_has_no_browser_fallback(self):
        bad=Mock(side_effect=TimeoutError('mock gateway down'))
        dealer=self.adapters(bad)[2]
        result=dealer()
        self.assertEqual(result['status'],'TECHNICAL_ERROR')
        self.assertIsNone(result['dl_usd'])
        self.browser.get_dealer_price_dict.assert_not_called()
        self.assertEqual(len(self.snapshot()['dp_live_requests']),0)

    def test_runtime_dealer_unverified_gateway_prices_never_found(self):
        base={'status':'FOUND','manufacturer':'Polaris','query_oem':'2206328',
              'currency':'USD','last_verified_at':health._now(),'dealer_price_usd':31.25,
              'source':SOURCE}
        for change in ({'source':'public_msrp'},{'currency':'RUB'},
                       {'dealer_price_usd':None,'msrp_usd':999,'rrp':999},
                       {'last_verified_at':'2020-01-01T00:00:00Z'},{'manufacturer':'Honda'}):
            dealer=self.adapters(lambda m,o,**kw:{**base,**change})[2]
            result=dealer()
            self.assertEqual(result['status'],'DEALER_PRICE_UNVERIFIED')
            self.assertIsNone(result['dl_usd'])
            self.assertNotIn('offer:2206328',self.dealer_context.user_data)
            self.browser.get_dealer_price_dict.assert_not_called()
        self.assertEqual(len(self.snapshot()['dp_live_requests']),0)

    def test_all_adapters_fresh_cache_hit_never_call_live(self):
        self.complete_verified_cache()
        live=Mock(side_effect=AssertionError("cache hit must not request DCP"))
        def lookup(m,o,*,max_age_hours=168):return gateway.request_price(self.db,m,o,live_request=live,max_age_hours=max_age_hours)
        results=[fn() for fn in self.adapters(lookup)]
        self.assertEqual(results[0]["dp_usd"],31.25)
        self.assertEqual(results[1]["_dealer_price_usd"],31.25)
        self.assertEqual(results[2]["dl_usd"],31.25)
        live.assert_not_called()
        self.browser.get_dealer_price_dict.assert_not_called()

    def test_duplicate_and_late_results_do_not_change_adapter_cache(self):
        _,claim=self.pipeline("FOUND")
        before=self.snapshot()
        response=self.post(self.payload(claim,dealer_price_usd=999))
        self.assertEqual(response.status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_dealer_adapter_rejects_retail_msrp_wrong_identity_and_expiry(self):
        base={"status":"FOUND","manufacturer":"Polaris","query_oem":"2206328",
              "currency":"USD","last_verified_at":health._now(),"dealer_price_usd":31.25,
              "source":SOURCE}
        for changes in ({"source":"dealerorders_public_retail"},{"dealer_price_usd":None,"msrp_usd":999},
                        {"currency":"RUB"},{"manufacturer":"Honda"},{"query_oem":"wrong"},
                        {"last_verified_at":"2020-01-01T00:00:00+00:00"}):
            adapter=gateway.SharedDealerPriceAdapter(lambda m,o,**kw:{**base,**changes},max_age_hours=168)
            self.assertEqual(adapter.get_dealer_price_dict("Polaris","2206328")["status"],"DEALER_PRICE_UNVERIFIED")

    def test_gateway_found_without_atomic_cache_commit_is_not_found(self):
        result=gateway.request_price(self.db,"Polaris","2206328",
            live_request=lambda *a,**k:{"status":"FOUND","dealer_price_usd":999,"msrp_usd":999})
        self.assertEqual(result["status"],"NO_PRICE")
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_price_cache").fetchone()[0],0)

    def test_cache_first_survives_cloudflare_block(self):
        self.complete_verified_cache()
        health.set_health(self.db,"CLOUDFLARE")
        live=Mock(side_effect=AssertionError("fresh cache is available"))
        results=[fn() for fn in self.adapters(lambda m,o,**kw:gateway.request_price(self.db,m,o,live_request=live,**kw))]
        self.assertEqual(results[0]["dp_usd"],31.25)
        self.assertEqual(results[1]["_dealer_price_usd"],31.25)
        self.assertEqual(results[2]["dl_usd"],31.25)
        live.assert_not_called()

    def test_crashed_producer_keeps_all_real_adapters_fail_closed_until_admin_recovery(self):
        claim=self.claim()
        def lookup(m,o,*,max_age_hours=168):
            return gateway.request_price(self.db,m,o,wait_seconds=0,max_age_hours=max_age_hours)
        results=[fn() for fn in self.adapters(lookup)]
        self.assertIsNone(results[0]["dp_usd"])
        self.assertNotIn("_dealer_price_usd",results[1])
        self.assertEqual(results[2]["status"],"TIMEOUT")
        self.assertIsNone(bridge.claim_next(self.db,producer_id="other-worker"))
        self.expire_and_quarantine(claim)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET orphaned_at=?",((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),))
        health.set_health(self.db,"READY")
        results=[fn() for fn in self.adapters(lookup)]
        self.assertIsNone(results[0]["dp_usd"])
        self.assertEqual(results[1]["_dealer_price_status"],"TECHNICAL_ERROR")
        self.assertEqual(results[2]["status"],"TECHNICAL_ERROR")
        self.assertIsNone(bridge.claim_next(self.db))
        plan=recovery.recovery_plan(self.db,claim["request_id"])
        recovery.revoke_orphan(self.db,claim["request_id"],approved_plan_sha256=plan["plan_sha256"],
            operator="isolated-admin",producer_inactive_confirmed=True,evidence_sha256="a"*64)
        self.assertIsNone(bridge.claim_next(self.db))
        results,new=self.pipeline("FOUND",expected_requests=2)
        self.assertEqual(results[2]["dl_usd"],31.25)
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim,dealer_price_usd=999)).status_code,409)
        self.assertEqual(before,self.snapshot())
        self.assertNotEqual(new["claim_token"],claim["claim_token"])

    def test_ready_after_challenge_allows_new_explicit_adapter_request(self):
        self.pipeline("CLOUDFLARE")
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_coordination SET blocked_at=?",((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),))
        health.set_health(self.db,"READY")
        results,_=self.pipeline("FOUND",expected_requests=2)
        self.assertEqual(results[0]["dp_usd"],31.25)
        self.assertEqual(results[1]["_dealer_price_usd"],31.25)
        self.assertEqual(results[2]["dl_usd"],31.25)


if __name__=="__main__":unittest.main()
