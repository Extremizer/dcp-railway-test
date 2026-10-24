import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from warehouse_sm08351_readonly_audit import inspect

class StockAuditTests(unittest.TestCase):
    def test_exact_oem_mapping_and_readonly(self):
        with TemporaryDirectory() as d:
            p=Path(d)/"copy.db"
            with sqlite3.connect(p) as c:
                c.executescript("""
                CREATE TABLE warehouses(id INTEGER, code TEXT, public_name TEXT,internal_name TEXT,city TEXT,adapter_type TEXT,website_url TEXT,active INTEGER,deleted_at TEXT,priority INTEGER);
                CREATE TABLE warehouse_sources(warehouse_id INTEGER,source_type TEXT,enabled INTEGER,priority INTEGER,ttl_minutes INTEGER,last_checked_at TEXT,last_success_at TEXT,last_error TEXT);
                CREATE TABLE warehouse_stock_current(warehouse_id INTEGER,source_type TEXT,oem TEXT,status TEXT,quantity REAL,price_rub REAL,observed_at TEXT,expires_at TEXT);
                CREATE TABLE warehouse_stock_reservations(warehouse_id INTEGER,status TEXT,oem TEXT,quantity REAL);
                INSERT INTO warehouses VALUES(1,'KRS','Склад 2','Vlad Extreme Life','Красноярск','vladextremelife','https://vladextremelife.ru',1,NULL,30);
                INSERT INTO warehouses VALUES(2,'X','Склад 3','Other supplier','Москва','other','https://example.org',1,NULL,40);
                INSERT INTO warehouse_sources VALUES(1,'website',1,20,30,'2026-10-24T07:00:00Z','2026-10-24T07:00:00Z',NULL);
                INSERT INTO warehouse_stock_current VALUES(1,'website','SM-08351','in_stock',1,4000,'2026-10-24T07:00:00Z','2026-10-24T07:30:00Z');
                INSERT INTO warehouse_stock_current VALUES(2,'website','SM-08351','out_of_stock',0,NULL,'2026-10-24T07:00:00Z','2026-10-24T07:30:00Z');
                INSERT INTO warehouse_stock_reservations VALUES(1,'reserved','SM-08351',1);
                """)
            result=inspect(str(p))
            self.assertEqual([x["public_name"] for x in result["warehouses"]],["Склад 2","Склад 3"])
            self.assertEqual(result["warehouses"][0]["website_host"],"vladextremelife.ru")
            self.assertEqual(result["warehouses"][0]["active_reservations"][0]["quantity_total"],1)
            self.assertEqual(result["warehouses"][1]["sm08351_snapshots"][0]["quantity"],0)
            self.assertEqual(result["status"],"READ_ONLY_SNAPSHOT")
            with self.assertRaises(ValueError):inspect(str(p),"OTHER")
    def test_schema_missing_fails_closed(self):
        with TemporaryDirectory() as d:
            p=Path(d)/"empty.db"
            sqlite3.connect(p).close()
            with self.assertRaisesRegex(ValueError,"WAREHOUSE_SCHEMA_INCOMPLETE"):inspect(str(p))
if __name__=="__main__":unittest.main()
