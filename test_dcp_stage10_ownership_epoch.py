"""Stage 10 detached shared-broker ownership tests, disposable SQLite only."""
import sqlite3
import tempfile
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import unittest
import dcp_stage10_ownership_epoch as epoch

def race(db, name):
    try:
        return epoch.acquire(db,name)
    except epoch.OwnershipDenied:
        return None

class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db=str(Path(self.temp.name)/"broker.sqlite")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE dp_live_requests(status TEXT)")
    def test_two_independent_processes_one_owner(self):
        with ProcessPoolExecutor(max_workers=2) as pool:
            a=list(pool.map(race,[self.db]*2,["old-host","new-host"]))
        winners=[x for x in a if x]
        self.assertEqual(len(winners),1)
        with sqlite3.connect(self.db) as c:
            self.assertTrue(epoch.validate(c,winners[0]))
            self.assertFalse(epoch.validate(c,{"epoch":winners[0]["epoch"],"owner":"impostor","nonce":winners[0]["nonce"]}))
    def test_expiry_never_authorizes_takeover(self):
        old=epoch.acquire(self.db,"old")
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_producer_ownership SET updated_at='2000-01-01T00:00:00+00:00'")
        with self.assertRaises(epoch.OwnershipDenied):
            epoch.acquire(self.db,"new")
        with self.assertRaises(epoch.OwnershipDenied):
            epoch.revoke(self.db,expected_epoch=old["epoch"],cessation_confirmed=False,evidence_ref="e"*64)
    def test_quarantine_and_stale_producer(self):
        old=epoch.acquire(self.db,"old")
        epoch.quarantine(self.db,old["epoch"])
        with sqlite3.connect(self.db) as c:self.assertFalse(epoch.validate(c,old))
        with self.assertRaises(epoch.OwnershipDenied):epoch.acquire(self.db,"new")
        epoch.revoke(self.db,expected_epoch=old["epoch"],cessation_confirmed=True,evidence_ref="e"*64)
        new=epoch.acquire(self.db,"new")
        self.assertGreater(new["epoch"],old["epoch"])
        with sqlite3.connect(self.db) as c:
            self.assertFalse(epoch.validate(c,old))
            self.assertTrue(epoch.validate(c,new))
    def test_unreconciled_claim_blocks_revoke(self):
        old=epoch.acquire(self.db,"old")
        with sqlite3.connect(self.db) as c:c.execute("INSERT INTO dp_live_requests VALUES ('orphaned')")
        with self.assertRaises(epoch.OwnershipDenied):
            epoch.revoke(self.db,expected_epoch=old["epoch"],cessation_confirmed=True,evidence_ref="e"*64)
        with sqlite3.connect(self.db) as c:self.assertTrue(epoch.validate(c,old))
