"""Actual signed-v2 agent + Stage9 runner + Stage10 epoch via fake in-process HTTP."""
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

from test_dcp_producer_v2 import ProducerV2Tests
import dcp_stage10_ownership_epoch as ownership
import dcp_stage10_mock_producer as mock_producer
import dcp_stage9_mock_linux_runner as stage9

class Stage10ProducerE2ETests(ProducerV2Tests):
    def setup_runner(self):
        state=Path(self.tmp.name)/"stage10-state"
        state.mkdir(exist_ok=True)
        env={
            "DP_RUNNER_TRANSPORT":"fake",
            "DP_RUNNER_SERVER_URL":"http://127.0.0.1:8999",
            "DP_RUNNER_STATE_DIR":str(state),
            "DP_RUNNER_LOCK_FILE":str(state/"lock"),
            "DP_RUNNER_JOURNAL_FILE":str(state/"inflight.json"),
            "DP_RUNNER_STATUS_FILE":str(state/"status.json"),
        }
        return stage9.configuration(env)

    def run_denial(self,status):
        self.enqueue()
        cfg=self.setup_runner()
        proof=ownership.acquire(self.db,self.agent.PRODUCER_ID)
        supplier=Mock(return_value={"status":status,"msrp_usd":999})
        with sqlite3.connect(self.db) as conn:
            before=conn.execute("SELECT * FROM dealer_price_cache ORDER BY manufacturer,oem").fetchall()
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            exitcode=mock_producer.execute(cfg,self.agent,proof,
                approved_identities={("Arctic Cat","2670-265")},lookup=supplier)
        self.assertEqual(exitcode,24)
        supplier.assert_called_once()
        with sqlite3.connect(self.db) as conn:
            after=conn.execute("SELECT * FROM dealer_price_cache ORDER BY manufacturer,oem").fetchall()
            result=conn.execute("SELECT result_status,cache_committed_at FROM dp_live_requests").fetchone()
        self.assertEqual(before,after)
        self.assertEqual(result,(status,None))
        self.assertFalse(cfg["journal"].exists())
        self.assertEqual(json.loads(cfg["status"].read_text())["runner"],"quarantined")
        self.assertTrue(self.agent._PRODUCER_STOP)

    def test_actual_v2_auth_required_no_found(self):
        self.run_denial("AUTH_REQUIRED")

    def test_actual_v2_cloudflare_no_found(self):
        self.run_denial("CLOUDFLARE")

    def test_stale_epoch_mock_producer_never_looks_up(self):
        self.enqueue()
        cfg=self.setup_runner()
        proof=ownership.acquire(self.db,self.agent.PRODUCER_ID)
        ownership.quarantine(self.db,proof["epoch"])
        supplier=Mock(side_effect=AssertionError("supplier must not run"))
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            outcome=mock_producer.execute(cfg,self.agent,proof,
                approved_identities={("Arctic Cat","2670-265")},lookup=supplier)
        self.assertEqual(outcome,0)
        supplier.assert_not_called()
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT status FROM dp_live_requests").fetchone()[0],"pending")

    def test_lost_ack_crash_latch_blocks_second_lookup(self):
        self.enqueue()
        cfg=self.setup_runner()
        proof=ownership.acquire(self.db,self.agent.PRODUCER_ID)
        self.drop_after_commit=True
        supplier=Mock(return_value={"status":"AUTH_REQUIRED"})
        with patch.dict("os.environ",{"EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1"}):
            self.assertEqual(mock_producer.execute(cfg,self.agent,proof,
                approved_identities={("Arctic Cat","2670-265")},lookup=supplier),24)
            self.assertTrue(cfg["journal"].exists())
            self.assertEqual(mock_producer.execute(cfg,self.agent,proof,
                approved_identities={("Arctic Cat","2670-265")},lookup=supplier),24)
        supplier.assert_called_once()

def load_tests(loader, tests, pattern):
    return loader.suiteClass(Stage10ProducerE2ETests(x) for x in sorted(
        x for x in Stage10ProducerE2ETests.__dict__ if x.startswith("test_")))
