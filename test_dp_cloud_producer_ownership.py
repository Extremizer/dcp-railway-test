"""No-network transaction tests for global producer fencing and no auto takeover."""
import os
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import dp_cloud_producer_ownership as owner

class OwnerEpochTests(unittest.TestCase):
 def setUp(self):
  self.db=sqlite3.connect(":memory:",isolation_level=None)
  self.db.execute("CREATE TABLE dp_live_requests(request_id TEXT,status TEXT)")
  self.env=patch.dict(os.environ,{"EXTREMIZER_DP_CLOUD_OWNERSHIP_ENABLED":"1"})
  self.env.start();self.addCleanup(self.env.stop)
  self.addCleanup(self.db.close)
  self.now=datetime(2026,10,20,tzinfo=timezone.utc)
 def acquire(self,p="cloud-host-000001"):
  self.db.execute("BEGIN IMMEDIATE")
  result=owner.acquire(self.db,p,now=self.now)
  self.db.commit()
  return result
 def test_default_closed(self):
  with patch.dict(os.environ,{"EXTREMIZER_DP_CLOUD_OWNERSHIP_ENABLED":"0"}):
   self.assertEqual(owner.acquire(self.db,"cloud-host-000001")["status"],"DISABLED")
 def test_acquire_and_duplicate_fenced(self):
  a=self.acquire()
  self.assertEqual(a["status"],"ACQUIRED")
  self.assertEqual(self.acquire("cloud-host-000002")["status"],"OWNED_OR_QUARANTINED")
  self.assertTrue(owner.allowed(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now))
  self.assertFalse(owner.allowed(self.db,"cloud-host-000002",a["owner_epoch"],now=self.now))
 def test_expired_not_taken_over(self):
  a=self.acquire()
  self.assertEqual(owner.renew(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now+timedelta(seconds=181))["status"],"REJECTED")
  self.assertEqual(self.acquire("cloud-host-000002")["status"],"OWNED_OR_QUARANTINED")
 def test_release_then_new_epoch(self):
  a=self.acquire()
  self.assertEqual(owner.release(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now)["status"],"RELEASED")
  b=self.acquire("cloud-host-000002")
  self.assertEqual(b["generation"],2)
  self.assertNotEqual(a["owner_epoch"],b["owner_epoch"])
  self.assertFalse(owner.allowed(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now))
 def test_pending_blocks_takeover_and_release(self):
  a=self.acquire()
  self.db.execute("INSERT INTO dp_live_requests VALUES('r1','claimed')")
  self.assertEqual(owner.release(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now)["status"],"WORK_PENDING")
  self.db.execute("UPDATE dp_live_requests SET status='orphaned'")
  self.assertEqual(owner.release(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now)["status"],"WORK_PENDING")
 def test_request_generation_is_bound_and_old_epoch_fails(self):
  a=self.acquire()
  with patch.object(owner,"_utc",return_value=self.now):
   self.db.execute("BEGIN IMMEDIATE")
   owner.bind_claim(self.db,"r1",1,"cloud-host-000001",a["owner_epoch"])
   self.db.commit()
   self.assertTrue(owner.completion_allowed(self.db,"r1",1,"cloud-host-000001",a["owner_epoch"]))
   self.assertFalse(owner.completion_allowed(self.db,"r1",2,"cloud-host-000001",a["owner_epoch"]))
   self.assertEqual(owner.release(self.db,"cloud-host-000001",a["owner_epoch"],now=self.now)["status"],"RELEASED")
   self.assertFalse(owner.completion_allowed(self.db,"r1",1,"cloud-host-000001",a["owner_epoch"]))
 def test_wrong_host_or_invalid_token_rejected(self):
  a=self.acquire()
  self.assertFalse(owner.allowed(self.db,"cloud-host-000001","bad",now=self.now))
  self.assertFalse(owner.allowed(self.db,"legacy-windows-agent",a["owner_epoch"],now=self.now))
  self.assertEqual(owner.release(self.db,"cloud-host-000002",a["owner_epoch"],now=self.now)["status"],"REJECTED")
if __name__=="__main__": unittest.main()
