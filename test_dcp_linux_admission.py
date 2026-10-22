import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import dcp_linux_admission as a
class AdmissionTargetedTests(unittest.TestCase):
 def test_no_session_denies_broker(self):
  class Broker:
   def acquire(self):raise AssertionError("acquire forbidden")
  with self.assertRaises(a.AdmissionError):
   a.broker_admission({"dealer_authenticated":False},3,Broker())
 def test_corrupt_and_pending_journal_fail_closed(self):
  with tempfile.TemporaryDirectory() as td:
   root=Path(td)
   with self.assertRaisesRegex(a.AdmissionError,"JOURNAL_NOT_INITIALIZED"):a.safe_profile(root)
   (root/"linux-producer-journal.json").write_text('{"state":"CLAIMED","claim":"fixture"}')
   with self.assertRaisesRegex(a.AdmissionError,"JOURNAL_QUARANTINED"):a.safe_profile(root)
 def test_atomic_clean_journal_allows_single_lock(self):
  with tempfile.TemporaryDirectory() as td:
   a.persist_journal(td,{"state":"CLEAN","claim":None})
   fd=a.safe_profile(td)
   try:
    with self.assertRaisesRegex(a.AdmissionError,"LOCAL_OWNER_LOCK_HELD"):a.safe_profile(td)
   finally:
    import os
    os.close(fd)
 def test_identity_never_logins_or_navigates(self):
  with patch("polaris_dealer_session_identity_guard.verify_dealer_session",return_value={"dealer_authenticated":False,"dealer_context_verified":False,"status":"AUTH_REQUIRED"}) as f:
   r=a.verify_identity("fixture@example.invalid",lambda:[],lambda tab,js:None,lambda:"AUTH_REQUIRED")
   self.assertEqual(r["status"],"AUTH_REQUIRED")
   self.assertTrue(f.called)
 def test_signed_broker_acquire_only(self):
  class Broker:
   def acquire(self):return {"status":"ACQUIRED","owner_epoch":"opaque","generation":2}
   def next(self):raise AssertionError("claim forbidden")
  r=a.broker_admission({"dealer_authenticated":True,"dealer_context_verified":True},5,Broker())
  self.assertEqual(r["status"],"BROKER_ADMITTED_NO_CLAIMS")
