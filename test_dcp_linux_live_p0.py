import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from fastapi import HTTPException
import dcp_linux_cdp_identity as cdp
import dcp_linux_live_app as live

class LinuxLiveP0Tests(unittest.TestCase):
    def test_empty_browser_preflight_is_auth_required(self):
        with patch.object(cdp,"tabs",return_value=[{"type":"page","url":"about:blank"}]):
            self.assertEqual(cdp.preflight(),"AUTH_REQUIRED")
    def test_challenge_fails_closed(self):
        item={"type":"page","url":cdp.EXACT_CART}
        with patch.object(cdp,"tabs",return_value=[item]), patch.object(cdp,"evaluate",return_value={
            "url":cdp.EXACT_CART,"host":cdp.DCP_HOST,"challenged":True,"cart":True,"logout":True}):
            self.assertEqual(cdp.preflight(),"CLOUDFLARE")
    def test_exact_guard_mismatch_rejected(self):
        item={"type":"page","url":cdp.EXACT_CART}
        with patch.object(cdp,"tabs",return_value=[item]),patch.object(cdp,"evaluate",return_value={
            "url":cdp.EXACT_CART,"host":cdp.DCP_HOST,"challenged":False,"cart":True,"logout":True}):
            with patch("polaris_dealer_session_identity_guard.verify_dealer_session",
                       return_value={"status":"DEALER_IDENTITY_MISMATCH","dealer_authenticated":False}) as call:
                self.assertFalse(cdp.exact_dealer_identity("fixture@example.invalid")["dealer_authenticated"])
                self.assertTrue(call.called)
    def test_navigation_requires_distinct_owner_go(self):
        with patch.dict("os.environ",{},clear=True):
            with self.assertRaises(HTTPException):
                live._manual_go()
    def test_production_broker_endpoint_rejected(self):
        with patch.dict("os.environ",{"DCP_STAGING_BROKER_ORIGIN":
              "https://dcp-railway-test-production.up.railway.app"},clear=True):
            with self.assertRaisesRegex(Exception,"BROKER_ORIGIN_NOT_APPROVED"):
                live._signed_broker()
    def test_claim_completion_surface_not_exposed(self):
        methods={r.path for r in live.app.routes}
        self.assertNotIn("/internal/dp-cloud/next",methods)
        self.assertNotIn("/internal/dp-cloud/result",methods)
        self.assertIn("/admission/status",methods)
        self.assertIn("/admission/acquire",methods)
    def test_failed_identity_blocks_before_broker(self):
        with patch.object(live,"_authorized",return_value="fixture"), patch.object(live,"_manual_go"),patch.object(live,"_identity",return_value={"dealer_authenticated":False}):
            with self.assertRaises(HTTPException) as ctx:
                live.admission_acquire(authorization="Bearer fixture")
            self.assertEqual(ctx.exception.status_code,409)
