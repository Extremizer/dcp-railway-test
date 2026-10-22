"""Stage 9 mock-only guard tests. No network, Railway or real DCP."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import dcp_stage9_mock_linux_runner as r

class Stage9LinuxMockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        d = Path(self.temp.name)
        self.env = {"DP_RUNNER_TRANSPORT": "fake",
                    "DP_RUNNER_SERVER_URL": "http://127.0.0.1:8999",
                    "DP_RUNNER_STATE_DIR": str(d),
                    "DP_RUNNER_LOCK_FILE": str(d/"runner.lock"),
                    "DP_RUNNER_JOURNAL_FILE": str(d/"inflight.json"),
                    "DP_RUNNER_STATUS_FILE": str(d/"health.json")}
        self.cfg = r.configuration(self.env)

    def test_requires_explicit_loopback_server_and_transport(self):
        for url in ("", "https://dcp-railway-test-production.up.railway.app",
                    "http://example.org:8999", "http://127.0.0.1",
                    "http://127.0.0.1:8999/x", "http://username:password@localhost:8999"):
            with self.subTest(url=url), self.assertRaises(r.UnsafeConfiguration):
                r.configuration({**self.env, "DP_RUNNER_SERVER_URL": url})
        with self.assertRaises(r.UnsafeConfiguration):
            r.configuration({**self.env, "DP_RUNNER_TRANSPORT": "browser"})
        with self.assertRaises(r.UnsafeConfiguration):
            r.configuration({**self.env, "DP_RUNNER_JOURNAL_FILE": "relative.json"})

    def test_lock_prevents_deployment_overlap_on_shared_volume(self):
        with r.LinuxSingleton(self.cfg["lock"]):
            with self.assertRaises(r.SingletonHeld):
                with r.LinuxSingleton(self.cfg["lock"]):
                    pass
        with r.LinuxSingleton(self.cfg["lock"]):
            pass

    def test_inflight_journal_quarantines_without_claim(self):
        self.cfg["journal"].write_text('{"phase":"lookup_inflight"}')
        agent = SimpleNamespace(run_once=Mock(side_effect=AssertionError("never claim")))
        self.assertEqual(r.execute(self.cfg, agent=agent), 24)
        agent.run_once.assert_not_called()
        status = json.loads(self.cfg["status"].read_text())
        self.assertEqual(status["runner"], "quarantined")
        self.assertEqual(status["transport"], "fake")

    def test_idle_is_not_supplier_ready(self):
        fake = SimpleNamespace(run_once=Mock(return_value=False),
                               oemixibot_dcp_price=SimpleNamespace())
        self.assertEqual(r.execute(self.cfg, agent=fake), 0)
        assert not self.cfg["journal"].exists()
        state = json.loads(self.cfg["status"].read_text())
        self.assertEqual((state["runner"], state["supplier"]), ("alive", "unavailable"))
        self.assertEqual(fake.BASE_URL, self.cfg["url"])
        self.assertEqual(fake.JOURNAL_FILE, self.cfg["journal"])
        self.assertIs(fake.oemixibot_dcp_price.get_dealer_price_dict, r.fake_lookup)

    def test_crash_quarantine_and_no_requeue(self):
        def crash():
            self.cfg["journal"].write_text('{"phase":"result_delivery_uncertain"}')
            raise KeyboardInterrupt()
        fake = SimpleNamespace(run_once=crash, oemixibot_dcp_price=SimpleNamespace())
        self.assertEqual(r.execute(self.cfg, agent=fake), 24)
        self.assertTrue(self.cfg["journal"].exists())
        fake.run_once = Mock(side_effect=AssertionError("must not retry"))
        self.assertEqual(r.execute(self.cfg, agent=fake), 24)
        fake.run_once.assert_not_called()

    def test_producer_latch_stops_even_if_journal_is_clear(self):
        fake = SimpleNamespace(run_once=lambda: setattr(fake, "_PRODUCER_STOP", True) or True,
                               oemixibot_dcp_price=SimpleNamespace())
        self.assertEqual(r.execute(self.cfg, agent=fake), 24)

    def test_fake_supplier_never_returns_dealer_price(self):
        self.assertEqual(r.fake_lookup("Arctic Cat", "2670-265")["status"], "TECHNICAL_ERROR")
        self.assertNotIn("dealer_price_usd", r.fake_lookup("Arctic Cat", "2670-265"))


    def test_identity_guard_rejects_auto_unknown_and_unapproved(self):
        approved = {("Arctic Cat", "2670-265")}
        for manufacturer in ("__AUTO__", "AUTO", "UNKNOWN", "", "Honda"):
            with self.subTest(manufacturer=manufacturer):
                self.assertFalse(r.identity_approved(
                    {"manufacturer": manufacturer, "oem": "2670-265"}, approved))
        self.assertTrue(r.identity_approved(
            {"manufacturer": "Arctic Cat", "oem": "2670-265"}, approved))
        self.assertFalse(r.identity_approved(
            {"manufacturer": "Arctic Cat", "oem": "other"}, approved))

    def test_unverified_claim_quarantines_without_supplier_call(self):
        for manufacturer in ("__AUTO__", "UNKNOWN", "Honda"):
            with self.subTest(manufacturer=manufacturer):
                if self.cfg["journal"].exists():
                    self.cfg["journal"].unlink()
                supplier = Mock()
                def claim():
                    self.cfg["journal"].write_text('{"phase":"claim_delivery_uncertain"}')
                    return {"manufacturer": manufacturer, "oem": "2670-265"}
                fake = SimpleNamespace(
                    _claim=claim,
                    oemixibot_dcp_price=SimpleNamespace(),
                    run_once=lambda: fake._claim() and fake.oemixibot_dcp_price.get_dealer_price_dict(manufacturer, "2670-265"))
                self.assertEqual(r.execute(self.cfg, agent=fake, lookup=supplier,
                    approved_identities={("Arctic Cat", "2670-265")}), 24)
                supplier.assert_not_called()
                self.assertTrue(self.cfg["journal"].exists())
                self.assertEqual(json.loads(self.cfg["status"].read_text())["runner"], "quarantined")

    def test_no_windows_fallback(self):
        with patch.object(r.sys, "platform", "win32"):
            with self.assertRaises(r.UnsafeConfiguration):
                r.configuration(self.env)

if __name__ == "__main__":
    unittest.main()
