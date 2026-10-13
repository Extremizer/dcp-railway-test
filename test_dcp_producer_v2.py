"""Actual producer + actual HTTP handlers; fake browser, disposable DB only."""
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import sys
from types import SimpleNamespace
import urllib.error
from unittest.mock import Mock, patch

from test_dp_result_completion import CompletionFixture, KEY, OWNER, RAW_SOURCE
import dp_live_bridge as bridge


class ProducerV2Tests(CompletionFixture):
    def setUp(self):
        super().setUp()
        self.browser = SimpleNamespace(get_dealer_price_dict=Mock(return_value={
            "status":"FOUND","manufacturer":"Arctic Cat","query_oem":"2670-265",
            "matched_sku":"2670-265","current_oem":"2670-265","dealer_price_usd":31.25,
            "source":RAW_SOURCE,"name":"fake DCP dealer row"}))
        with patch.dict(sys.modules, {"oemixibot_dcp_price":self.browser,
                                     "dealercostparts_manufacturer_finder_v6_6":SimpleNamespace(CATALOGS=[])}):
            spec=importlib.util.spec_from_file_location("isolated_stage3_agent",Path(__file__).with_name("dp_sync_agent.py"))
            self.agent=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.agent)
        self.agent.PRODUCER_ID=OWNER
        self.agent.JOURNAL_FILE=Path(self.tmp.name)/"inflight.json"
        self.agent.STATUS_FILE=Path(self.tmp.name)/"status.json"
        self.agent._key=lambda:KEY
        self.agent._DCP_STATUS="READY"
        self.agent._set_dcp_health=Mock()
        self.requests=[];self.drop_after_commit=False;self.drop_claim=False;self.tamper=False;self.bad_signature=False
        self.opener=patch("urllib.request.urlopen",side_effect=self.urlopen)
        self.opener.start();self.addCleanup(self.opener.stop)

    def urlopen(self,req,timeout):
        path=req.full_url.removeprefix(self.agent.BASE_URL)
        self.assertIn(path,{"/internal/dp-sync/next","/internal/dp-sync/result"})
        h=dict(req.header_items());body=req.data or b""
        self.requests.append((req.get_method(),path,body,h))
        self.assertEqual(h.get("X-dp-protocol"),"2")
        if self.tamper and path.endswith("result"):
            body=body.replace(b"31.25",b"999.0")
        if self.bad_signature:h["X-dp-sync-sig"]="0"*64
        r=self.client.request(req.get_method(),path,content=body,headers=h)
        if self.drop_claim and path.endswith("next"):raise TimeoutError("claim response lost")
        if self.drop_after_commit and path.endswith("result"):
            self.assertEqual(r.status_code,200,r.text)
            raise TimeoutError("server committed; ACK lost")
        if r.status_code>=400:raise urllib.error.HTTPError(req.full_url,r.status_code,"mock",None,io.BytesIO(r.content))
        return io.BytesIO(r.content)

    def test_exact_signed_get_and_post_complete_real_server(self):
        self.enqueue();self.assertTrue(self.agent.run_once())
        self.assertEqual(len(self.requests),2)
        payload=json.loads(self.requests[1][2])
        self.assertEqual(payload["protocol_version"],2)
        self.assertEqual(payload["producer_id"],OWNER)
        self.assertEqual(payload["currency"],"USD")
        self.assertTrue(payload["claim_token"])
        self.assertFalse(self.agent.JOURNAL_FILE.exists())
        with sqlite3.connect(self.db) as c:
            row=c.execute("SELECT status,cache_committed_at FROM dp_live_requests").fetchone()
        self.assertEqual(row[0],"finished");self.assertTrue(row[1])

    def test_timeout_after_commit_no_second_lookup_even_after_ready(self):
        self.enqueue();self.drop_after_commit=True
        with self.assertRaises(TimeoutError):self.agent.run_once()
        self.agent._DCP_STATUS="READY"
        self.assertFalse(self.agent.run_once())
        self.browser.get_dealer_price_dict.assert_called_once()
        self.assertEqual(len(self.requests),2)
        data=json.loads(self.agent.JOURNAL_FILE.read_text())
        self.assertEqual(data["phase"],"result_delivery_uncertain")
        self.assertNotIn("claim_token",data)
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache WHERE manufacturer='Arctic Cat'").fetchone()[0],31.25)

    def test_claim_timeout_can_leave_claim_but_never_browser_lookup(self):
        self.enqueue();self.drop_claim=True
        with self.assertRaises(TimeoutError):self.agent.run_once()
        self.assertFalse(self.agent.run_once());self.browser.get_dealer_price_dict.assert_not_called()
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("SELECT status FROM dp_live_requests").fetchone()[0],"claimed")

    def test_body_tamper_401_rolls_back_and_stops(self):
        self.enqueue();self.tamper=True;before=self.snapshot()["dealer_price_cache"]
        with self.assertRaises(urllib.error.HTTPError) as e:self.agent.run_once()
        self.assertEqual(e.exception.code,401)
        self.assertEqual(self.snapshot()["dealer_price_cache"],before)
        self.assertFalse(self.agent.run_once());self.browser.get_dealer_price_dict.assert_called_once()

    def test_bad_signature_get_never_calls_browser(self):
        self.enqueue();self.bad_signature=True
        with self.assertRaises(urllib.error.HTTPError) as e:self.agent.run_once()
        self.assertEqual(e.exception.code,401);self.browser.get_dealer_price_dict.assert_not_called()
        self.assertFalse(self.agent.run_once())

    def test_409_delivery_is_latched_without_retry(self):
        claim=self.claim();payload=self.agent._validated_result(claim,self.browser.get_dealer_price_dict.return_value)
        claim["claim_generation"]+=1
        with self.assertRaises(urllib.error.HTTPError) as e:self.agent._finish(claim,payload)
        self.assertEqual(e.exception.code,409)
        self.assertEqual(self.snapshot()["dealer_price_cache"][0][2],99)

    def test_cloudflare_and_auth_stop_before_result_delivery(self):
        for status in ("CLOUDFLARE","AUTH_REQUIRED"):
            with self.subTest(status=status):
                self.agent._PRODUCER_STOP=False
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM dp_live_requests")
                    c.execute("UPDATE dp_live_coordination SET blocked_status=NULL,blocked_at=NULL")
                self.enqueue();self.browser.get_dealer_price_dict.return_value={"status":status}
                self.assertTrue(self.agent.run_once());self.assertTrue(self.agent._PRODUCER_STOP)
                self.agent._set_dcp_health.assert_called_with(status,detail=status)
                self.assertFalse(self.agent.run_once())

    def test_disk_journal_survives_new_boot_and_blocks_main_before_health(self):
        self.agent._journal("lookup_inflight",{"request_id":"test","claim_generation":1})
        self.agent._PRODUCER_STOP=False
        self.agent._acquire_singleton=lambda:True
        self.agent._preflight_dcp=Mock(side_effect=AssertionError("no browser before reconciliation"))
        self.assertEqual(self.agent.main(),24)
        self.browser.get_dealer_price_dict.assert_not_called()

    def test_producer_id_unique_per_import_and_stable_across_requests(self):
        self.enqueue();self.agent.run_once()
        self.assertEqual({r[3]["X-dp-producer-id"] for r in self.requests},{OWNER})
        source=Path(self.agent.__file__).read_text()
        self.assertIn('PRODUCER_ID = "dp-v2-" + uuid.uuid4().hex',source)

    def test_source_sku_price_currency_and_identity_fail_closed(self):
        item={"manufacturer":"Arctic Cat","oem":"2670-265"};base=self.browser.get_dealer_price_dict.return_value
        for change in ({"source":"retail"},{"source":"#add_2670-265 .c2 .dbl"},{"matched_sku":"9999"},
                       {"manufacturer":"Honda"},{"query_oem":"9999"},{"dealer_price_usd":True},
                       {"dealer_price_usd":float("inf")},{"dealer_price_usd":0},{"currency":"RUB"},
                       {"dealer_price_usd":None,"msrp_usd":999}):
            self.assertEqual(self.agent._validated_result(item,{**base,**change})["status"],"TECHNICAL_ERROR")
        self.assertEqual(self.agent._validated_result(item,base)["status"],"FOUND")

    def test_claim_wrong_owner_old_protocol_and_expired_lease_no_lookup(self):
        base={"protocol_version":2,"producer_id":OWNER,"claim_token":"abc","claim_generation":1,
              "request_id":"req","manufacturer":"Arctic Cat","oem":"2670-265","lease_expires_at":"2099-01-01T00:00:00+00:00"}
        for change in ({"producer_id":"other"},{"protocol_version":1},{"claim_generation":True},
                       {"claim_token":""},{"lease_expires_at":"2020-01-01T00:00:00+00:00"}):
            with patch.object(self.agent,"_request",return_value={"request":{**base,**change}}):
                with self.assertRaises(ValueError):self.agent._claim()
        self.browser.get_dealer_price_dict.assert_not_called()

    def test_server_unavailable_never_falls_back_v1(self):
        with patch("urllib.request.urlopen",side_effect=ConnectionError("offline")):
            with self.assertRaises(ConnectionError):self.agent.run_once()
        self.assertTrue(self.agent._PRODUCER_STOP);self.assertFalse(self.agent.run_once())
        self.browser.get_dealer_price_dict.assert_not_called()

    def test_crash_during_lookup_quarantine_and_manual_recovery_no_retry(self):
        from datetime import datetime, timezone, timedelta
        import dp_claim_recovery as recovery
        import dp_live_health as health
        self.enqueue()
        self.browser.get_dealer_price_dict.side_effect=KeyboardInterrupt("simulated producer crash")
        with self.assertRaises(KeyboardInterrupt):self.agent.run_once()
        with sqlite3.connect(self.db) as c:
            request_id=c.execute("SELECT request_id FROM dp_live_requests").fetchone()[0]
            c.execute("UPDATE dp_live_requests SET lease_expires_at=?",((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),))
        self.assertIsNone(bridge.claim_next(self.db,producer_id="replacement"))
        self.assertEqual(self.enqueue()["error_code"],"claim_recovery_required")
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET orphaned_at=?",((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),))
        health.set_health(self.db,"READY")
        plan=recovery.recovery_plan(self.db,request_id)
        r=recovery.revoke_orphan(self.db,request_id,approved_plan_sha256=plan["plan_sha256"],
            operator="isolated-admin",producer_inactive_confirmed=True,evidence_sha256="a"*64)
        self.assertFalse(r["requeued"])
        self.assertIsNone(bridge.claim_next(self.db))
        self.agent._PRODUCER_STOP=False
        self.assertFalse(self.agent.run_once())  # Server recovery never clears disk uncertainty.
        self.browser.get_dealer_price_dict.assert_called_once()
