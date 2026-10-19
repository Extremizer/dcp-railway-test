"""P0: verified identity is separate from DCP price and startup seed.
All SQLite fixtures are temporary and never connect to production.
"""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import probnik_app as app


def database(path, *, identities=(), dealer=()):
    with sqlite3.connect(path) as conn:
        conn.execute("""CREATE TABLE probnik_oem_identity (
            manufacturer TEXT, oem TEXT, current_oem TEXT, name TEXT,
            verified INTEGER, source_kind TEXT, source_ref TEXT,
            last_verified_at TEXT)""")
        conn.execute("""CREATE TABLE dealer_price_cache (
            manufacturer TEXT, oem TEXT, dealer_price_usd REAL,
            last_verified_at TEXT)""")
        conn.executemany(
            "INSERT INTO probnik_oem_identity VALUES(?,?,?,?,?,?,?,?)",
            [(m,o,o,n,1,"verified_public_catalog",
              "https://ski-doo-shop.brp.com/ca/en/release-spring-417127467.html",
              "2026-10-19T00:00:00+00:00") for m,o,n in identities],
        )
        conn.executemany("INSERT INTO dealer_price_cache VALUES(?,?,?,?)",dealer)


class ProbnikIdentityP0(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/"synthetic_orders.db"

    def lookup(self):
        with patch.object(app,"ORDERS_DB",self.path), patch.object(
            app.dp_live_bridge,"request_live_dp",return_value={"status":"ADMISSION_CLOSED"}
        ) as bridge:
            result=app._identity_and_price("417127467")
        return result,bridge

    def test_no_approved_identity_never_requests_auto(self):
        database(self.path)
        info,bridge=self.lookup()
        self.assertIsNone(info["manufacturer"])
        self.assertIsNone(info["dp_usd"])
        bridge.assert_not_called()

    def test_canonical_identity_kept_if_dp_admission_closed(self):
        database(self.path,identities=[("Ski-Doo","417127467","Release Spring")])
        info,bridge=self.lookup()
        self.assertEqual(info["manufacturer"],"Ski-Doo")
        self.assertEqual(info["name"],"Release Spring")
        self.assertIsNone(info["dp_usd"])
        self.assertFalse(info["price_fresh"])
        bridge.assert_called_once()
        self.assertEqual(bridge.call_args.args[1:3],("Ski-Doo","417127467"))
        text=app._compose("417127467",info,[])
        self.assertIn("Ski-Doo",text)
        self.assertIn("Цена сейчас недоступна",text)

    def test_conflict_is_fail_closed(self):
        database(self.path,identities=[
            ("Ski-Doo","417127467","Release Spring"),
            ("Polaris","417127467","Conflict")])
        info,bridge=self.lookup()
        self.assertIsNone(info["manufacturer"])
        bridge.assert_not_called()

    def test_startup_seed_not_extended(self):
        import inspect
        src=inspect.getsource(app.init_probnik_identity)
        self.assertNotIn('"417127467"',src)
        self.assertIn('"860200783"',src)
        self.assertIn('"3006-682"',src)

    def test_no_msrp_price_fallback(self):
        database(self.path,identities=[("Ski-Doo","417127467","Release Spring")])
        info,_=self.lookup()
        self.assertIsNone(info["customer_rub"])
        self.assertIsNone(info["rrp_rub"])


if __name__=="__main__":
    unittest.main()
