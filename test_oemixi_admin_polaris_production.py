import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import dcp_category_authority
import oemixi_admin_polaris_production as prod
import polaris_production_authority_seed as seedmod


def create_authority_tables(path):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE oem_catalog_cache(
            manufacturer TEXT NOT NULL,
            current_oem TEXT NOT NULL,
            name TEXT,
            catalog TEXT,
            msrp_usd REAL,
            msrp_verified INTEGER NOT NULL DEFAULT 0,
            previous_oems_json TEXT,
            source_kind TEXT,
            source_ref TEXT,
            first_seen_at TEXT,
            last_verified_at TEXT,
            PRIMARY KEY(manufacturer,current_oem)
        )""")
        conn.execute("""CREATE TABLE oem_catalog_aliases(
            manufacturer TEXT NOT NULL,
            alias_oem TEXT NOT NULL,
            current_oem TEXT NOT NULL,
            alias_kind TEXT NOT NULL,
            PRIMARY KEY(manufacturer,alias_oem)
        )""")
        conn.commit()


class ProductionWiringTests(unittest.TestCase):
    def test_01_resolver_requires_explicit_existing_db(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "EXTREMIZER_ORDERS_DB_FILE_REQUIRED"):
                prod.make_category_resolver()
        with self.assertRaisesRegex(RuntimeError, "EXTREMIZER_ORDERS_DB_FILE_NOT_FOUND"):
            prod.make_category_resolver(Path("definitely-missing.db"))

    def test_02_resolver_reads_production_authority_read_only(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"orders.db"
            create_authority_tables(db)
            with sqlite3.connect(db) as conn:
                conn.execute("""INSERT INTO oem_catalog_cache(
                    manufacturer,current_oem,name,catalog,msrp_usd,msrp_verified,
                    previous_oems_json,source_kind,source_ref,first_seen_at,last_verified_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                ("Polaris","2206328","QUICKDRIVE BELT KIT","Parts",239.99,1,
                 '["2205250"]',"validated_public_msrp","fixture",
                 "2026-10-16T18:00:00+00:00","2026-10-16T18:00:00+00:00"))
                conn.execute("""INSERT INTO oem_catalog_aliases VALUES(?,?,?,?)""",
                             ("Polaris","2206328","2206328","current"))
                conn.execute("""INSERT INTO oem_catalog_aliases VALUES(?,?,?,?)""",
                             ("Polaris","2205250","2206328","previous"))
                conn.commit()

            resolver=prod.make_category_resolver(db)
            old=resolver("Polaris","2205250")
            current=resolver("Polaris","2206328")
            self.assertEqual(old["resolved_oem"],"2206328")
            self.assertEqual(current["resolved_oem"],"2206328")
            self.assertEqual(old["item_category"],"Parts")

            with self.assertRaises(sqlite3.OperationalError):
                with prod._connect_read_only(db) as conn:
                    conn.execute("DELETE FROM oem_catalog_aliases")

    def test_03_provider_is_admin_only_and_preserves_global_fields(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"orders.db"
            create_authority_tables(db)
            with patch.object(prod, "admin_browser_price") as call:
                call.return_value={
                    "status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
                    "admin_unit_price_usd":"184.07",
                    "dealer_price_usd":None,
                    "production_found":False,
                }
                provider=prod.make_admin_polaris_provider(db)
                result=provider("2205250")
            self.assertEqual(result["admin_unit_price_usd"],"184.07")
            self.assertIsNone(result["dealer_price_usd"])
            self.assertIs(result["production_found"],False)
            self.assertEqual(call.call_count,1)

    def test_04_build_application_binds_provider_explicitly(self):
        fake_app=object()
        with patch("oemixibot_app.build_application", return_value=fake_app) as build, \
             patch("oemixibot_app.bind_admin_polaris_price_resolver") as bind, \
             patch.object(prod, "make_admin_polaris_provider", return_value="provider"):
            result=prod.build_application("token", db_path=Path(__file__))
        self.assertIs(result,fake_app)
        build.assert_called_once_with("token")
        bind.assert_called_once_with(fake_app,"provider")

    def test_05_seed_is_idempotent_and_resolves_both_oems(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db=Path(d)/"orders.db"
            create_authority_tables(db)
            first=seedmod.seed(db)
            second=seedmod.seed(db)
            self.assertEqual(first["status"],"SEEDED")
            self.assertEqual(second["status"],"SEEDED")
            with sqlite3.connect(db) as conn:
                self.assertEqual(
                    conn.execute("""SELECT COUNT(*) FROM oem_catalog_cache
                                   WHERE manufacturer='Polaris'""").fetchone()[0],1)
                self.assertEqual(
                    conn.execute("""SELECT COUNT(*) FROM oem_catalog_aliases
                                   WHERE manufacturer='Polaris'""").fetchone()[0],2)
                self.assertEqual(
                    dcp_category_authority.resolve(conn,"Polaris","2205250")["resolved_oem"],
                    "2206328")
                self.assertEqual(
                    dcp_category_authority.resolve(conn,"Polaris","2206328")["resolved_oem"],
                    "2206328")

    def test_06_seed_conflict_blocks_before_write(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as d:
            db=Path(d)/"orders.db"
            create_authority_tables(db)
            with sqlite3.connect(db) as conn:
                conn.execute("""INSERT INTO oem_catalog_cache(
                    manufacturer,current_oem,catalog,msrp_verified
                ) VALUES(?,?,?,?)""",("Polaris","2205250","Accessories",1))
                conn.commit()
                before=conn.total_changes
            with self.assertRaisesRegex(RuntimeError,"AUTHORITY_CONFLICT_OLD_DIRECT"):
                seedmod.seed(db)
            with sqlite3.connect(db) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM oem_catalog_aliases").fetchone()[0],0)
                self.assertEqual(
                    conn.execute("""SELECT COUNT(*) FROM oem_catalog_cache
                                   WHERE current_oem='2206328'""").fetchone()[0],0)

    def test_07_seed_missing_tables_fails_closed(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/"orders.db"
            sqlite3.connect(db).close()
            with self.assertRaisesRegex(RuntimeError,"AUTHORITY_TABLES_MISSING"):
                seedmod.preflight(db)


if __name__=="__main__":
    unittest.main()
