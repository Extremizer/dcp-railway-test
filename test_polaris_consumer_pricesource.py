import ast,sqlite3,types,unittest
from datetime import datetime,timezone,timedelta
from pathlib import Path
from unittest.mock import patch
from test_polaris_fenced_completion_e2e import PolarisFencedE2E,REQUESTED,RESOLVED
from dp_result_completion import complete_result
import dcp_price_gateway

def client_cache_reader(db):
    """Execute unchanged client PriceSource function with isolated fixture deps."""
    source=Path("extremizer_bot.py").read_text(encoding="utf-8")
    funcs={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
    tree=ast.fix_missing_locations(ast.Module(body=[
        funcs["_dealer_price_cache_key"],funcs["get_dealer_price_cache"]],type_ignores=[]))
    ns={"finder":types.SimpleNamespace(manufacturer_alias=lambda x:x,normalize_oem=lambda x:x.strip().upper()),
        "init_orders_db":lambda:None,"ORDERS_DB_FILE":db,"DP_CACHE_MAX_AGE_HOURS":168.0,
        "sqlite3":sqlite3,"datetime":datetime}
    exec(compile(tree,"extremizer_bot.py","exec"),ns)
    return ns["get_dealer_price_cache"]

class ClientAndDealerConsumerTests(unittest.TestCase):
 def setUp(self):
  self.fixture=PolarisFencedE2E("test_positive_alias_completion")
  self.fixture.setUp()
  self.addCleanup(self.fixture.doCleanups)
  complete_result(self.fixture.db,self.fixture.payload())
 def test_client_cache_reads_verified_polaris(self):
  found=client_cache_reader(self.fixture.db)("Polaris",REQUESTED)
  self.assertEqual(found["dealer_price_usd"],184.07)
  self.assertTrue(found["fresh"])
 def test_client_alias_cache_reads_resolved(self):
  found=client_cache_reader(self.fixture.db)("Polaris",RESOLVED)
  self.assertEqual(found["dealer_price_usd"],184.07)
 def test_client_rejects_msrp_cache(self):
  with sqlite3.connect(self.fixture.db) as c:c.execute("UPDATE dealer_price_cache SET source='msrp'")
  self.assertIsNone(client_cache_reader(self.fixture.db)("Polaris",RESOLVED))
 def test_client_stale_25h(self):
  age=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(timespec="seconds")
  with sqlite3.connect(self.fixture.db) as c:
   c.execute("UPDATE dealer_price_cache SET last_verified_at=?",(age,))
   c.execute("UPDATE dp_live_requests SET cache_committed_at=?",(age,))
  self.assertIsNone(client_cache_reader(self.fixture.db)("Polaris",REQUESTED))
 def test_oemixi_consumer_uses_common_cache(self):
  import oemixibot_catalog as catalog
  gateway=lambda manufacturer,oem,max_age_hours:dcp_price_gateway.request_price(
      self.fixture.db,manufacturer,oem,max_age_hours=max_age_hours)
  adapter=dcp_price_gateway.SharedDealerPriceAdapter(gateway,max_age_hours=24)
  with patch.object(catalog,"resolve_identity",return_value={"oem":REQUESTED,"manufacturer":"Polaris","item_type":"part"}),patch.object(
        catalog.oem_reference_service,"lookup_oem",return_value={}),patch.object(catalog,"confirm_identity"):
   offer=catalog.resolve_offer(REQUESTED,price_gateway=adapter)
  self.assertEqual(offer["dl_usd"],184.07)
  self.assertEqual(offer["requested_oem"],REQUESTED)
  self.assertEqual(offer["oem"],RESOLVED)
if __name__=="__main__":unittest.main()
