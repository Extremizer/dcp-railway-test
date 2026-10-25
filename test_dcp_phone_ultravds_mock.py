"""Offline contract tests for UltraVDS mock-only launcher; no SSH or network."""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import dcp_phone_ultravds_mock as host

class UltraVdsMockTests(unittest.TestCase):
    def env(self):
        return {
            "DCP_PHONE_STAGING_MOCK_ONLY": "1",
            "DCP_PHONE_ULTRAVDS_MOCK_ONLY": "1",
            "DCP_CLOUD_PRODUCER_ENABLED": "0",
            "DCP_LIVE_NAVIGATION_GO": "0",
            "DCP_RECOVERY_TELEGRAM_BOT_TOKEN": "test:offline",
            "DCP_RECOVERY_OWNER_ID": "12345",
            "DCP_CLOUD_STATE_DIR": str(host.STATE),
            "XDG_RUNTIME_DIR": "/run/user/1001",
        }

    def test_mock_only_admission_is_fail_closed(self):
        e=self.env()
        for key,wrong in (("DCP_PHONE_STAGING_MOCK_ONLY","0"),
                          ("DCP_PHONE_ULTRAVDS_MOCK_ONLY","0"),
                          ("DCP_CLOUD_PRODUCER_ENABLED","1"),
                          ("DCP_LIVE_NAVIGATION_GO","1")):
            with self.subTest(key=key):
                bad=e.copy();bad[key]=wrong
                with self.assertRaises(host.VpsMockBlocked):
                    host.preflight(bad,uid=1001,platform="linux")
        with self.assertRaises(host.VpsMockBlocked):
            host.preflight(e,uid=0,platform="linux")
        with self.assertRaises(host.VpsMockBlocked):
            host.preflight(e,uid=1001,platform="win32")

    def test_local_preflight_does_not_require_secrets_but_keeps_mock_gate(self):
        e=self.env()
        e.pop("DCP_RECOVERY_TELEGRAM_BOT_TOKEN")
        e.pop("DCP_RECOVERY_OWNER_ID")
        e["DCP_PHONE_ULTRAVDS_LOCAL_PREFLIGHT"]="1"
        with self.assertRaises(host.VpsMockBlocked) as caught:
            host.preflight(e,uid=1001,platform="linux")
        self.assertIn(str(caught.exception),
                      ("snap_chromium_not_installed","snap_user_session_missing"))
        e["DCP_CLOUD_PRODUCER_ENABLED"]="1"
        with self.assertRaisesRegex(host.VpsMockBlocked,"mock_only_admission_required"):
            host.preflight(e,uid=1001,platform="linux")

    def test_local_health_mode_disables_remote_grants(self):
        import dcp_phone_staging_app as appmod
        env=self.env()
        env.pop("DCP_RECOVERY_TELEGRAM_BOT_TOKEN")
        env.pop("DCP_RECOVERY_OWNER_ID")
        env["DCP_PHONE_ULTRAVDS_LOCAL_PREFLIGHT"]="1"
        with patch.dict(os.environ,env,clear=True):
            appmod.configure()
            self.assertIsNone(appmod.app.state.grants)
            self.assertIsNone(appmod.app.state.coordinator)
            self.assertIsNotNone(appmod.app.state.browser)

    def test_chromium_args_keep_sandbox_and_loopback(self):
        args=host.browser_args(host.STATE/"test-profile")
        self.assertNotIn("--no-sandbox",args)
        self.assertNotIn("--disable-setuid-sandbox",args)
        self.assertNotIn("--disable-seccomp-filter-sandbox",args)
        self.assertIn("--remote-debugging-address=127.0.0.1",args)
        self.assertEqual(args[-1],host.MOCK_URL)
        self.assertTrue(host.MOCK_URL.startswith("file:///home/extremizer-dcp/snap/chromium/common/"))
        code=Path(host.__file__).read_text()
        self.assertNotIn("www.dealercostparts.com",code)
        self.assertIn('host="127.0.0.1"',code)

if __name__=="__main__":
    unittest.main()
