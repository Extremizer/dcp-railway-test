"""Synthetic Stage A tests, no production DB or DCP."""
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import dcp_category_authority
from oemixi_category_bootstrap import bootstrap_category

def evidence():
    return {
        "verified": True, "manufacturer": "Polaris",
        "requested_oem": "3211070", "current_oem": "3211070",
        "catalog": "Parts", "name": "DRIVE BELT",
        "msrp_usd": 114.99, "source_kind": "live_public_form_metadata",
        "source_ref": "synthetic:exact_pol_parts_3211070",
        "verified_at": datetime.now(timezone.utc).isoformat(),
    }

class StageABootstrapTests(unittest.TestCase):
    def setUp(self):
        self.t = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = str(Path(self.t.name)/"synthetic.db")
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TABLE oem_catalog_cache
                (manufacturer TEXT,current_oem TEXT,name TEXT,catalog TEXT,msrp_usd REAL,
                 msrp_verified INTEGER,previous_oems_json TEXT,source_kind TEXT,source_ref TEXT,
                 first_seen_at TEXT,last_verified_at TEXT,PRIMARY KEY(manufacturer,current_oem))""")
            c.execute("""CREATE TABLE oem_catalog_aliases
                (manufacturer TEXT,alias_oem TEXT,current_oem TEXT,alias_kind TEXT,
                PRIMARY KEY(manufacturer,alias_oem))""")
    def tearDown(self):
        self.t.cleanup()
    def inspect(self):
        with sqlite3.connect(self.db) as c:
            count=c.execute("SELECT COUNT(*) FROM oem_catalog_cache").fetchone()[0]
            aliases=c.execute("SELECT COUNT(*) FROM oem_catalog_aliases").fetchone()[0]
        return count,aliases
    def test_fresh_db_starts_unverified(self):
        with sqlite3.connect(self.db) as c:
            self.assertIsNone(dcp_category_authority.resolve(c,"Polaris","3211070"))
    def test_absent_independent_verifier_no_writes(self):
        self.assertEqual(bootstrap_category(self.db,"Polaris","3211070")["status"],
                         "ENROLLMENT_VERIFIER_UNAVAILABLE")
        self.assertEqual(self.inspect(),(0,0))
    def test_verified_exact_evidence_bootstraps_resolvable_authority(self):
        result=bootstrap_category(self.db,"Polaris","3211070",trusted_catalog_verifier=lambda m,o:evidence())
        self.assertEqual(result["status"],"CATEGORY_VERIFIED")
        with sqlite3.connect(self.db) as c:
            self.assertEqual(dcp_category_authority.resolve(c,"Polaris","3211070")["resolved_oem"],"3211070")
            row=c.execute("SELECT msrp_usd,msrp_verified FROM oem_catalog_cache").fetchone()
            self.assertEqual(row,(114.99,1))
        self.assertEqual(self.inspect(),(1,1))
    def test_wrong_manufacturer_fails_without_write(self):
        self.assertEqual(bootstrap_category(self.db,"Honda","3211070",
            trusted_catalog_verifier=lambda m,o:evidence())["status"],"MANUFACTURER_NOT_ENABLED")
        self.assertEqual(self.inspect(),(0,0))
    def test_mismatched_exact_oem_fails(self):
        bad={**evidence(),"requested_oem":"9999999"}
        self.assertEqual(bootstrap_category(self.db,"Polaris","3211070",
           trusted_catalog_verifier=lambda m,o:bad)["status"],"CATEGORY_MISMATCH")
        self.assertEqual(self.inspect(),(0,0))
    def test_alias_fails_closed(self):
        bad={**evidence(),"current_oem":"9999999"}
        self.assertEqual(bootstrap_category(self.db,"Polaris","3211070",
           trusted_catalog_verifier=lambda m,o:bad)["status"],"ALIAS_AUTHORITY_REQUIRED")
        self.assertEqual(self.inspect(),(0,0))
    def test_untrusted_source_rejected(self):
        bad={**evidence(),"source_kind":"user_selection"}
        self.assertEqual(bootstrap_category(self.db,"Polaris","3211070",
           trusted_catalog_verifier=lambda m,o:bad)["status"],"CATEGORY_UNVERIFIED")
        self.assertEqual(self.inspect(),(0,0))
    def test_unverified_flag_rejected(self):
        bad={**evidence(),"verified":False}
        self.assertEqual(bootstrap_category(self.db,"Polaris","3211070",
           trusted_catalog_verifier=lambda m,o:bad)["status"],"CATEGORY_UNVERIFIED")
        self.assertEqual(self.inspect(),(0,0))
    def test_retry_idempotent(self):
        for i in range(2):
            self.assertEqual(bootstrap_category(self.db,"Polaris","3211070",
                trusted_catalog_verifier=lambda m,o:evidence())["status"],"CATEGORY_VERIFIED")
        self.assertEqual(self.inspect(),(1,1))
if __name__ == "__main__":
    unittest.main()
