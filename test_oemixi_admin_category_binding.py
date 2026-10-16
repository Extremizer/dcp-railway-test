import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
from oemixi_admin_polaris_cart_price import admin_cart_price

NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
OEM="2206328"
def verified(**kw):
    d={"status":"VALIDATED_DEALER_CART_CANDIDATE","manufacturer":"Polaris",
       "item_category":"Parts","requested_oem":OEM,"matched_sku":OEM,
       "current_oem":OEM,"unit_price_usd":"184.07","currency":"USD",
       "dealer_price_usd":None,"production_found":False}
    d.update(kw)
    return d
class ProductionAuthorityBindingTests(unittest.TestCase):
    def test_missing_path_fails_closed(self):
        with patch.dict(os.environ,{},clear=True):
            r=admin_cart_price(OEM,inspect=lambda **k:verified(),now=NOW)
        self.assertIsNone(r["admin_unit_price_usd"])
    def test_missing_file_fails_closed_without_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            missing=Path(folder)/"not-created.sqlite"
            with patch.dict(os.environ,{"EXTREMIZER_DCP_CATEGORY_DB_PATH":str(missing)}):
                r=admin_cart_price(OEM,inspect=lambda **k:verified(),now=NOW)
            self.assertFalse(missing.exists())
            self.assertIsNone(r["admin_unit_price_usd"])
    def test_server_authority_from_readonly_file(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"catalog.sqlite"
            with sqlite3.connect(p) as db:
                db.execute("CREATE TABLE oem_catalog_cache(manufacturer TEXT,current_oem TEXT,catalog TEXT,msrp_verified INTEGER,source_kind TEXT,source_ref TEXT,last_verified_at TEXT,previous_oems_json TEXT)")
                db.execute("CREATE TABLE oem_catalog_aliases(manufacturer TEXT,alias_oem TEXT,current_oem TEXT,alias_kind TEXT)")
                db.execute("INSERT INTO oem_catalog_cache VALUES(?,?,?,?,?,?,?,?)",("Polaris",OEM,"Parts",1,"validated_public_msrp","synthetic-server-catalog",NOW.isoformat(),"[]"))
                db.execute("INSERT INTO oem_catalog_aliases VALUES(?,?,?,?)",("Polaris",OEM,OEM,"current"))
            with patch.dict(os.environ,{"EXTREMIZER_DCP_CATEGORY_DB_PATH":str(p)}):
                r=admin_cart_price(OEM,inspect=lambda **k:verified(),now=NOW)
            self.assertEqual(r["admin_unit_price_usd"],"184.07")
            self.assertIsNone(r["dealer_price_usd"])
            self.assertIs(r["production_found"],False)
    def test_missing_authority_tables_fails_closed(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/"empty.sqlite"
            with sqlite3.connect(p): pass
            with patch.dict(os.environ,{"EXTREMIZER_DCP_CATEGORY_DB_PATH":str(p)}):
                r=admin_cart_price(OEM,inspect=lambda **k:verified(),now=NOW)
            self.assertIsNone(r["admin_unit_price_usd"])
if __name__=="__main__":unittest.main()
