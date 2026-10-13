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
from unittest.mock import Mock
import unittest

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
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM dealer_price_cache")
            completion._write_identity(c,"Arctic Cat","2670-265","2670-265","known identity","fixture",health._now())
            c.execute("CREATE TABLE oem_catalog_cache(manufacturer,current_oem,name,msrp_usd,last_verified_at,msrp_verified)")
            c.execute("INSERT INTO oem_catalog_cache VALUES('Arctic Cat','2670-265','public catalog',999,?,1)",(health._now(),))
        self.browser=SimpleNamespace(get_dealer_price_dict=Mock(side_effect=AssertionError("direct DCP lookup forbidden")))
        self.finder=SimpleNamespace(manufacturer_alias=lambda m:bridge._request_key(m,"2670-265")[0],
                                    normalize_oem=lambda o:bridge._request_key("Arctic Cat",o)[1])

    def adapters(self,lookup):
        probnik=actual_functions("probnik_app.py",{"_identity_and_price","_customer_price_rub","_db_tables"},{
            "ORDERS_DB":self.db,"DP_MAX_AGE_HOURS":168,"finder":self.finder,
            "sqlite3":sqlite3,"math":math,"normalize_oem":self.finder.normalize_oem,
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
            "oem_identity_service":SimpleNamespace(lookup=lambda o:{"manufacturer":"Arctic Cat","item_type":"parts"}),
            "oemixibot_dcp_price":self.browser,"confirm_identity":Mock(),
        })
        dealer_adapter=gateway.SharedDealerPriceAdapter(lookup,max_age_hours=168)
        return (
            lambda:probnik["_identity_and_price"]("2670-265"),
            lambda:asyncio.run(procenka["enrich_found_result_with_dealer_price"]({
                "status":"FOUND","catalog":"Parts","manufacturer":"Arctic Cat","oem":"2670-265","msrp_usd":999})),
            lambda:dealer["resolve_offer"]("2670-265",price_gateway=dealer_adapter),
        )

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

    def test_all_adapters_auth_required_stop(self):
        results,_=self.pipeline("AUTH_REQUIRED")
        self.assertIsNone(results[0]["dp_usd"])
        self.assertNotIn("_dealer_price_usd",results[1])
        self.assertEqual(results[2]["status"],"AUTH_REQUIRED")
        self.assertIsNone(bridge.claim_next(self.db))

    def test_all_adapters_fresh_cache_hit_never_call_live(self):
        with sqlite3.connect(self.db) as c:
            completion._write_cache(c,"Arctic Cat","2670-265",31.25,"verified_local_dcp:server_html:#add_2670-265 .c2 .dbl",health._now())
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
        base={"status":"FOUND","manufacturer":"Arctic Cat","query_oem":"2670-265",
              "currency":"USD","last_verified_at":health._now(),"dealer_price_usd":31.25,
              "source":"verified_local_dcp:server_html:#add_2670-265 .c2 .dbl"}
        for changes in ({"source":"dealerorders_public_retail"},{"dealer_price_usd":None,"msrp_usd":999},
                        {"currency":"RUB"},{"manufacturer":"Honda"},{"query_oem":"wrong"},
                        {"last_verified_at":"2020-01-01T00:00:00+00:00"}):
            adapter=gateway.SharedDealerPriceAdapter(lambda m,o,**kw:{**base,**changes},max_age_hours=168)
            self.assertEqual(adapter.get_dealer_price_dict("Arctic Cat","2670-265")["status"],"NO_PRICE")

    def test_gateway_found_without_atomic_cache_commit_is_not_found(self):
        result=gateway.request_price(self.db,"Arctic Cat","2670-265",
            live_request=lambda *a,**k:{"status":"FOUND","dealer_price_usd":999,"msrp_usd":999})
        self.assertEqual(result["status"],"NO_PRICE")
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_price_cache").fetchone()[0],0)

    def test_cache_first_survives_cloudflare_block(self):
        with sqlite3.connect(self.db) as c:
            completion._write_cache(c,"Arctic Cat","2670-265",31.25,"verified_local_dcp:server_html:#add_2670-265 .c2 .dbl",health._now())
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
