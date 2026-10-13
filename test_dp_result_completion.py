"""Actual HTTP handlers, signed mock producer, disposable SQLite; no runtime imports."""
import ast
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import tempfile
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import patch

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import BaseModel

import dp_live_bridge as bridge
import dp_live_health as health
import dp_result_completion as completion
import dp_sync_protocol as protocol
import dp_claim_recovery as recovery

ROOT=Path(__file__).resolve().parent
KEY=b"isolated-producer-key-not-a-production-secret"
OWNER="mock-producer-1"
PATH="/internal/dp-sync/result"
RAW_SOURCE="server_html:#add_2670-265 .c2 .dbl"


def build_http(db):
    app=FastAPI()
    names={"DPSyncResultRequest","_require_dp_sync_signature","dp_sync_next","dp_sync_result"}
    nodes=[n for n in ast.parse((ROOT/"web_app.py").read_text()).body
           if isinstance(n,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    assert len(nodes)==len(names)
    ns={"app":app,"Header":Header,"HTTPException":HTTPException,"Request":Request,
        "BaseModel":BaseModel,"Any":Any,"core":SimpleNamespace(ORDERS_DB_FILE=db),
        "dp_live_bridge":bridge,"dp_result_completion":completion,"dp_sync_protocol":protocol,
        "_dp_sync_key":lambda:KEY,"json":json,"sqlite3":sqlite3,"time":time,
        "secrets":secrets,"hmac":hmac,"hashlib":hashlib}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"web_app.py","exec"),ns)
    return TestClient(app)


def headers(method,path,body=b"",producer_id=OWNER,timestamp=None):
    ts=int(time.time()) if timestamp is None else timestamp
    return {"X-DP-Protocol":"2","X-DP-Producer-ID":producer_id,"X-DP-Sync-Ts":str(ts),
            "X-DP-Sync-Sig":protocol.sign(KEY,method,path,ts,producer_id,body),"Content-Type":"application/json"}


class CompletionFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"mock.db"
        self.env=patch.dict(os.environ,{"EXTREMIZER_DP_HEALTH_ENFORCE":"1","EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS":"0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.addCleanup(self.tmp.cleanup)
        self.network=patch.object(socket.socket,"connect",side_effect=AssertionError("external network forbidden"))
        self.network.start();self.addCleanup(self.network.stop)
        bridge.init(self.db);health.set_health(self.db,"READY")
        with sqlite3.connect(self.db) as c:
            completion._write_cache(c,"Arctic Cat","2670-265",99,"verified_local_dcp:"+RAW_SOURCE,health._now())
            completion._write_identity(c,"Honda","91303-PA9-004","91303-PA9-004","fixture","fixture",health._now())
            c.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY,marker TEXT)")
            c.execute("INSERT INTO orders VALUES(1,'unchanged')")
            c.execute("CREATE TABLE finance_ledger(id INTEGER PRIMARY KEY,amount REAL)")
            c.execute("INSERT INTO finance_ledger VALUES(1,100)")
        self.client=build_http(self.db)
        self.addCleanup(self.client.close)

    def enqueue(self,manufacturer="Arctic Cat",oem="2670-265"):
        return bridge.request_live_dp(self.db,manufacturer,oem,wait_seconds=0)

    def claim(self,manufacturer="Arctic Cat",oem="2670-265"):
        self.enqueue(manufacturer,oem)
        response=self.client.get("/internal/dp-sync/next",headers=headers("GET","/internal/dp-sync/next"))
        self.assertEqual(response.status_code,200,response.text)
        value=response.json()["request"]
        self.assertIsNotNone(value)
        return value

    def payload(self,claim,**changes):
        value={"request_id":claim["request_id"],"protocol_version":2,"producer_id":claim["producer_id"],
               "claim_token":claim["claim_token"],"claim_generation":claim["claim_generation"],
               "status":"FOUND","manufacturer":"Arctic Cat","query_oem":"2670-265",
               "matched_sku":"2670-265","current_oem":"2670-265","dealer_price_usd":31.25,
               "currency":"USD","source":RAW_SOURCE,"name":"Mock dealer row"}
        return {**value,**changes}

    def post(self,payload,**changes):
        body=json.dumps(payload,separators=(",", ":")).encode()
        return self.client.post(PATH,content=body,headers={**headers("POST",PATH,body),**changes})

    def snapshot(self):
        with sqlite3.connect(self.db) as c:
            names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
            return {n:c.execute('SELECT * FROM "'+n+'" ORDER BY rowid').fetchall() for n in names}

    def expire_and_quarantine(self,claim):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET lease_expires_at=? WHERE request_id=?",
                      ((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),claim["request_id"]))
        self.assertIsNone(bridge.claim_next(self.db))


