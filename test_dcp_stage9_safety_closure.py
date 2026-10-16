"""Stage 9 safety closure: existing v2 producer, broker and mock HTTP only."""
import json
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import dp_live_bridge as bridge
from test_dcp_producer_v2 import ProducerV2Tests

class Stage9SafetyClosure(ProducerV2Tests):
    def test_synthetic_supplier_denials_use_actual_signed_v2_agent(self):
        for status in ("AUTH_REQUIRED", "CLOUDFLARE"):
            with self.subTest(status=status):
                self.agent._PRODUCER_STOP = False
                self.browser.get_dealer_price_dict.reset_mock()
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM dp_live_requests")
                    c.execute("UPDATE dp_live_coordination SET blocked_status=NULL,blocked_at=NULL")
                self.browser.get_dealer_price_dict.return_value = {"status":status, "msrp_usd":999}
                with sqlite3.connect(self.db) as c:
                    cache_before = c.execute("SELECT * FROM dealer_price_cache ORDER BY manufacturer, oem").fetchall()
                self.enqueue()
                self.assertTrue(self.agent.run_once())
                self.assertTrue(self.agent._PRODUCER_STOP)
                self.assertFalse(self.agent.run_once())
                self.browser.get_dealer_price_dict.assert_called_once()
                with sqlite3.connect(self.db) as c:
                    row = c.execute("SELECT status,result_status,dealer_price_usd,cache_committed_at FROM dp_live_requests").fetchone()
                    cache_after = c.execute("SELECT * FROM dealer_price_cache ORDER BY manufacturer, oem").fetchall()
                    blocked = c.execute("SELECT blocked_status FROM dp_live_coordination WHERE singleton_id=1").fetchone()[0]
                self.assertEqual(row, ("finished", status, None, None))
                self.assertEqual(blocked, status)
                self.assertEqual(cache_after, cache_before)
                self.assertFalse(self.agent.JOURNAL_FILE.exists())

    def test_two_hosts_first_claim_excludes_other_owner(self):
        self.enqueue()
        self.enqueue("Honda", "91303-PA9-004")
        old = bridge.claim_next(self.db, producer_id="old-host")
        self.assertIsNotNone(old)
        self.assertIsNone(bridge.claim_next(self.db, producer_id="new-host"))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dp_live_requests WHERE status='claimed'").fetchone()[0],1)

    def test_delayed_old_host_and_new_host_cannot_take_expired_claim(self):
        self.enqueue()
        old = bridge.claim_next(self.db, producer_id="old-host")
        self.assertIsNotNone(old)
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET lease_expires_at=? WHERE request_id=?",
                ((datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat(),old["request_id"]))
        self.assertIsNone(bridge.claim_next(self.db, producer_id="new-host"))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT status FROM dp_live_requests WHERE request_id=?",
                (old["request_id"],)).fetchone()[0],"orphaned")
        self.assertFalse(bridge.finish(self.db,old["request_id"],result_status="FOUND",
            dealer_price_usd=31.25,source="verified_local_dcp:mock",producer_id="old-host",
            claim_token=old["claim_token"],claim_generation=old["claim_generation"]))

def load_tests(loader, tests, pattern):
    methods = sorted(x for x in Stage9SafetyClosure.__dict__ if x.startswith("test_"))
    return unittest.TestSuite(Stage9SafetyClosure(x) for x in methods)
