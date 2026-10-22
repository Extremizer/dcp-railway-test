import tempfile
import unittest
from pathlib import Path
import dcp_linux_live_isolated as live
class LiveCandidateTests(unittest.TestCase):
 def test_default_denied(self):
  with self.assertRaises(live.AdmissionDenied):live.admission({})
 def test_mock_cannot_be_passed_as_live(self):
  with tempfile.TemporaryDirectory() as td:
   env={"DCP_PHONE_STAGING_MOCK_ONLY":"1","DCP_CLOUD_PRODUCER_ENABLED":"0","DCP_CLOUD_BROWSER_HOST_ENABLED":"1",
        "DCP_PHONE_RECOVERY_ENABLED":"1","EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1",
        "EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT":"fixture@example.invalid",
        "DCP_CLOUD_STATE_DIR":td,"DCP_RECOVERY_TELEGRAM_BOT_TOKEN":"fixture","DCP_RECOVERY_OWNER_ID":"10"}
   with self.assertRaisesRegex(live.AdmissionDenied,"MOCK_BROWSER_MODE_FORBIDDEN"):live.admission(env)
 def test_manual_host_producer_claims_never_auto_enabled(self):
  with tempfile.TemporaryDirectory() as td:
   env={"DCP_PHONE_STAGING_MOCK_ONLY":"0","DCP_CLOUD_PRODUCER_ENABLED":"1","DCP_CLOUD_BROWSER_HOST_ENABLED":"1",
        "DCP_PHONE_RECOVERY_ENABLED":"1","EXTREMIZER_DP_OWNERSHIP_ENFORCE":"1",
        "EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT":"fixture@example.invalid",
        "DCP_CLOUD_STATE_DIR":td,"DCP_RECOVERY_TELEGRAM_BOT_TOKEN":"fixture","DCP_RECOVERY_OWNER_ID":"10"}
   with self.assertRaisesRegex(live.AdmissionDenied,"CLAIM_PROCESS_MUST_BE_SEPARATELY_ADMITTED"):live.admission(env)