class AtomicHttpTests(CompletionFixture):
    def test_found_commits_cache_identity_and_request_together(self):
        claim=self.claim("__AUTO__")
        before=self.snapshot()
        response=self.post(self.payload(claim,current_oem="2670-266"))
        self.assertEqual(response.status_code,200,response.text)
        after=self.snapshot()
        self.assertEqual(after["orders"],before["orders"])
        self.assertEqual(after["finance_ledger"],before["finance_ledger"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache WHERE manufacturer='Arctic Cat' ORDER BY oem").fetchall(),[(31.25,),(31.25,)])
            self.assertEqual(c.execute("SELECT manufacturer,current_oem FROM probnik_oem_identity WHERE oem='2670-265'").fetchone(),("Arctic Cat","2670-266"))
            self.assertEqual(c.execute("SELECT status,result_status FROM dp_live_requests").fetchone(),("finished","FOUND"))

    def test_duplicate_is_409_and_no_second_write(self):
        claim=self.claim();p=self.payload(claim)
        self.assertEqual(self.post(p).status_code,200)
        before=self.snapshot()
        self.assertEqual(self.post({**p,"dealer_price_usd":444}).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_two_conflicting_results_commit_only_one(self):
        claim=self.claim();p=self.payload(claim)
        with ThreadPoolExecutor(max_workers=2) as pool:
            codes=list(pool.map(lambda price:self.post({**p,"dealer_price_usd":price}).status_code,[31.25,44.5]))
        self.assertEqual(sorted(codes),[200,409])
        with sqlite3.connect(self.db) as c:
            self.assertIn(c.execute("SELECT dealer_price_usd FROM dealer_price_cache WHERE oem='2670-265'").fetchone()[0],(31.25,44.5))

    def test_pending_result_cannot_write_cache(self):
        request=self.enqueue()
        p=self.payload({"request_id":request["request_id"],"producer_id":OWNER,"claim_token":"not-claimed","claim_generation":1})
        before=self.snapshot()
        self.assertEqual(self.post(p).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_unknown_request_cannot_write(self):
        p=self.payload({"request_id":"absent","producer_id":OWNER,"claim_token":"absent","claim_generation":1})
        before=self.snapshot()
        self.assertEqual(self.post(p).status_code,404)
        self.assertEqual(before,self.snapshot())

    def test_late_result_is_rollback_even_before_quarantine_scan(self):
        claim=self.claim()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET lease_expires_at=?",((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),))
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim)).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_fence_owner_generation_and_token_must_match(self):
        claim=self.claim()
        for field,value in (("claim_token","different"),("claim_generation",2),("producer_id","different"),("protocol_version",1)):
            with self.subTest(field=field):
                before=self.snapshot()
                self.assertEqual(self.post(self.payload(claim,**{field:value})).status_code,409)
                self.assertEqual(before,self.snapshot())

    def test_invalid_price_currency_oem_manufacturer_source_no_writes(self):
        claim=self.claim()
        changes=[{"dealer_price_usd":v} for v in (True,0,-1,float("nan"),float("inf"))]
        changes += [{"currency":"RUB"},{"manufacturer":"Honda"},{"query_oem":"2670-266"},
                    {"matched_sku":"2670-999"},{"source":"public_msrp"},{"source":"dealerorders_public_retail"},
                    {"source":RAW_SOURCE+":rrp"}]
        for change in changes:
            with self.subTest(change=change):
                before=self.snapshot()
                self.assertEqual(self.post(self.payload(claim,**change)).status_code,400)
                self.assertEqual(before,self.snapshot())

    def test_auto_conflicting_confirmed_identity_cannot_be_overwritten(self):
        with sqlite3.connect(self.db) as c:
            completion._write_identity(c,"Honda","2670-265","2670-265","existing","existing",health._now())
        claim=self.claim("__AUTO__");before=self.snapshot()
        response=self.post(self.payload(claim))
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(before,self.snapshot())

    def test_failure_after_cache_write_rolls_everything_back(self):
        claim=self.claim("__AUTO__")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TRIGGER fail_identity BEFORE INSERT ON probnik_oem_identity BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim)).status_code,500)
        self.assertEqual(before,self.snapshot())

    def test_failure_on_alias_cache_write_rolls_first_upsert_back(self):
        claim=self.claim()
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TRIGGER fail_alias BEFORE INSERT ON dealer_price_cache WHEN NEW.oem='2670-266' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim,current_oem="2670-266")).status_code,500)
        self.assertEqual(before,self.snapshot())

    def test_failure_on_request_completion_rolls_identity_and_cache_back(self):
        claim=self.claim("__AUTO__")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TRIGGER fail_finish BEFORE UPDATE ON dp_live_requests WHEN NEW.status='finished' BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim)).status_code,500)
        self.assertEqual(before,self.snapshot())

    def test_blocked_auto_result_finishes_without_false_identity(self):
        claim=self.claim("__AUTO__")
        response=self.post(self.payload(claim,status="CLOUDFLARE",manufacturer=None,query_oem=None,matched_sku=None))
        self.assertEqual(response.status_code,200,response.text)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache WHERE oem='2670-265'").fetchone()[0],99)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM probnik_oem_identity WHERE oem='2670-265'").fetchone()[0],0)
        self.assertEqual(self.enqueue()["status"],"CLOUDFLARE")

    def test_error_results_preserve_previous_verified_price(self):
        for status in ("NOT_FOUND","NO_PRICE","TECHNICAL_ERROR"):
            claim=self.claim()
            before=self.snapshot()["dealer_price_cache"]
            self.assertEqual(self.post(self.payload(claim,status=status,dealer_price_usd=555)).status_code,200)
            self.assertEqual(before,self.snapshot()["dealer_price_cache"])

    def test_commit_failure_rolls_all_result_writes_back(self):
        claim=self.claim("__AUTO__")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE fk_parent(id INTEGER PRIMARY KEY)")
            c.execute("CREATE TABLE fk_child(parent_id INTEGER REFERENCES fk_parent(id) DEFERRABLE INITIALLY DEFERRED)")
            c.execute("CREATE TRIGGER fail_commit AFTER UPDATE ON dp_live_requests WHEN NEW.status='finished' BEGIN INSERT INTO fk_child VALUES(999); END")
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim)).status_code,500)
        self.assertEqual(before,self.snapshot())

    def test_found_during_session_block_does_not_write(self):
        claim=self.claim()
        for status in ("CLOUDFLARE","AUTH_REQUIRED"):
            health.set_health(self.db,status)
            before=self.snapshot()
            self.assertEqual(self.post(self.payload(claim)).status_code,409)
            self.assertEqual(before,self.snapshot())

    def test_unsigned_tampered_stale_and_wrong_method_signature_no_writes(self):
        claim=self.claim();p=self.payload(claim);body=json.dumps(p).encode()
        cases=[{},headers("POST",PATH,body+b"tampered"),headers("GET",PATH,body),
               headers("POST",PATH,body,timestamp=int(time.time())-61)]
        for supplied in cases:
            before=self.snapshot()
            response=self.client.post(PATH,content=body,headers={"Content-Type":"application/json",**supplied})
            self.assertEqual(response.status_code,401,response.text)
            self.assertEqual(before,self.snapshot())

    def test_legacy_agent_never_receives_new_fenced_work(self):
        self.enqueue()
        ts=int(time.time());path="/internal/dp-sync/next"
        sig=hmac.new(KEY,f"v1\n{path}\n{ts}".encode(),hashlib.sha256).hexdigest()
        response=self.client.get(path,headers={"X-DP-Sync-Ts":str(ts),"X-DP-Sync-Sig":sig})
        self.assertEqual(response.status_code,200)
        self.assertIsNone(response.json()["request"])
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT status FROM dp_live_requests").fetchone()[0],"pending")


