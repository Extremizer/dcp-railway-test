"""Stage 10 epoch binding against accepted PR64 bridge and atomic completion.

All tests use disposable SQLite, fake source and in-process signed HTTP.
"""
from unittest.mock import patch
from concurrent.futures import ProcessPoolExecutor
import os
import sqlite3
import json
import dp_sync_protocol
from test_dp_result_completion import KEY, OWNER
import unittest

from test_dp_result_completion import CompletionFixture, headers
import dp_live_bridge as bridge
import dp_result_completion as completion
import dcp_stage10_ownership_epoch as epoch

def _independent_host_claim(arguments):
    db_file, producer, proof = arguments
    os.environ["EXTREMIZER_DP_OWNERSHIP_ENFORCE"] = "1"
    claim = bridge.claim_next(db_file, producer_id=producer, ownership_proof=proof)
    return claim is not None


class EpochBrokerIntegrationTests(CompletionFixture):
    def test_epoch_required_for_claim_and_stale_cannot_claim(self):
        self.enqueue()
        old=epoch.acquire(self.db,"old-host")
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            self.assertIsNone(bridge.claim_next(self.db,producer_id="new-host"))
            self.assertIsNone(bridge.claim_next(self.db,producer_id="old-host"))
            claimed=bridge.claim_next(self.db,producer_id="old-host",ownership_proof=old)
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed["ownership_epoch"],old["epoch"])
            self.assertIsNone(bridge.claim_next(self.db,producer_id="new-host",ownership_proof=old))

    def test_completion_denies_missing_or_stale_proof_with_no_cache_mutation(self):
        self.enqueue()
        old=epoch.acquire(self.db,"mock-producer-1")
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            claim=bridge.claim_next(self.db,producer_id="mock-producer-1",ownership_proof=old)
            self.assertIsNotNone(claim)
            payload=self.payload(claim)
            with sqlite3.connect(self.db) as c:
                before=c.execute("SELECT * FROM dealer_price_cache ORDER BY manufacturer,oem").fetchall()
            for proof in (None,{"epoch":old["epoch"]-1,"owner":old["owner"],"nonce":old["nonce"]}):
                denied=self.post({**payload,"ownership_proof":proof})
                self.assertEqual(denied.status_code,409,denied.text)
            with sqlite3.connect(self.db) as c:
                after=c.execute("SELECT * FROM dealer_price_cache ORDER BY manufacturer,oem").fetchall()
                still_claimed=c.execute("SELECT status FROM dp_live_requests WHERE request_id=?",
                    (claim["request_id"],)).fetchone()[0]
            self.assertEqual(before,after)
            self.assertEqual(still_claimed,"claimed")

    def test_signed_http_claim_requires_correct_epoch_headers(self):
        self.enqueue()
        owner=epoch.acquire(self.db,"mock-producer-1")
        req_headers=headers("GET","/internal/dp-sync/next")
        def signed_epoch(number,nonce):
            binding=json.dumps({"epoch":number,"nonce":nonce},sort_keys=True,separators=(",",":")).encode()
            h=dict(req_headers)
            h.update({"X-DP-Ownership-Epoch":str(number),"X-DP-Ownership-Nonce":nonce})
            h["X-DP-Sync-Sig"]=dp_sync_protocol.sign(
                KEY,"GET","/internal/dp-sync/next",int(h["X-DP-Sync-Ts"]),OWNER,binding)
            return h
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            missing=self.client.get("/internal/dp-sync/next",headers=req_headers)
            self.assertEqual(missing.status_code,409)
            stale=self.client.get("/internal/dp-sync/next",
                headers=signed_epoch(owner["epoch"]-1,owner["nonce"]))
            self.assertEqual(stale.status_code,200)
            self.assertIsNone(stale.json()["request"])
            tampered={**signed_epoch(owner["epoch"],owner["nonce"]),
                      "X-DP-Ownership-Nonce":"0"*64}
            forged=self.client.get("/internal/dp-sync/next",headers=tampered)
            self.assertEqual(forged.status_code,401)
            accepted=self.client.get("/internal/dp-sync/next",
                headers=signed_epoch(owner["epoch"],owner["nonce"]))
            self.assertEqual(accepted.status_code,200)
            self.assertEqual(accepted.json()["request"]["ownership_epoch"],owner["epoch"])

    def test_independent_mock_hosts_compete_for_claim(self):
        self.enqueue()
        owner=epoch.acquire(self.db,"mock-producer-1")
        with ProcessPoolExecutor(max_workers=2) as workers:
            outcome=list(workers.map(_independent_host_claim,[
                (str(self.db),"mock-producer-1",owner),
                (str(self.db),"foreign-producer",owner)]))
        self.assertEqual(outcome.count(True),1)
        self.assertEqual(outcome.count(False),1)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute(
                "SELECT claim_owner FROM dp_live_requests WHERE status='claimed'").fetchone()[0],
                "mock-producer-1")

    def test_epoch_revoke_requires_no_inflight(self):
        self.enqueue()
        old=epoch.acquire(self.db,"old-host")
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            self.assertIsNotNone(bridge.claim_next(self.db,producer_id="old-host",ownership_proof=old))
        with self.assertRaises(epoch.OwnershipDenied):
            epoch.revoke(self.db,expected_epoch=old["epoch"],cessation_confirmed=True,evidence_ref="x"*64)

def load_tests(loader, tests, pattern):
    return unittest.TestSuite(EpochBrokerIntegrationTests(n) for n in sorted(
        name for name in EpochBrokerIntegrationTests.__dict__ if name.startswith("test_")))
