"""Offline cloud worker state/journal regression; no supplier access."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import Mock
from dcp_cloud_worker import CloudWorker,normalize_result

class WorkerTests(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup)
  self.path=Path(t.name)
  self.client=Mock(owner_epoch=None)
  self.browser=Mock()
  self.notifier=Mock()
  self.lookup=Mock()
  self.worker=CloudWorker(client=self.client,browser=self.browser,
   lookup=self.lookup,notifier=self.notifier,journal_dir=self.path,enabled=True)
 def test_requires_human_recovery(self):
  self.browser.status.return_value="AUTH_REQUIRED"
  self.assertEqual(self.worker.tick(),"AUTH_REQUIRED")
  self.notifier.notify.assert_called_once_with("AUTH_REQUIRED")
  self.client.next.assert_not_called()
  self.browser.status.return_value="READY"
  self.assertEqual(self.worker.tick(),"HUMAN_CONFIRMATION_REQUIRED")
  self.assertTrue(self.worker.acknowledge_manual_recovery())
  self.assertEqual(self.worker.tick(),"OWNER_ACQUIRED_ADMISSION_CLOSED")
 def test_cloudflare_never_claims(self):
  self.browser.status.return_value="CLOUDFLARE"
  self.assertEqual(self.worker.tick(),"CLOUDFLARE")
  self.client.next.assert_not_called()
 def test_crash_uncertainty_is_durable(self):
  self.browser.status.return_value="READY"
  self.client.owner_epoch="test-epoch"
  self.client.next.side_effect=TimeoutError("mock")
  self.assertEqual(self.worker.tick(),"RECONCILIATION_REQUIRED")
  self.assertTrue(self.worker.journal.exists())
  self.client.next.reset_mock()
  self.assertEqual(self.worker.tick(),"RECONCILIATION_REQUIRED")
  self.client.next.assert_not_called()
 def test_fresh_worker_fails_closed_on_existing_journal(self):
  self.worker._write_uncertain("claim_request_uncertain")
  second=CloudWorker(client=self.client,browser=self.browser,
   lookup=self.lookup,notifier=self.notifier,journal_dir=self.path,enabled=True)
  self.assertEqual(second.tick(),"RECONCILIATION_REQUIRED")
 def test_polaris_without_cloud_cart_never_calls_lookup(self):
  self.browser.status.return_value="READY"
  self.client.owner_epoch="test-epoch"
  self.client.next.return_value={"request_id":"r1","claim_generation":1,"manufacturer":"Polaris","oem":"2206328"}
  self.client.complete.return_value={"ok":True}
  self.assertEqual(self.worker.tick(),"TECHNICAL_ERROR")
  self.lookup.assert_not_called()
 def test_bad_dealer_evidence_never_found(self):
  claim={"manufacturer":"Arctic Cat","oem":"2670-265"}
  value={"status":"FOUND","manufacturer":"Arctic Cat","query_oem":"2670-265",
         "current_oem":"2670-265","matched_sku":"2670-265","source":"msrp",
         "dealer_price_usd":31.25,"currency":"USD","authorized":True}
  self.assertEqual(normalize_result(claim,value)["status"],"TECHNICAL_ERROR")

if __name__=="__main__":unittest.main()
