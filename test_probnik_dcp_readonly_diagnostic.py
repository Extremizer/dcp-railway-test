import sqlite3
from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
import unittest
from probnik_dcp_readonly_diagnostic import inspect_copy

NOW=datetime(2026,10,24,8,0,tzinfo=timezone.utc)
class ProbeTests(unittest.TestCase):
 def test_closed_admission_no_cache_and_stale_health(self):
  with TemporaryDirectory() as d:
   p=Path(d)/"sample.db"
   with sqlite3.connect(p) as c:
    c.executescript("""
CREATE TABLE dealer_price_cache(manufacturer TEXT,oem TEXT,dealer_price_usd REAL,last_verified_at TEXT);
CREATE TABLE dp_live_admission(singleton_id INTEGER,mode TEXT);
CREATE TABLE dp_live_health(singleton_id INTEGER,status TEXT,checked_at TEXT);
CREATE TABLE dp_live_coordination(singleton_id INTEGER,blocked_status TEXT);
CREATE TABLE dp_live_requests(manufacturer TEXT,oem TEXT,status TEXT,result_status TEXT,error_code TEXT,requested_at TEXT);
INSERT INTO dp_live_admission VALUES(1,'CLOSED');
INSERT INTO dp_live_health VALUES(1,'CLOUDFLARE','2026-10-23T01:00:00+00:00');
INSERT INTO dp_live_coordination VALUES(1,'CLOUDFLARE');
INSERT INTO dealer_price_cache VALUES('Ski-Doo','417300574',100.00,'2026-10-20T01:00:00+00:00');
INSERT INTO dp_live_requests VALUES('Ski-Doo','417300574','finished','TIMEOUT','test_error','2026-10-24T07:00:00Z');
""")
   r=inspect_copy(p,now=NOW,health_enforced=True)
   item=r["items"][0]
   self.assertEqual(item["cache"],"STALE")
   self.assertEqual(item["live_admission"],"CLOSED")
   self.assertEqual(item["coordination"],"CLOUDFLARE")
   self.assertTrue(item["health_is_stale"])
   self.assertIn("LIVE_ADMISSION_NOT_CONFIRMED_OPEN",item["blocker_candidates"])
   self.assertIn("HEALTH_GATE_BLOCKING",item["blocker_candidates"])
   self.assertNotIn("100",str(r))
   self.assertNotIn("test_error",str(r))
 def test_without_cache_table_fail_closed_no_request(self):
  with TemporaryDirectory() as d:
   p=Path(d)/"empty.db"
   sqlite3.connect(p).close()
   r=inspect_copy(p,now=NOW)
   self.assertEqual(r["items"][0]["cache"],"SCHEMA_MISSING")
   self.assertIn("PROBNIK_RETURN_BEFORE_LIVE_WHEN_CACHE_TABLE_MISSING",r["items"][0]["blocker_candidates"])
   self.assertFalse(r["live_requests_created"])
 def test_invalid_ttl_denied(self):
  with TemporaryDirectory() as d:
   p=Path(d)/"empty.db";sqlite3.connect(p).close()
   with self.assertRaisesRegex(ValueError,"INVALID_CACHE_TTL"):inspect_copy(p,now=NOW,cache_age_hours=0)
if __name__=="__main__":unittest.main()
