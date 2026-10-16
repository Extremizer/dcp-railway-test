import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

import oemixi_category_authority_transport as transport

SECRET="isolated-oemixi-category-authority-secret"


def make_db(path):
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
        stamp="2026-10-16T18:00:00+00:00"
        conn.execute("""INSERT INTO oem_catalog_cache VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            ("Polaris","2206328","QUICKDRIVE BELT KIT","Parts",239.99,1,
             '["2205250"]',"validated_public_msrp","fixture",stamp,stamp))
        conn.execute("INSERT INTO oem_catalog_aliases VALUES(?,?,?,?)",
                     ("Polaris","2206328","2206328","current"))
        conn.execute("INSERT INTO oem_catalog_aliases VALUES(?,?,?,?)",
                     ("Polaris","2205250","2206328","previous"))
        conn.commit()


def table_digest(path):
    with sqlite3.connect(path) as conn:
        rows={}
        for table in ("oem_catalog_cache","oem_catalog_aliases"):
            rows[table]=conn.execute(f"SELECT * FROM {table} ORDER BY 1,2").fetchall()
    return hashlib.sha256(json.dumps(rows,sort_keys=True,default=str).encode()).hexdigest()


class CategoryAuthorityBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=Path(self.tmp.name)/"orders.db"
        make_db(self.db)
        app=FastAPI()
        app.include_router(transport.build_router(db_file=self.db,root_secret=SECRET))
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()

    def _headers(self,manufacturer="Polaris",oem="2205250",ts=None,secret=SECRET):
        manufacturer,oem=transport.normalize_identity(manufacturer,oem)
        body=transport.canonical_body(manufacturer,oem)
        ts=str(int(time.time()) if ts is None else ts)
        sig=transport.sign_request(secret,timestamp=ts,body=body)
        return {
            "X-OEMixi-Authority-TS":ts,
            "X-OEMixi-Authority-Sig":sig,
        }

    def test_01_previous_oem_resolves_read_only(self):
        before=table_digest(self.db)
        r=self.client.post(
            transport.PATH,
            json={"manufacturer":"Polaris","oem":"2205250"},
            headers=self._headers(),
        )
        self.assertEqual(r.status_code,200)
        data=r.json()
        self.assertEqual(data["status"],"CATEGORY_VERIFIED")
        self.assertEqual(data["resolved_oem"],"2206328")
        self.assertEqual(data["item_category"],"Parts")
        self.assertEqual(data["category_source_kind"],"validated_public_msrp")
        self.assertEqual(before,table_digest(self.db))

    def test_02_current_oem_resolves(self):
        r=self.client.post(
            transport.PATH,
            json={"manufacturer":"Polaris","oem":"2206328"},
            headers=self._headers(oem="2206328"),
        )
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["resolved_oem"],"2206328")

    def test_03_unknown_oem_fail_closed(self):
        r=self.client.post(
            transport.PATH,
            json={"manufacturer":"Polaris","oem":"9999999"},
            headers=self._headers(oem="9999999"),
        )
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["status"],"CATEGORY_UNVERIFIED")
        self.assertNotIn("item_category",r.json())

    def test_04_bad_signature_rejected(self):
        h=self._headers()
        h["X-OEMixi-Authority-Sig"]="0"*64
        r=self.client.post(
            transport.PATH,
            json={"manufacturer":"Polaris","oem":"2205250"},
            headers=h,
        )
        self.assertEqual(r.status_code,401)

    def test_05_stale_signature_rejected(self):
        stale=int(time.time())-120
        r=self.client.post(
            transport.PATH,
            json={"manufacturer":"Polaris","oem":"2205250"},
            headers=self._headers(ts=stale),
        )
        self.assertEqual(r.status_code,401)

    def test_06_extra_request_field_rejected(self):
        r=self.client.post(
            transport.PATH,
            json={"manufacturer":"Polaris","oem":"2205250","write":True},
            headers=self._headers(),
        )
        self.assertEqual(r.status_code,422)

    def test_07_missing_db_returns_503(self):
        missing=Path(self.tmp.name)/"missing.db"
        app=FastAPI()
        app.include_router(transport.build_router(db_file=missing,root_secret=SECRET))
        with TestClient(app) as client:
            r=client.post(
                transport.PATH,
                json={"manufacturer":"Polaris","oem":"2205250"},
                headers=self._headers(),
            )
        self.assertEqual(r.status_code,503)

    def test_08_invalid_secret_refused_at_build(self):
        with self.assertRaises(ValueError):
            transport.build_router(db_file=self.db,root_secret="short")


if __name__=="__main__":
    unittest.main()
