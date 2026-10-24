"""Offline release candidate checks: no network, no production DB or browser."""
from contextlib import closing
from datetime import datetime,timezone,timedelta
import os
import sqlite3
import tempfile
import unittest

from dcp_price_gateway import _read_cache,request_price,SharedDealerPriceAdapter
from unified_price_contract_v1 import from_gateway_and_msrp,dealer_legacy_offer

MFG="Ski-Doo"
OEM="417300574"
SOURCE=f"verified_local_dcp:live_dom:current_oem_{OEM}"
NOW=datetime.now(timezone.utc)

class ExactSkiDooGateway(unittest.TestCase):
    def setUp(self):
        f=tempfile.NamedTemporaryFile(suffix=".db",delete=False)
        self.path=f.name
        f.close()
        self.when=NOW.isoformat()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("""CREATE TABLE dealer_price_cache(
              manufacturer TEXT,oem TEXT,dealer_price_usd REAL,
              source TEXT,last_verified_at TEXT)""")
            db.execute("""CREATE TABLE dp_live_requests(
              manufacturer TEXT,oem TEXT,dealer_price_usd REAL,
              result_source TEXT,cache_committed_at TEXT,
              result_status TEXT,status TEXT,current_oem TEXT,
              request_id TEXT,result_manufacturer TEXT,item_category TEXT,
              category_source_kind TEXT,category_source_ref TEXT,
              category_verified_at TEXT)""")
            self.c=(MFG,OEM,207.07,SOURCE,self.when)
            self.r=(MFG,OEM,207.07,SOURCE,self.when,"FOUND","finished",OEM,
                    "signed-request-1",MFG,"Parts","explicit_manufacturer_parts_search",
                    "DCP exact OEM search",self.when)
            db.execute("INSERT INTO dealer_price_cache VALUES (?,?,?,?,?)",self.c)
            db.execute("INSERT INTO dp_live_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",self.r)
            db.commit()
    def tearDown(self):
        os.unlink(self.path)
    def update_request(self,field,value):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(f"UPDATE dp_live_requests SET {field}=?",(value,))
            db.commit()
    def update_both_stamp(self,value):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE dp_live_requests SET cache_committed_at=?",(value,))
            db.execute("UPDATE dealer_price_cache SET last_verified_at=?",(value,))
            db.commit()
    def get(self):
        return _read_cache(self.path,MFG,OEM,24,now=NOW)
    def test_exact_committed_cache(self):
        x=self.get()
        self.assertEqual(x["status"],"FOUND")
        self.assertEqual(x["dealer_price_usd"],207.07)
        self.assertEqual(x["request_id"],"signed-request-1")
    def test_gateway_returns_fresh_verified_without_new_lookup(self):
        def no_network(*a,**kw):
            self.fail("unexpected DCP live lookup")
        x=request_price(self.path,MFG,OEM,max_age_hours=24,live_request=no_network)
        self.assertEqual(x["status"],"FOUND")
        self.assertTrue(x["verified_cache_hit"])
    def test_adapter_accepts_only_exact_evidenced_dealer_price(self):
        raw=self.get()
        dealer=SharedDealerPriceAdapter(lambda *a,**kw:raw,max_age_hours=24).get_dealer_price_dict(MFG,OEM)
        self.assertEqual(dealer["dealer_price_usd"],207.07)
        self.assertEqual(dealer["status"],"FOUND")
        contract=from_gateway_and_msrp(manufacturer=MFG,requested_oem=OEM,
            item_category="Parts",dealer_result=raw,msrp_result={},
            dealer_max_age_hours=24,msrp_max_age_hours=720)
        self.assertEqual(dealer_legacy_offer(contract)["dealer_price_usd"],207.07)
    def test_pending_uncommitted_never_accepted(self):
        self.update_request("status","claimed")
        self.assertIsNone(self.get())
    def test_wrong_result_manufacturer_denied(self):
        self.update_request("result_manufacturer","Arctic Cat")
        self.assertIsNone(self.get())
    def test_missing_category_authority_denied(self):
        self.update_request("category_source_ref",None)
        self.assertIsNone(self.get())
    def test_mismatched_current_oem_denied(self):
        self.update_request("current_oem","417300575")
        self.assertIsNone(self.get())
    def test_retail_or_msrp_source_denied(self):
        other="verified_local_dcp:msrp_wrong"
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("UPDATE dp_live_requests SET result_source=?",(other,))
            db.execute("UPDATE dealer_price_cache SET source=?",(other,))
            db.commit()
        self.assertIsNone(self.get())
    def test_stale_more_than_24h_denied(self):
        self.update_both_stamp((NOW-timedelta(hours=25)).isoformat())
        self.assertIsNone(self.get())
    def test_future_timestamps_rejected(self):
        self.update_both_stamp((NOW+timedelta(minutes=3)).isoformat())
        self.assertIsNone(self.get())
    def test_uncorroborated_found_payload_denied_by_adapter(self):
        raw=dict(self.get())
        raw.pop("verified_cache_hit")
        self.assertEqual(SharedDealerPriceAdapter(lambda *a,**kw:raw,max_age_hours=24)
                         .get_dealer_price_dict(MFG,OEM)["status"],"DEALER_PRICE_UNVERIFIED")
    def test_other_non_polaris_cannot_use_this_route(self):
        self.assertIsNone(_read_cache(self.path,"Can-Am",OEM,24,now=NOW))
        raw=dict(self.get())
        self.assertEqual(SharedDealerPriceAdapter(lambda *a,**kw:raw,max_age_hours=24)
                         .get_dealer_price_dict("Can-Am",OEM)["status"],"DEALER_PRICE_UNVERIFIED")

if __name__=="__main__":
    unittest.main()
