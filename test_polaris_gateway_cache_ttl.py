import sqlite3,unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import Mock
from test_polaris_fenced_completion_e2e import PolarisFencedE2E,REQUESTED,RESOLVED
from dp_result_completion import complete_result
import dcp_price_gateway as gateway

class PolarisGatewayTTL(unittest.TestCase):
 def setUp(self):
  self.fixture=PolarisFencedE2E("test_positive_alias_completion")
  self.fixture.setUp()
  self.addCleanup(self.fixture.doCleanups)
  complete_result(self.fixture.db,self.fixture.payload())
  self.now=datetime.now(timezone.utc)
 def at_age(self,hours):
  timestamp=(self.now-timedelta(hours=hours)).isoformat(timespec="seconds")
  with sqlite3.connect(self.fixture.db) as conn:
   conn.execute("UPDATE dealer_price_cache SET last_verified_at=?",(timestamp,))
   conn.execute("UPDATE dp_live_requests SET cache_committed_at=?",(timestamp,))
 def test_fresh_23h(self):
  self.at_age(23)
  r=gateway._read_cache(self.fixture.db,"Polaris",RESOLVED,24,now=self.now)
  self.assertEqual(r["dealer_price_usd"],184.07)
 def test_boundary_24h(self):
  self.at_age(24)
  r=gateway._read_cache(self.fixture.db,"Polaris",REQUESTED,24,now=self.now)
  self.assertEqual(r["dealer_price_usd"],184.07)
 def test_stale_25h(self):
  self.at_age(25)
  self.assertIsNone(gateway._read_cache(self.fixture.db,"Polaris",REQUESTED,24,now=self.now))
 def test_read_preserves_verified_at(self):
  self.at_age(23)
  with sqlite3.connect(self.fixture.db) as conn:
   before=conn.execute("SELECT last_verified_at FROM dealer_price_cache WHERE oem=?",(REQUESTED,)).fetchone()[0]
  self.assertIsNotNone(gateway._read_cache(self.fixture.db,"Polaris",REQUESTED,24,now=self.now))
  with sqlite3.connect(self.fixture.db) as conn:
   after=conn.execute("SELECT last_verified_at FROM dealer_price_cache WHERE oem=?",(REQUESTED,)).fetchone()[0]
  self.assertEqual(before,after)
 def test_fresh_cache_skips_live(self):
  live=Mock(side_effect=AssertionError("LIVE must not run"))
  result=gateway.request_price(self.fixture.db,"Polaris",RESOLVED,max_age_hours=24,live_request=live)
  self.assertEqual(result["dealer_price_usd"],184.07)
  live.assert_not_called()
 def test_dealer_adapter_reads_committed_cache(self):
  adapter=gateway.SharedDealerPriceAdapter(lambda m,o,max_age_hours:gateway.request_price(
   self.fixture.db,m,o,max_age_hours=max_age_hours),max_age_hours=24)
  r=adapter.get_dealer_price_dict("Polaris",RESOLVED)
  self.assertEqual(r["status"],"FOUND")
  self.assertEqual(r["dealer_price_usd"],184.07)
 def test_non_polaris_not_promoted(self):
  self.assertIsNone(gateway._read_cache(self.fixture.db,"Yamaha",RESOLVED,24,now=self.now))
 def test_evidence_required(self):
  with sqlite3.connect(self.fixture.db) as conn:
   conn.execute("DELETE FROM dp_live_recovery_events WHERE event_type='polaris_cart_verified_found'")
  self.assertIsNone(gateway._read_cache(self.fixture.db,"Polaris",RESOLVED,24,now=self.now))
 def test_no_msrp_fallback(self):
  with sqlite3.connect(self.fixture.db) as conn:
   conn.execute("UPDATE dealer_price_cache SET source='msrp'")
  self.assertIsNone(gateway._read_cache(self.fixture.db,"Polaris",RESOLVED,24,now=self.now))
 def test_stale_refresh_failed_no_price(self):
  self.at_age(25)
  r=gateway.request_price(self.fixture.db,"Polaris",REQUESTED,max_age_hours=24,
   live_request=lambda *a,**k:{"status":"AUTH_REQUIRED","msrp_usd":239.99})
  self.assertNotEqual(r.get("status"),"FOUND")
  self.assertNotIn("dealer_price_usd",r)
  self.assertNotIn("msrp_usd",r)
if __name__=="__main__":unittest.main()
