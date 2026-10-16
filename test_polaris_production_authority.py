import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import dcp_category_authority as authority
from oemixi_admin_polaris_browser_price import _category
from polaris_probe_errors import ProbeFailure


class PolarisProductionAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot_path=Path(__file__).with_name("dcp_public_msrp_cache.json")
        cls.snapshot=json.loads(cls.snapshot_path.read_text(encoding="utf-8"))

    def test_01_snapshot_exact_proven_pair(self):
        entries=self.snapshot.get("entries") or []
        self.assertEqual(len(entries),1)
        e=entries[0]
        self.assertEqual(e["manufacturer"],"Polaris")
        self.assertEqual(e["oem"],"2206328")
        self.assertEqual(e["previous_oems"],["2205250"])
        self.assertEqual(e["catalog"],"Parts")
        self.assertEqual(e["msrp_usd"],239.99)
        self.assertEqual(e["source_kind"],"validated_public_msrp")
        self.assertTrue(e["source_file"])
        self.assertTrue(e["captured_at"])

    def _bootstrap_db(self):
        import extremizer_bot as core
        tmp=tempfile.TemporaryDirectory()
        db=Path(tmp.name)/"orders.db"
        old_db=core.ORDERS_DB_FILE
        old_snapshot=core.PUBLIC_MSRP_CACHE_FILE
        core.ORDERS_DB_FILE=db
        core.PUBLIC_MSRP_CACHE_FILE=self.snapshot_path
        try:
            core.init_orders_db()
        finally:
            core.ORDERS_DB_FILE=old_db
            core.PUBLIC_MSRP_CACHE_FILE=old_snapshot
        return tmp,db

    def test_02_real_bootstrap_current_and_previous_authority(self):
        tmp,db=self._bootstrap_db()
        try:
            with sqlite3.connect(db) as conn:
                current=authority.resolve(conn,"Polaris","2206328")
                previous=authority.resolve(conn,"Polaris","2205250")
                self.assertIsNotNone(current)
                self.assertIsNotNone(previous)
                self.assertEqual(current["item_category"],"Parts")
                self.assertEqual(current["resolved_oem"],"2206328")
                self.assertEqual(previous["resolved_oem"],"2206328")
                self.assertEqual(previous["category_source_kind"],"validated_public_msrp")
                aliases=conn.execute(
                    """SELECT alias_oem,current_oem,alias_kind
                         FROM oem_catalog_aliases
                        WHERE manufacturer='Polaris'
                        ORDER BY alias_oem"""
                ).fetchall()
                self.assertEqual(aliases,[
                    ("2205250","2206328","previous"),
                    ("2206328","2206328","current"),
                ])
        finally:
            tmp.cleanup()

    def test_03_conflicting_direct_old_sku_fails_closed(self):
        tmp,db=self._bootstrap_db()
        try:
            with sqlite3.connect(db) as conn:
                row=conn.execute(
                    """SELECT source_kind,source_ref,last_verified_at
                         FROM oem_catalog_cache
                        WHERE manufacturer='Polaris' AND current_oem='2206328'"""
                ).fetchone()
                conn.execute(
                    """INSERT INTO oem_catalog_cache(
                           manufacturer,current_oem,catalog,msrp_verified,
                           source_kind,source_ref,first_seen_at,last_verified_at
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    ("Polaris","2205250","Accessories",1,row[0],row[1],row[2],row[2]),
                )
                conn.commit()
                self.assertIsNone(authority.resolve(conn,"Polaris","2205250"))
        finally:
            tmp.cleanup()

    @staticmethod
    def evidence(kind,stamp):
        return {
            "item_category":"Parts",
            "resolved_oem":"2206328",
            "category_source_kind":kind,
            "category_source_ref":"isolated-test",
            "category_verified_at":stamp.isoformat(),
        }

    def test_04_source_aware_ttl(self):
        now=datetime(2026,10,16,20,0,0,tzinfo=timezone.utc)
        validated=self.evidence("validated_public_msrp",now-timedelta(hours=719))
        self.assertEqual(_category("2205250",lambda *_:validated,now)["resolved_oem"],"2206328")
        stale=self.evidence("validated_public_msrp",now-timedelta(hours=721))
        with self.assertRaises(ProbeFailure) as cm:
            _category("2205250",lambda *_:stale,now)
        self.assertEqual(cm.exception.code,"CATEGORY_STALE")

        live=self.evidence("live_public_form_metadata",now-timedelta(hours=23))
        self.assertEqual(_category("2205250",lambda *_:live,now)["resolved_oem"],"2206328")
        live_stale=self.evidence("live_public_form_metadata",now-timedelta(hours=25))
        with self.assertRaises(ProbeFailure) as cm:
            _category("2205250",lambda *_:live_stale,now)
        self.assertEqual(cm.exception.code,"CATEGORY_STALE")

    def test_05_future_and_unknown_source_fail_closed(self):
        now=datetime(2026,10,16,20,0,0,tzinfo=timezone.utc)
        future=self.evidence("validated_public_msrp",now+timedelta(seconds=1))
        with self.assertRaises(ProbeFailure) as cm:
            _category("2205250",lambda *_:future,now)
        self.assertEqual(cm.exception.code,"CATEGORY_STALE")
        unknown=self.evidence("arbitrary",now)
        with self.assertRaises(ProbeFailure) as cm:
            _category("2205250",lambda *_:unknown,now)
        self.assertEqual(cm.exception.code,"CATEGORY_UNVERIFIED")


if __name__=="__main__":
    unittest.main()
