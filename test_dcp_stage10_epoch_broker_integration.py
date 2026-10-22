"""Stage 10 epoch binding against accepted PR64 bridge and atomic completion.

All tests use disposable SQLite, fake source and in-process signed HTTP.
"""
from unittest.mock import patch
import sqlite3
import unittest

from test_dp_result_completion import CompletionFixture
import dp_live_bridge as bridge
import dp_result_completion as completion
import dcp_stage10_ownership_epoch as epoch

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
