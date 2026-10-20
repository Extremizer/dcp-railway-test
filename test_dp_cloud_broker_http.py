"""HTTP integration on a disposable database, with no supplier calls."""
import os, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
from dp_cloud_owner_http import build_cloud_router
import dp_live_bridge as broker
import dp_live_health as health
from dcp_isolated_category_fixture import seed_test_category_authority

class CloudBrokerHTTP(unittest.TestCase):
 def setUp(self):
  t=tempfile.TemporaryDirectory()
  self.addCleanup(t.cleanup)
  self.db=Path(t.name)/"broker.db"
  e=patch.dict(os.environ,{"EXTREMIZER_DP_CLOUD_OWNERSHIP_ENABLED":"1",
     "EXTREMIZER_DP_HEALTH_ENFORCE":"1","EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS":"0"})
  e.start()
  self.addCleanup(e.stop)
  broker.init(self.db)
  health.set_health(self.db,"READY")
  seed_test_category_authority(self.db)
  app=FastAPI()
  app.include_router(build_cloud_router(self.db,key=b"z"*32))
  self.http=TestClient(app)
  self.addCleanup(self.http.close)

 def make_producer(self,who):
  from dp_cloud_producer_client import CloudV2Producer
  def send(method,path,body,headers):
   r=self.http.request(method,path,content=body,headers=headers)
   if r.status_code>=400:
    raise RuntimeError("http_"+str(r.status_code))
   return r.json()
  return CloudV2Producer(who,b"z"*32,send)

 def test_owner_auth_lifecycle(self):
  a=self.make_producer("cloud-host-000001")
  b=self.make_producer("cloud-host-000002")
  self.assertEqual(a.acquire()["generation"],1)
  with self.assertRaises(RuntimeError):b.acquire()
  self.assertEqual(a.renew()["status"],"RENEWED")
  self.assertIsNone(a.next())
  self.assertEqual(a.release()["status"],"RELEASED")
  self.assertEqual(b.acquire()["generation"],2)

 def test_verified_price_atomic_result(self):
  import sqlite3
  from dcp_admission_test_support import approve_mode
  a=self.make_producer("cloud-host-000001")
  a.acquire()
  approve_mode(self.db,"OPEN")
  broker.request_live_dp(self.db,"Arctic Cat","2670-265",wait_seconds=0)
  claim=a.next()
  self.assertIsNotNone(claim)
  source="server_html:#add_2670-265 .c2 .dbl"
  raw={"status":"FOUND","manufacturer":"Arctic Cat","query_oem":"2670-265",
   "matched_sku":"2670-265","current_oem":"2670-265","currency":"USD",
   "dealer_price_usd":31.25,"source":source,"name":"Mock dealer"}
  result=a.complete(claim,raw)
  self.assertTrue(result["server_cache_committed"])
  with sqlite3.connect(self.db) as db:
   price=db.execute("SELECT dealer_price_usd FROM dealer_price_cache WHERE manufacturer=? AND oem=?",("Arctic Cat","2670-265")).fetchone()
   self.assertEqual(price,(31.25,))
  approve_mode(self.db,"CLOSED")
  self.assertEqual(a.release()["status"],"RELEASED")

if __name__=="__main__": unittest.main()