class RecoveryTests(CompletionFixture):
    def ready(self,claim):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET orphaned_at=? WHERE request_id=?",((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),claim["request_id"]))
        health.set_health(self.db,"READY")

    def revoke(self,claim,**changes):
        plan=recovery.recovery_plan(self.db,claim["request_id"])
        return recovery.revoke_orphan(self.db,claim["request_id"],**{
            "approved_plan_sha256":plan["plan_sha256"],"operator":"isolated-test-admin",
            "producer_inactive_confirmed":True,"evidence_sha256":"a"*64,**changes})

    def test_client_timeout_is_not_producer_loss(self):
        claim=self.claim()
        request=self.enqueue()
        self.assertEqual(request["status"],"TIMEOUT")
        self.assertEqual(request["request_id"],claim["request_id"])
        self.assertIsNone(bridge.claim_next(self.db,producer_id="other-producer"))
        self.assertEqual(self.post(self.payload(claim)).status_code,200)

    def test_expiry_quarantines_without_second_lookup_even_on_ready(self):
        claim=self.claim();self.expire_and_quarantine(claim)
        self.ready(claim)
        self.assertIsNone(bridge.claim_next(self.db,producer_id="new-producer"))
        self.assertEqual(self.enqueue()["error_code"],"claim_recovery_required")
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim)).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_manual_confirmation_and_fresh_ready_both_required(self):
        claim=self.claim();self.expire_and_quarantine(claim)
        with self.assertRaises(completion.CompletionError):self.revoke(claim)
        self.ready(claim)
        for changes in ({"producer_inactive_confirmed":False},{"operator":""},{"approved_plan_sha256":"bad"},{"evidence_sha256":"bad"}):
            before=self.snapshot()
            with self.assertRaises(completion.CompletionError):self.revoke(claim,**changes)
            self.assertEqual(before,self.snapshot())

    def test_cloudflare_auth_and_future_ready_cannot_release_orphan(self):
        claim=self.claim();self.expire_and_quarantine(claim)
        self.ready(claim)
        for status in ("CLOUDFLARE","AUTH_REQUIRED"):
            health.set_health(self.db,status)
            with self.assertRaises(completion.CompletionError):self.revoke(claim)
        health.set_health(self.db,"READY",checked_at=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat())
        with self.assertRaises(completion.CompletionError):self.revoke(claim)

    def test_revoke_fences_old_owner_and_never_auto_requeues(self):
        claim=self.claim();self.expire_and_quarantine(claim);self.ready(claim)
        result=self.revoke(claim)
        self.assertFalse(result["requeued"])
        self.assertIsNone(bridge.claim_next(self.db))
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim)).status_code,409)
        self.assertEqual(before,self.snapshot())
        new_claim=self.claim()
        self.assertNotEqual(new_claim["request_id"],claim["request_id"])
        self.assertNotEqual(new_claim["claim_token"],claim["claim_token"])
        before=self.snapshot()
        self.assertEqual(self.post(self.payload(claim,dealer_price_usd=555)).status_code,409)
        self.assertEqual(before,self.snapshot())

    def test_plan_is_read_only_and_contains_token_hash_only(self):
        claim=self.claim();self.expire_and_quarantine(claim)
        before=self.snapshot()
        plan=recovery.recovery_plan(self.db,claim["request_id"])
        self.assertNotIn(claim["claim_token"],json.dumps(plan))
        self.assertEqual(before,self.snapshot())

    def test_changed_claim_invalidates_plan(self):
        claim=self.claim();self.expire_and_quarantine(claim);self.ready(claim)
        plan=recovery.recovery_plan(self.db,claim["request_id"])
        with sqlite3.connect(self.db) as c:c.execute("UPDATE dp_live_requests SET claim_generation=claim_generation+1")
        before=self.snapshot()
        with self.assertRaises(completion.CompletionError):self.revoke(claim,approved_plan_sha256=plan["plan_sha256"])
        self.assertEqual(before,self.snapshot())

    def test_recovery_audit_failure_rolls_revocation_back(self):
        claim=self.claim();self.expire_and_quarantine(claim);self.ready(claim)
        with sqlite3.connect(self.db) as c:c.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON dp_live_recovery_events BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
        before=self.snapshot()
        with self.assertRaises(sqlite3.Error):self.revoke(claim)
        self.assertEqual(before,self.snapshot())

    def test_legacy_unfenced_claim_is_quarantined_not_reissued(self):
        req=self.enqueue()
        with sqlite3.connect(self.db) as c:c.execute("UPDATE dp_live_requests SET status='claimed',claim_token=NULL WHERE request_id=?",(req["request_id"],))
        self.assertIsNone(bridge.claim_next(self.db))
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute("SELECT status FROM dp_live_requests").fetchone()[0],"orphaned")

    def test_legacy_internal_finish_cannot_accept_expired_fenced_claim(self):
        claim=self.claim()
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET lease_expires_at=?",(datetime.now(timezone.utc).replace(microsecond=1).isoformat(),))
        before=self.snapshot()
        self.assertFalse(bridge.finish(self.db,claim["request_id"],result_status="FOUND",dealer_price_usd=31.25,
            source="verified_local_dcp:"+RAW_SOURCE,claim_token=claim["claim_token"],
            claim_generation=claim["claim_generation"],producer_id=claim["producer_id"]))
        self.assertEqual(before,self.snapshot())


if __name__ == "__main__":unittest.main()
