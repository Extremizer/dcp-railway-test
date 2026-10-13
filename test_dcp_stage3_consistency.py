import asyncio
from datetime import datetime
import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import unittest

import dcp_price_gateway as gateway
from dcp_stage3_scope_guard import require_procenka_unchanged
from test_dcp_three_adapters import actual_functions
from test_dp_result_completion import CompletionFixture
import dp_live_bridge as bridge


class SingleWriterTests(CompletionFixture):
    def enrich(self,live,cache_only=True):
        live_call=Mock(return_value=live)
        writer=Mock(side_effect=AssertionError("consumer must never upsert DP"))
        ns=actual_functions("extremizer_bot.py",{"enrich_found_result_with_dealer_price"},{
            "datetime":datetime,"asyncio":asyncio,"DCP_CACHE_ONLY":cache_only,
            "ORDERS_DB_FILE":self.db,"get_dealer_price_cache":lambda *a,**kw:None,
            "upsert_dealer_price_cache":writer,"log":logging.getLogger("isolated-stage3"),
            "dp_live_bridge":SimpleNamespace(request_live_dp=live_call),
            "os":SimpleNamespace(getenv=lambda *a:"0"),
        })
        result=asyncio.run(ns["enrich_found_result_with_dealer_price"]({
            "status":"FOUND","manufacturer":"Arctic Cat","oem":"2670-265","catalog":"Parts"}))
        writer.assert_not_called()
        return result

    def test_found_requires_real_atomic_completion_marker_both_modes(self):
        for mode in (True,False):
            for value in (None,False,1,"true"):
                result=self.enrich({"status":"FOUND","dealer_price_usd":999,"server_cache_committed":value},mode)
                self.assertNotIn("_dealer_price_usd",result)
                self.assertEqual(result["_dealer_price_status"],"TECHNICAL_ERROR")

    def test_confirmed_completion_never_competes_with_newer_cache(self):
        result=self.enrich({"status":"FOUND","dealer_price_usd":31.25,"source":"verified_local_dcp:fixture",
                            "server_cache_committed":True,"cache_committed_at":"2026-10-13T00:00:00Z"})
        self.assertEqual(result["_dealer_price_usd"],31.25)
        self.assertEqual(result["_dealer_price_checked_at"],"2026-10-13T00:00:00Z")
        self.assertEqual(self.snapshot()["dealer_price_cache"][0][2],99)

    def test_only_atomic_server_sets_persisted_proof_legacy_finish_does_not(self):
        r=self.enqueue();claim=bridge.claim_next(self.db,producer_id="legacy-test")
        self.assertTrue(bridge.finish(self.db,r["request_id"],result_status="FOUND",dealer_price_usd=31.25,
            source="verified_local_dcp:fixture",producer_id=claim["producer_id"],
            claim_token=claim["claim_token"],claim_generation=claim["claim_generation"]))
        result=bridge.request_live_dp(self.db,"Arctic Cat","2670-265",wait_seconds=0)
        self.assertIs(result["server_cache_committed"],False)
        self.assertEqual(self.snapshot()["dealer_price_cache"][0][2],99)

    def test_shortage_live_consumer_uses_commit_guard_no_cache_writer(self):
        source=Path(__file__).with_name("extremizer_bot.py").read_text()
        start=source.index('                _live=await asyncio.to_thread(dp_live_bridge.request_live_dp,ORDERS_DB_FILE,_m')
        end=source.index('            if _dp is not None:',start)
        block=source[start:end]
        self.assertIn('_live.get("server_cache_committed") is True',block)
        self.assertNotIn('upsert_dealer_price_cache',block)


class ReadinessTests(unittest.TestCase):
    def test_exact_byte_preservation_outside_two_authorized_spans(self):
        require_procenka_unchanged(Path(__file__).with_name("extremizer_bot.py").read_text())

    def test_unrelated_text_finance_or_checkout_change_rejected(self):
        s=Path(__file__).with_name("extremizer_bot.py").read_text()
        with self.assertRaises(AssertionError):require_procenka_unchanged(s+'\n# unrelated mutation\n')
        with self.assertRaises(AssertionError):require_procenka_unchanged(s.replace('f"🇺🇸 Цена из США для', 'f"Изменённая цена для'))

    def test_missing_gateway_offline_and_invalid_response_fail_closed(self):
        for fn in (None,lambda *a,**k:None,Mock(side_effect=TimeoutError)):
            adapter=gateway.SharedDealerPriceAdapter(fn,max_age_hours=1)
            result=adapter.get_dealer_price_dict("Arctic Cat","2670-265")
            self.assertEqual(result["status"],"TECHNICAL_ERROR")
            self.assertNotIn("dealer_price_usd",result)

    def test_dealer_freshness_is_explicit_no_client_default(self):
        for ttl in (None,0,True,float('inf'),-1):
            with self.assertRaises(ValueError):gateway.SharedDealerPriceAdapter(None,max_age_hours=ttl)
        with self.assertRaises(TypeError):gateway.SharedDealerPriceAdapter(None)

    def test_cache_hit_does_not_claim_it_was_a_new_server_commit(self):
        from test_dp_result_completion import RAW_SOURCE
        import tempfile,sqlite3,dp_result_completion,dp_live_health
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"test.db"
            with sqlite3.connect(db) as c:dp_result_completion._write_cache(c,"Arctic Cat","2670-265",31.25,"verified_local_dcp:"+RAW_SOURCE,dp_live_health._now())
            r=gateway.request_price(db,"Arctic Cat","2670-265",live_request=Mock(side_effect=AssertionError))
            self.assertTrue(r["verified_cache_hit"])
            self.assertNotIn("server_cache_committed",r)
