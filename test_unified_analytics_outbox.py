import sqlite3,tempfile,unittest
from pathlib import Path
import unified_analytics_outbox as q
class OutboxTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
  self.path=str(Path(self.tmp.name)/"existing-producer.sqlite")
  with sqlite3.connect(self.path) as c:c.execute("CREATE TABLE EXISTING_OWNER(id INTEGER)")
  q.initialize(self.path)
 def tearDown(self):self.tmp.cleanup()
 def test_fail_recover_and_restart(self):
  self.assertTrue(q.enqueue(self.path,"message:1",{"oem":"3211070"}))
  self.assertFalse(q.enqueue(self.path,"message:1",{"oem":"3211070"}))
  with self.assertRaises(ValueError):q.enqueue(self.path,"message:1",{"oem":"3211071"})
  self.assertEqual(q.flush(self.path,lambda _:False,now=100)['deferred'],1)
  # independent execution of queue after a simulated producer restart
  seen=[]
  self.assertEqual(q.flush(self.path,lambda d:seen.append(d) or True,now=1000)['delivered'],1)
  self.assertEqual(len(seen),1)
  self.assertEqual(q.flush(self.path,lambda _:True,now=2000)['delivered'],0)
 def test_bounded_error_evidence(self):
  q.enqueue(self.path,"message:2",{"oem":"3211070"})
  def fail(_):raise TimeoutError("secret detail should not persist")
  for i in range(5):q.flush(self.path,fail,now=100+i*1000)
  with sqlite3.connect(self.path) as c:
   row=c.execute("SELECT attempts,error_code,state FROM analytics_delivery_outbox").fetchone()
  self.assertEqual(row,(5,"TimeoutError","EXHAUSTED"))
 def test_no_shadow_db(self):
  with self.assertRaises(FileNotFoundError):q.initialize(str(Path(self.tmp.name)/"absent.sqlite"))
  self.assertFalse((Path(self.tmp.name)/"absent.sqlite").exists())
if __name__=='__main__':unittest.main()
