"""Synthetic signed transport; no external web or production DB."""
import asyncio
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from oemixi_enrollment_transport import create_router,make_headers,canonical_payload,PATH

SECRET="synthetic-only-secret-abcdefghijklmnopqrstuvwxyz-123456"

def proof(m,o):
    return {"verified":True,"manufacturer":m,"requested_oem":o,"current_oem":o,
            "catalog":"Parts","name":"DRIVE BELT","msrp_usd":114.99,
            "source_kind":"live_public_form_metadata","source_ref":"synthetic:exact",
            "verified_at":datetime.now(timezone.utc).isoformat()}

class EnrollmentTransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db=str(Path(self.tmp.name)/"test.db")
        c=sqlite3.connect(self.db)
        c.executescript("""
          CREATE TABLE oem_catalog_cache(manufacturer TEXT,current_oem TEXT,name TEXT,
          catalog TEXT,msrp_usd REAL,msrp_verified INTEGER,previous_oems_json TEXT,
          source_kind TEXT,source_ref TEXT,first_seen_at TEXT,last_verified_at TEXT,
          PRIMARY KEY(manufacturer,current_oem));
          CREATE TABLE oem_catalog_aliases(manufacturer TEXT,alias_oem TEXT,
          current_oem TEXT,alias_kind TEXT,PRIMARY KEY(manufacturer,alias_oem));
        """)
        c.close()
        app=FastAPI()
        app.include_router(create_router(db_file=self.db,secret=SECRET,verifier=proof))
        self.client=TestClient(app)
    def tearDown(self):
        self.client.close()
        self.tmp.cleanup()
    def submit(self,body=None,headers=None):
        body=body or canonical_payload("Polaris","3211070")
        return self.client.post(PATH,content=body,headers=headers or make_headers(SECRET,body))
    def count(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM oem_catalog_cache").fetchone()[0]
    def test_signed_authority_from_empty_db(self):
        self.assertEqual(self.count(),0)
        r=self.submit()
        self.assertEqual(r.status_code,200)
        self.assertEqual(r.json()["status"],"CATEGORY_VERIFIED")
        self.assertEqual(self.count(),1)
    def test_replay_denied(self):
        body=canonical_payload("Polaris","3211070")
        h=make_headers(SECRET,body)
        self.assertEqual(self.submit(body,h).status_code,200)
        self.assertEqual(self.submit(body,h).status_code,409)
        self.assertEqual(self.count(),1)
    def test_tampered_body_denied(self):
        original=canonical_payload("Polaris","3211070")
        h=make_headers(SECRET,original)
        self.assertEqual(self.submit(canonical_payload("Honda","3211070"),h).status_code,403)
        self.assertEqual(self.count(),0)
    def test_bad_signature_denied(self):
        b=canonical_payload("Polaris","3211070")
        h=make_headers(SECRET,b);h["X-Enrollment-Signature"]="0"*64
        self.assertEqual(self.submit(b,h).status_code,403)
        self.assertEqual(self.count(),0)
    def test_untrusted_verifier_blocks_authority(self):
        app=FastAPI()
        app.include_router(create_router(db_file=self.db,secret=SECRET,
                         verifier=lambda m,o:{**proof(m,o),"verified":False}))
        with TestClient(app) as client:
            b=canonical_payload("Polaris","3211070")
            r=client.post(PATH,content=b,headers=make_headers(SECRET,b))
            self.assertEqual(r.json()["status"],"CATEGORY_UNVERIFIED")
        self.assertEqual(self.count(),0)
if __name__=="__main__":
    unittest.main()
