import sqlite3,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
import dp_live_bridge as bridge
from dp_result_completion import complete_result,CompletionError
from polaris_dealer_cart_completion_contract import SOURCE

REQUESTED="2205250"
RESOLVED="2206328"
class PolarisFencedE2E(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
  self.db=Path(self.temp.name)/"dp.sqlite"
  bridge.init(self.db)
  self.now=datetime.now(timezone.utc)
  with sqlite3.connect(self.db) as c:
   c.execute("CREATE TABLE oem_catalog_cache(manufacturer TEXT,current_oem TEXT,catalog TEXT,msrp_verified INTEGER,source_kind TEXT,source_ref TEXT,last_verified_at TEXT,previous_oems_json TEXT)")
   c.execute("CREATE TABLE oem_catalog_aliases(manufacturer TEXT,alias_oem TEXT,current_oem TEXT,alias_kind TEXT)")
   c.execute("INSERT INTO oem_catalog_cache VALUES(?,?,?,?,?,?,?,?)",("Polaris",RESOLVED,"Parts",1,"validated_public_msrp","fixture",self.now.isoformat(),'["2205250"]'))
   c.executemany("INSERT INTO oem_catalog_aliases VALUES(?,?,?,?)",[("Polaris",REQUESTED,RESOLVED,"previous"),("Polaris",RESOLVED,RESOLVED,"current")])
   c.execute("INSERT INTO dp_live_requests(request_id,manufacturer,oem,status,requested_at,claim_token,claim_owner,claim_generation,lease_expires_at,item_category,category_source_kind,category_source_ref,category_verified_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",("r1","Polaris",REQUESTED,"claimed",self.now.isoformat(),"token","producer",3,(self.now+timedelta(minutes=5)).isoformat(),"Parts","validated_public_msrp","fixture",self.now.isoformat()))
 def payload(self):
  return dict(request_id="r1",protocol_version=2,claim_token="token",claim_generation=3,producer_id="producer",status="FOUND",manufacturer="Polaris",query_oem=REQUESTED,current_oem=RESOLVED,matched_sku=RESOLVED,dealer_price_usd=184.07,currency="USD",source=SOURCE,dealer_cart_proof=dict(status="ADMIN_PROBE_CART_PRICE_VERIFIED",requested_oem=REQUESTED,resolved_oem=RESOLVED,matched_sku=RESOLVED,quantity=1,unit_price_usd=184.07,subtotal_usd=184.07,currency="USD",dealer_authenticated=True,dealer_context_verified=True,trusted_preflight=True,cart_restored=True,production_found=False,dealer_price_usd=None,dealer_identity_proof_ref="fixture-proof",category_source_kind="validated_public_msrp",category_source_ref="fixture",category_verified_at=self.now.isoformat()))
 def cached(self):
  with sqlite3.connect(self.db) as c:
   exists=c.execute("SELECT 1 FROM sqlite_master WHERE name='dealer_price_cache'").fetchone()
   return c.execute("SELECT oem,dealer_price_usd FROM dealer_price_cache ORDER BY oem").fetchall() if exists else []
 def reject(self,modify):
  p=self.payload();modify(p)
  with self.assertRaises(CompletionError):complete_result(self.db,p)
  self.assertEqual(self.cached(),[])
 def test_positive_alias_completion(self):
  self.assertTrue(complete_result(self.db,self.payload())["server_cache_committed"])
  self.assertEqual(self.cached(),[(REQUESTED,184.07),(RESOLVED,184.07)])
 def test_duplicate_rejected(self):
  complete_result(self.db,self.payload())
  with self.assertRaises(CompletionError):complete_result(self.db,self.payload())
 def test_stale_token(self):self.reject(lambda p:p.update(claim_token="stale"))
 def test_wrong_producer(self):self.reject(lambda p:p.update(producer_id="second"))
 def test_wrong_generation(self):self.reject(lambda p:p.update(claim_generation=2))
 def test_expired_claim(self):
  with sqlite3.connect(self.db) as c:c.execute("UPDATE dp_live_requests SET lease_expires_at=?",((self.now-timedelta(minutes=1)).isoformat(),))
  self.reject(lambda p:None)
 def test_restoration_failed(self):self.reject(lambda p:p["dealer_cart_proof"].update(cart_restored=False))
 def test_wrong_identity(self):self.reject(lambda p:p["dealer_cart_proof"].update(dealer_context_verified=False))
 def test_wrong_sku(self):self.reject(lambda p:p.update(matched_sku=REQUESTED))
 def test_msrp_rejected(self):self.reject(lambda p:p.update(source="msrp",dealer_price_usd=239.99))
 def test_client_cart_rejected(self):self.reject(lambda p:p.update(dealer_price_usd=209.99))
 def test_no_authority(self):
  with sqlite3.connect(self.db) as c:c.execute("UPDATE oem_catalog_cache SET msrp_verified=0")
  self.reject(lambda p:None)
if __name__=="__main__":unittest.main()
