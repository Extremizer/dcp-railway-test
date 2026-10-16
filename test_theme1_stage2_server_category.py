"""Temporary SQLite runtime tests; no LIVE DCP, no production resources."""
import sqlite3
import tempfile
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch

import dcp_category_authority as authority
import dp_live_bridge as bridge
import dp_result_completion as completion
import oemixibot_dcp_price as supplier

class Stage2ServerCategoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"isolated.db"
        bridge.init(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE oem_catalog_cache (
                manufacturer TEXT, current_oem TEXT, catalog TEXT, msrp_verified INTEGER,
                source_kind TEXT, source_ref TEXT, last_verified_at TEXT)""")
            conn.execute("""CREATE TABLE oem_catalog_aliases (
                manufacturer TEXT, alias_oem TEXT, current_oem TEXT, alias_kind TEXT)""")
            conn.execute("""INSERT INTO oem_catalog_cache VALUES (?,?,?,?,?,?,?)""",
                ("Polaris","2206328","Parts",1,"validated_public_msrp","owner-reviewed-catalog",
                 datetime.now(timezone.utc).isoformat()))
            conn.execute("INSERT INTO oem_catalog_aliases VALUES (?,?,?,?)",
                         ("Polaris","2206328","2206328","current"))
            conn.commit()
    def tearDown(self): self.tmp.cleanup()

    def resolve(self):
        with sqlite3.connect(self.db) as conn:
            return authority.resolve(conn,"Polaris","2206328")

    def test_verified_route_and_negative_provenance(self):
        self.assertEqual(self.resolve()["item_category"],"Parts")
        for query,params in (
            ("UPDATE oem_catalog_cache SET source_ref=NULL",()),
            ("UPDATE oem_catalog_cache SET msrp_verified=0",()),
            ("UPDATE oem_catalog_cache SET catalog=''",()),
        ):
            with self.subTest(query=query):
                with sqlite3.connect(self.db) as conn:
                    conn.execute("SAVEPOINT s")
                    conn.execute(query,params)
                    self.assertIsNone(authority.resolve(conn,"Polaris","2206328"))
                    conn.execute("ROLLBACK TO s")
                    conn.execute("RELEASE s")

    def test_unknown_auto_and_nonexistent_never_enqueue(self):
        for brand in ("__AUTO__","UNKNOWN","MadeUpBrand"):
            self.assertEqual(bridge.request_live_dp(self.db,brand,"2206328",wait_seconds=0)["status"],"UNKNOWN_MANUFACTURER")
        self.assertEqual(bridge.request_live_dp(self.db,"Polaris","9999999",wait_seconds=0)["status"],"CATEGORY_UNVERIFIED")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dp_live_requests").fetchone()[0],0)

    def test_enqueued_claim_contains_server_authority(self):
        # Only isolated temp DB, never a live supplier. Zero wait avoids producer.
        result=bridge.request_live_dp(self.db,"Polaris","2206328",wait_seconds=0)
        self.assertEqual(result["status"],"TIMEOUT")
        with sqlite3.connect(self.db) as conn:
            row=conn.execute("SELECT item_category,category_source_kind,category_source_ref,category_verified_at FROM dp_live_requests").fetchone()
            self.assertEqual(row[0],"Parts")
            self.assertTrue(all(row))
        with patch.dict("os.environ",{"EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS":"0"}):
            claim=bridge.claim_next(self.db,producer_id="isolated-test")
        self.assertIsNotNone(claim)
        self.assertEqual(claim["item_category"],"Parts")
        return claim

    def test_supplier_verified_route_still_cannot_promote_dealer(self):
        claim=dict(manufacturer="Polaris",oem="2206328",**self.resolve())
        self.assertEqual(supplier.get_dealer_price("Polaris","2206328",category_evidence=claim).status,"DEALER_PRICE_UNVERIFIED")
        self.assertEqual(supplier.get_dealer_price("Polaris","2206328").status,"CATEGORY_UNVERIFIED")
        claim["item_category"]="Accessories"
        self.assertEqual(supplier.get_dealer_price("Polaris","2206328",category_evidence=claim).status,"CATEGORY_UNVERIFIED")

    def test_found_completion_rolls_back_and_never_writes_cache(self):
        result=bridge.request_live_dp(self.db,"Polaris","2206328",wait_seconds=0)
        self.assertEqual(result["status"],"TIMEOUT")
        with patch.dict("os.environ",{"EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS":"0"}):
            claim=bridge.claim_next(self.db,producer_id="isolated-test")
        self.assertIsNotNone(claim)
        payload=dict(request_id=claim["request_id"],protocol_version=2,
           producer_id=claim["producer_id"],claim_token=claim["claim_token"],
           claim_generation=claim["claim_generation"],status="FOUND",
           manufacturer="Polaris",query_oem="2206328",matched_sku="2206328",
           current_oem="2206328",currency="USD",dealer_price_usd=184.07,
           source="live_dom:#add_2206328 .c2 .dbl")
        with self.assertRaises(completion.CompletionError):
            completion.complete_result(self.db,payload)
        with sqlite3.connect(self.db) as conn:
            self.assertFalse(conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='dealer_price_cache'").fetchone())
            self.assertEqual(conn.execute("SELECT status FROM dp_live_requests").fetchone()[0],"claimed")

if __name__=="__main__":
    unittest.main()
