"""Windows-OFF *simulation*: accepted producer/HTTP/broker/cache, no network.

Validation only. Does not start Railway, Chrome, Telegram or the installed agent.
The MSRP inventory test deliberately records an unmet 720h requirement; it is
not an acceptance test declaring that consumer policy implemented.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import json
import logging
import math
import os
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import dcp_price_gateway as gateway
import dp_live_admission as admission
import dp_live_bridge as bridge
import dp_live_health as health
import test_dcp_producer_v2 as producer_support
from dcp_admission_test_support import approve_mode
from test_dcp_three_adapters import actual_functions
from test_dp_result_completion import completion, RAW_SOURCE, headers

DEALER_TTL_HOURS = 24
MSRP_TTL_HOURS = 720


class AutonomyFeasibilityTests(producer_support.ProducerV2Tests):
    def setUp(self):
        super().setUp()
        # Linux test process substitutes Railway; Windows remains untouched.
        self.assertEqual(os.name, "posix", "Windows-OFF rehearsal requires Linux CI")
        self.agent.BASE_URL = "https://producer-simulation.invalid"
        self.agent.PRODUCER_ID = "railway-simulation-boot-1"
        self.direct_browser = SimpleNamespace(get_dealer_price_dict=Mock(
            side_effect=AssertionError("consumer browser bypass forbidden")))
        self.finder = SimpleNamespace(
            manufacturer_alias=lambda m: bridge._request_key(m, "2670-265")[0],
            normalize_oem=lambda o: bridge._request_key("Arctic Cat", o)[1],
            CATALOGS=[])
        with sqlite3.connect(self.db) as c:
            c.execute("DELETE FROM dealer_price_cache")
            completion._write_identity(c, "Arctic Cat", "2670-265", "2670-265",
                                       "known fixture", "fixture", health._now())
            c.execute("""CREATE TABLE oem_catalog_cache(
                manufacturer,current_oem,name,catalog,msrp_usd,msrp_verified,
                previous_oems_json,source_kind,source_ref,first_seen_at,last_verified_at)""")
            c.execute("CREATE TABLE oem_catalog_aliases(manufacturer,current_oem,alias_oem,alias_kind)")
            now = health._now()
            c.execute("INSERT INTO oem_catalog_cache VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      ("Arctic Cat", "2670-265", "mock catalog", "Parts", 999, 1,
                       "[]", "validated_public_msrp", "fixture", now, now))
            c.execute("INSERT INTO oem_catalog_aliases VALUES('Arctic Cat','2670-265','2670-265','current')")
        self.agent._set_dcp_health = Mock(side_effect=lambda s, **kw: health.set_health(self.db, s))

    def adapters(self, lookup):
        # Execute actual accepted adapter function bodies; no bot imports/start.
        probnik = actual_functions("probnik_app.py",
            {"_identity_and_price", "_customer_price_rub", "_db_tables"}, {
                "ORDERS_DB": self.db, "DP_MAX_AGE_HOURS": DEALER_TTL_HOURS,
                "finder": self.finder, "sqlite3": sqlite3, "math": math,
                "normalize_oem": self.finder.normalize_oem,
                "PRICE_COEFFICIENT": 1, "USD_RUB_RATE": 100,
                "dp_live_bridge": SimpleNamespace(request_live_dp=lambda db,m,o,**kw: lookup(m,o))})
        procenka = actual_functions("extremizer_bot.py", {
            "enrich_found_result_with_dealer_price", "get_dealer_price_cache",
            "upsert_dealer_price_cache", "_dealer_price_cache_key"}, {
                "ORDERS_DB_FILE": self.db, "DP_CACHE_MAX_AGE_HOURS": DEALER_TTL_HOURS,
                "DCP_CACHE_ONLY": True, "datetime": datetime, "asyncio": asyncio,
                "sqlite3": sqlite3, "finder": self.finder, "init_orders_db": lambda: None,
                "log": logging.getLogger("autonomy-mock"),
                "dp_live_bridge": SimpleNamespace(request_live_dp=lambda db,m,o,**kw: lookup(m,o))})
        procenka["upsert_dealer_price_cache"] = Mock(
            side_effect=AssertionError("competing consumer DP writer forbidden"))
        dealer = actual_functions("oemixibot_catalog.py", {"resolve_identity", "resolve_offer"}, {
            "finder": self.finder, "oem_reference_service": SimpleNamespace(lookup_oem=lambda o: {}),
            "oem_identity_service": SimpleNamespace(lookup=lambda o: {"manufacturer": "Arctic Cat", "item_type": "parts"}),
            "oemixibot_dcp_price": self.direct_browser, "confirm_identity": Mock()})
        adapter = gateway.SharedDealerPriceAdapter(lookup, max_age_hours=DEALER_TTL_HOURS)
        return (
            lambda: probnik["_identity_and_price"]("2670-265"),
            lambda: asyncio.run(procenka["enrich_found_result_with_dealer_price"]({
                "status": "FOUND", "catalog": "Parts", "manufacturer": "Arctic Cat",
                "oem": "2670-265", "msrp_usd": 999})),
            lambda: dealer["resolve_offer"]("2670-265", price_gateway=adapter))

    def lookup(self, m, o, *, max_age_hours=DEALER_TTL_HOURS):
        self.assertEqual(max_age_hours, DEALER_TTL_HOURS)
        return gateway.request_price(self.db, m, o, max_age_hours=max_age_hours, wait_seconds=3)

    def cache(self, age_hours, *, source="verified_local_dcp:" + RAW_SOURCE):
        when = (datetime.now(timezone.utc) - timedelta(hours=age_hours)).isoformat()
        with sqlite3.connect(self.db) as c:
            completion._write_cache(c, "Arctic Cat", "2670-265", 31.25, source, when)

    def prices(self, results):
        return (results[0]["dp_usd"], results[1].get("_dealer_price_usd"), results[2].get("dl_usd"))

    def test_windows_off_three_actual_adapters_one_signed_producer_and_atomic_cache(self):
        barrier = threading.Barrier(3)
        original = bridge.request_live_dp
        def shared_live(db, m, o, **kw):
            barrier.wait(timeout=4)
            return original(db, m, o, **kw)
        def guarded_browser(*args):
            # Even a different OEM/producer cannot claim while this lookup runs.
            self.assertIsNone(bridge.claim_next(self.db, producer_id="second-producer"))
            return self.browser.get_dealer_price_dict.return_value
        self.browser.get_dealer_price_dict.side_effect = guarded_browser
        with patch.object(bridge, "request_live_dp", side_effect=shared_live):
            with ThreadPoolExecutor(max_workers=3) as pool:
                futures = [pool.submit(fn) for fn in self.adapters(self.lookup)]
                deadline = time.monotonic() + 4
                while time.monotonic() < deadline:
                    with sqlite3.connect(self.db) as c:
                        if c.execute("SELECT COUNT(*) FROM dp_live_requests WHERE status='pending'").fetchone()[0]:
                            break
                    time.sleep(0.01)
                else:
                    self.fail("consumer admissions never reached broker")
                self.assertTrue(self.agent.run_once())
                results = [f.result(timeout=5) for f in futures]
        self.assertEqual(self.prices(results), (31.25, 31.25, 31.25))
        self.assertEqual(results[0]["customer_rub"], 3200)
        self.browser.get_dealer_price_dict.assert_called_once()
        self.direct_browser.get_dealer_price_dict.assert_not_called()
        self.assertEqual(len(self.requests), 2)  # signed GET claim, signed POST result
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT status,result_status,claim_owner FROM dp_live_requests").fetchall(),
                             [("finished", "FOUND", "railway-simulation-boot-1")])
            self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache").fetchall(), [(31.25,)])
            self.assertEqual(c.execute("SELECT marker FROM orders").fetchall(), [("unchanged",)])
            self.assertEqual(c.execute("SELECT amount FROM finance_ledger").fetchall(), [(100,)])

    def test_dealer_23h_cache_serves_all_consumers_with_closed_admission_and_no_lookup(self):
        self.cache(23)
        approve_mode(self.db, "CLOSED")
        results = [fn() for fn in self.adapters(self.lookup)]
        self.assertEqual(self.prices(results), (31.25, 31.25, 31.25))
        self.assertEqual(self.requests, [])
        self.browser.get_dealer_price_dict.assert_not_called()

    def test_dealer_25h_is_not_substituted_even_with_fresh_msrp(self):
        self.cache(25)
        approve_mode(self.db, "CLOSED")
        results = [fn() for fn in self.adapters(self.lookup)]
        self.assertEqual(self.prices(results), (None, None, None))
        self.assertEqual(results[2]["status"], "ADMISSION_CLOSED")
        self.assertEqual(self.requests, [])
        self.browser.get_dealer_price_dict.assert_not_called()
        self.direct_browser.get_dealer_price_dict.assert_not_called()

    def test_cloudflare_and_auth_block_every_consumer_and_latch_producer(self):
        for status in ("CLOUDFLARE", "AUTH_REQUIRED"):
            with self.subTest(status=status):
                health.set_health(self.db, "READY")
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM dp_live_requests")
                    c.execute("UPDATE dp_live_coordination SET blocked_status=NULL,blocked_at=NULL")
                self.agent._PRODUCER_STOP = False
                self.browser.get_dealer_price_dict.return_value = {"status": status}
                self.enqueue()
                self.assertTrue(self.agent.run_once())
                requests_before = len(self.requests)
                results = [fn() for fn in self.adapters(self.lookup)]
                self.assertEqual(self.prices(results), (None, None, None))
                self.assertEqual(results[2]["status"], status)
                self.assertFalse(self.agent.run_once())
                self.assertEqual(len(self.requests), requests_before)
                # Passive READY is not permission to clear producer's STOP.
                health.set_health(self.db, "READY")
                self.assertFalse(self.agent.run_once())

    def test_lost_ack_after_commit_preserves_cache_and_blocks_second_lookup_on_restart(self):
        self.enqueue()
        self.drop_after_commit = True
        with self.assertRaises(TimeoutError):
            self.agent.run_once()
        self.assertEqual(self.agent.JOURNAL_FILE.read_text().find("result_delivery_uncertain") >= 0, True)
        self.agent._PRODUCER_STOP = False  # model a fresh boot's volatile state only
        self.agent._acquire_singleton = lambda: True
        self.agent._preflight_dcp = Mock(side_effect=AssertionError("no browser on unresolved journal"))
        self.assertEqual(self.agent.main(), 24)
        self.assertFalse(self.agent.run_once())
        results = [fn() for fn in self.adapters(self.lookup)]
        self.assertEqual(self.prices(results), (31.25, 31.25, 31.25))
        self.browser.get_dealer_price_dict.assert_called_once()

    def test_duplicate_result_cannot_complete_twice(self):
        self.enqueue()
        self.agent.run_once()
        method, path, body, signed = self.requests[-1]
        before = self.snapshot()
        response = self.client.request(method, path, content=body, headers=signed)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.snapshot(), before)

    def test_retail_and_msrp_producer_output_never_write_dealer_cache(self):
        for change in ({"dealer_price_usd": None, "msrp_usd": 999},
                       {"source": "dealerorders_public_retail"}, {"currency": "RUB"}):
            with self.subTest(change=change):
                with sqlite3.connect(self.db) as c:
                    c.execute("DELETE FROM dp_live_requests")
                self.agent._PRODUCER_STOP = False
                health.set_health(self.db, "READY")
                base = {"status": "FOUND", "manufacturer": "Arctic Cat", "query_oem": "2670-265",
                        "matched_sku": "2670-265", "current_oem": "2670-265", "dealer_price_usd": 31.25,
                        "source": RAW_SOURCE, "currency": "USD"}
                self.browser.get_dealer_price_dict.return_value = {**base, **change}
                self.enqueue()
                self.agent.run_once()
                with sqlite3.connect(self.db) as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_price_cache").fetchone()[0], 0)
                    self.assertEqual(c.execute("SELECT result_status FROM dp_live_requests").fetchone()[0], "TECHNICAL_ERROR")

    def test_distinct_oems_cannot_create_second_active_claim(self):
        self.enqueue()
        self.enqueue("Honda", "91303-PA9-004")
        first = bridge.claim_next(self.db, producer_id="cloud-one")
        self.assertIsNotNone(first)
        self.assertIsNone(bridge.claim_next(self.db, producer_id="cloud-two"))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dp_live_requests WHERE status='claimed'").fetchone()[0], 1)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dp_live_requests WHERE status='pending'").fetchone()[0], 1)

    def test_crash_keeps_disk_journal_and_quarantines_without_auto_requeue(self):
        self.enqueue()
        self.browser.get_dealer_price_dict.side_effect = KeyboardInterrupt("simulated crash")
        with self.assertRaises(KeyboardInterrupt):
            self.agent.run_once()
        self.assertTrue(self.agent.JOURNAL_FILE.exists())
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE dp_live_requests SET lease_expires_at=?",
                      ((datetime.now(timezone.utc) - timedelta(seconds=2)).isoformat(),))
        self.assertIsNone(bridge.claim_next(self.db, producer_id="replacement"))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT status FROM dp_live_requests").fetchall(), [("orphaned",)])
        self.agent._PRODUCER_STOP = False
        self.assertFalse(self.agent.run_once())
        self.browser.get_dealer_price_dict.assert_called_once()

    def test_records_unmet_msrp_720h_requirement_in_actual_accepted_reader(self):
        # Diagnostic: this deliberately confirms a GAP, not MSRP policy PASS.
        ns = actual_functions("extremizer_bot.py", {"get_oem_catalog_cache_result"}, {
            "ORDERS_DB_FILE": self.db, "finder": self.finder, "sqlite3": sqlite3,
            "json": json, "init_orders_db": lambda: None})
        for age in (MSRP_TTL_HOURS - 1, MSRP_TTL_HOURS + 1):
            with sqlite3.connect(self.db) as c:
                c.execute("UPDATE oem_catalog_cache SET last_verified_at=?",
                          ((datetime.now(timezone.utc) - timedelta(hours=age)).isoformat(),))
            result = ns["get_oem_catalog_cache_result"]("Arctic Cat", "2670-265")
            self.assertEqual(result["price"], 999)
        # Accepted reader still returns 721h MSRP. Fix belongs to coordinated
        # consumer policy integration; this validation patch cannot call it ready.
        self.assertEqual(MSRP_TTL_HOURS, 720)

    def test_linux_process_singleton_is_not_provided_by_windows_mutex(self):
        # Diagnostic: broker excludes concurrent claims, but Linux process
        # singleton needs a future host lock + rollout authority (not a fake PASS).
        self.assertTrue(self.agent._acquire_singleton())
        self.assertTrue(self.agent._acquire_singleton())

    def test_gateway_never_uses_uncommitted_found(self):
        result = gateway.request_price(self.db, "Arctic Cat", "2670-265",
            max_age_hours=DEALER_TTL_HOURS,
            live_request=lambda *a, **kw: {"status": "FOUND", "dealer_price_usd": 999})
        self.assertEqual(result["status"], "NO_PRICE")


def load_tests(loader, tests, pattern):
    # Reuse accepted setup without re-running inherited Stage 3 test methods.
    names = sorted(n for n in AutonomyFeasibilityTests.__dict__ if n.startswith("test_"))
    return unittest.TestSuite(AutonomyFeasibilityTests(n) for n in names)
