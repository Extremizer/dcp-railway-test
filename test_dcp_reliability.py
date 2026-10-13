# -*- coding: utf-8 -*-
"""Mock-only reliability tests. No browser, DCP, Telegram or production DB."""
import ast
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
from typing import Any
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit

import dp_live_bridge as bridge
import dp_live_health as health

ROOT = Path(__file__).resolve().parent
SOURCE = "verified_local_dcp:server_html:exact_dealer_row"


def functions(filename, names, namespace):
    tree = ast.parse((ROOT / filename).read_text(encoding="utf-8"))
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), filename, "exec"), namespace)
    return namespace


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "mock.db"
        self.env = patch.dict(os.environ, {"EXTREMIZER_DP_HEALTH_ENFORCE": "1", "EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS": "0"})
        self.env.start()
        bridge.init(self.db)
        health.set_health(self.db, "READY")

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def enqueue(self, manufacturer="Arctic Cat", oem="2670-265"):
        return bridge.request_live_dp(self.db, manufacturer, oem, wait_seconds=0)

    def finish(self, request_id, **kwargs):
        # Existing Stage 1 tests now provide the fence returned by the broker.
        with sqlite3.connect(self.db) as conn:
            row=conn.execute("SELECT claim_token,claim_generation,claim_owner FROM dp_live_requests WHERE request_id=?",(request_id,)).fetchone()
        if row and row[0]:
            kwargs.update(claim_token=row[0],claim_generation=row[1],producer_id=row[2])
        return bridge.finish(self.db,request_id,**kwargs)

    def test_three_consumer_requests_share_one_lookup(self):
        # These are broker contract consumers; production OEMixi is NOT yet wired
        # to this broker. Its migration remains a separate documented prerequisite.
        barrier = threading.Barrier(3)
        def consumer(manufacturer):
            barrier.wait(timeout=3)
            return bridge.request_live_dp(self.db, manufacturer, " 2670-265 ", wait_seconds=3)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(consumer, m) for m in ("Arctic Cat", "arctic_cat", "ARCTIC-CAT")]
            deadline = time.monotonic() + 2
            lookup = None
            while lookup is None and time.monotonic() < deadline:
                lookup = bridge.claim_next(self.db)
                if lookup is None: time.sleep(0.01)
            self.assertIsNotNone(lookup)
            self.assertIsNone(bridge.claim_next(self.db))
            self.finish(lookup["request_id"], result_status="FOUND", dealer_price_usd=31.25, source=SOURCE)
            results = [f.result(timeout=4) for f in futures]
        self.assertEqual({r["request_id"] for r in results}, {lookup["request_id"]})
        self.assertEqual([r["status"] for r in results], ["FOUND"] * 3)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM dp_live_requests").fetchone()[0], 1)

    def test_different_oems_are_serialized(self):
        first = self.enqueue()
        self.enqueue("Honda", "91303-PA9-004")
        self.assertEqual(bridge.claim_next(self.db)["request_id"], first["request_id"])
        self.assertIsNone(bridge.claim_next(self.db))
        self.finish(first["request_id"], result_status="NOT_FOUND")
        self.assertEqual(bridge.claim_next(self.db)["manufacturer"], "Honda")

    def test_rate_limit_between_finished_lookups(self):
        first = self.enqueue()
        self.enqueue("Honda", "91303-PA9-004")
        bridge.claim_next(self.db)
        self.finish(first["request_id"], result_status="NOT_FOUND")
        with patch.dict(os.environ, {"EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS": "5"}):
            self.assertIsNone(bridge.claim_next(self.db))
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE dp_live_requests SET claimed_at=? WHERE request_id=?", ((datetime.now(timezone.utc)-timedelta(seconds=6)).isoformat(), first["request_id"]))
            self.assertIsNotNone(bridge.claim_next(self.db))

    def test_expired_pending_never_reaches_agent(self):
        self.enqueue()
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE dp_live_requests SET requested_at=?", ((datetime.now(timezone.utc)-timedelta(seconds=60)).isoformat(),))
        self.assertIsNone(bridge.claim_next(self.db))

    def test_claim_not_reissued_after_client_timeout(self):
        first = self.enqueue()
        bridge.claim_next(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE dp_live_requests SET requested_at=?", ((datetime.now(timezone.utc)-timedelta(hours=1)).isoformat(),))
        self.assertEqual(self.enqueue()["request_id"], first["request_id"])
        self.assertIsNone(bridge.claim_next(self.db))

    def test_cloudflare_latches_even_if_health_flag_disabled(self):
        self._check_block("CLOUDFLARE")

    def test_auth_required_latches_until_confirmed_ready(self):
        self._check_block("AUTH_REQUIRED")

    def _check_block(self, status):
        first = self.enqueue()
        bridge.claim_next(self.db)
        self.enqueue("Honda", "91303-PA9-004")
        self.finish(first["request_id"], result_status=status)
        with patch.dict(os.environ, {"EXTREMIZER_DP_HEALTH_ENFORCE": "0"}):
            for _ in range(20):
                self.assertEqual(self.enqueue()["status"], status)
                self.assertIsNone(bridge.claim_next(self.db))
        health.set_health(self.db, "READY", checked_at=(datetime.now(timezone.utc)+timedelta(seconds=2)).isoformat())
        self.assertIsNotNone(bridge.claim_next(self.db))

    def test_days_of_blockage_do_not_auto_retry(self):
        first = self.enqueue()
        self.finish(first["request_id"], result_status="CLOUDFLARE")
        health.set_health(self.db, "CLOUDFLARE")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE dp_live_coordination SET blocked_at=?", ((datetime.now(timezone.utc)-timedelta(days=2)).isoformat(),))
        for _ in range(10):
            self.assertEqual(self.enqueue()["status"], "CLOUDFLARE")
            self.assertIsNone(bridge.claim_next(self.db))

    def test_old_ready_cannot_close_new_circuit(self):
        first = self.enqueue()
        self.finish(first["request_id"], result_status="CLOUDFLARE")
        self.assertEqual(self.enqueue()["status"], "CLOUDFLARE")

    def test_recent_verified_result_reused_without_new_live(self):
        first = self.enqueue()
        self.finish(first["request_id"], result_status="FOUND", dealer_price_usd=31.25, source=SOURCE)
        result = self.enqueue("arctic_cat")
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["request_id"], first["request_id"])
        self.assertIsNone(bridge.claim_next(self.db))

    def test_invalid_prices_and_msrp_never_found(self):
        for price, source in [(0, SOURCE), (-1, SOURCE), (float("nan"), SOURCE), (float("inf"), SOURCE), (True, SOURCE), (55, "public_msrp"), (55, SOURCE+":msrp")]:
            req = self.enqueue("Honda", "91303-PA9-004")
            self.finish(req["request_id"], result_status="FOUND", dealer_price_usd=price, source=source)
            with sqlite3.connect(self.db) as conn:
                row = conn.execute("SELECT result_status,dealer_price_usd FROM dp_live_requests WHERE request_id=?", (req["request_id"],)).fetchone()
            self.assertEqual(row, ("NO_PRICE", None))

    def test_error_payload_cannot_leak_price(self):
        req = self.enqueue()
        self.finish(req["request_id"], result_status="CLOUDFLARE", dealer_price_usd=99, source=SOURCE)
        with sqlite3.connect(self.db) as conn:
            self.assertIsNone(conn.execute("SELECT dealer_price_usd FROM dp_live_requests").fetchone()[0])

    def test_late_result_cannot_latch_completed_request(self):
        req = self.enqueue()
        self.finish(req["request_id"], result_status="NOT_FOUND")
        self.assertFalse(self.finish(req["request_id"], result_status="CLOUDFLARE"))
        self.assertEqual(self.enqueue()["status"], "TIMEOUT")


class BrowserTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "status.json"
        self.path.write_text(json.dumps({"dcp_status":"READY", "dcp_checked_at":health._now(), "status":"online"}))
        self.producer = functions("oemixibot_dcp_price.py", {"DealerPriceResult", "_is_dcp_page", "_money", "get_dealer_price"}, {
            "dataclass":dataclass, "Any":Any, "math":math, "re":re, "json":json,
            "urlsplit":urlsplit, "parse_qs":parse_qs, "dp_live_health":health,
            "DCP_HOST":"www.dealercostparts.com", "LOCAL_HEALTH_FILE":self.path,
            "_parts_slug":lambda m:("Arctic Cat", "arctic_cat"), "_normalize_oem":lambda o:o,
            "_tabs":Mock(return_value=[]), "_pick_authorized_dcp_tab":Mock(return_value={"id":"mock"}),
            "_runtime_evaluate":Mock(return_value={"http_status":403,"challenged":True,"authorized":False}),
        })

    def tearDown(self): self.tmp.cleanup()

    def test_background_cloudflare_stops_without_navigation(self):
        r = self.producer["get_dealer_price"]("Arctic Cat", "2670-265")
        self.assertEqual(r.status, "CLOUDFLARE")
        self.assertEqual(self.producer["_runtime_evaluate"].call_count, 1)
        expression = self.producer["_runtime_evaluate"].call_args.args[1]
        self.assertIn("fetch(", expression)
        self.assertNotRegex(expression, r"location\.(href|assign|replace)|window\.open|Page\.navigate")

    def test_blocked_health_never_touches_browser(self):
        for status in ("CLOUDFLARE", "AUTH_REQUIRED"):
            self.path.write_text(json.dumps({"dcp_status":status,"dcp_checked_at":health._now()}))
            self.assertEqual(self.producer["get_dealer_price"]("Arctic Cat", "2670-265").status, status)
        self.producer["_tabs"].assert_not_called()
        self.producer["_runtime_evaluate"].assert_not_called()

    def test_missing_health_never_touches_browser(self):
        self.path.unlink()
        self.assertEqual(self.producer["get_dealer_price"]("Arctic Cat", "2670-265").status, "TECHNICAL_ERROR")
        self.producer["_runtime_evaluate"].assert_not_called()

    def test_exact_open_dom_avoids_background_fetch(self):
        self.producer["_tabs"].return_value = [{"type":"page","url":"https://www.dealercostparts.com/oemparts/partsearch/arctic_cat?partsearch=2670-265"}]
        self.producer["_runtime_evaluate"].return_value = {"authorized":True,"found":True,"dealer_price_text":"$31.25","matched_sku":"2670-265"}
        r = self.producer["get_dealer_price"]("Arctic Cat", "2670-265")
        self.assertEqual(r.status,"FOUND")
        self.assertNotIn("fetch(",self.producer["_runtime_evaluate"].call_args.args[1])
        self.producer["_pick_authorized_dcp_tab"].assert_not_called()

    def test_unauthorized_open_dom_stops_without_fetch(self):
        self.producer["_tabs"].return_value=[{"type":"page","url":"https://www.dealercostparts.com/oemparts/partsearch/arctic_cat?partsearch=2670-265"}]
        self.producer["_runtime_evaluate"].return_value={"authorized":False,"found":False}
        self.assertEqual(self.producer["get_dealer_price"]("Arctic Cat","2670-265").status,"AUTH_REQUIRED")
        self.assertEqual(self.producer["_runtime_evaluate"].call_count,1)
        self.producer["_pick_authorized_dcp_tab"].assert_not_called()

    def test_accessories_auth_loss_stops_auto_discovery(self):
        self.producer["_parts_slug"]=lambda m:("Ski-Doo","ski_doo")
        self.producer["_runtime_evaluate"].side_effect=[{"authorized":True,"found":False}, {"authorized":False,"found":False}]
        self.assertEqual(self.producer["get_dealer_price"]("Ski-Doo","779132").status,"AUTH_REQUIRED")
        self.assertEqual(self.producer["_runtime_evaluate"].call_count,2)
        expression=self.producer["_runtime_evaluate"].call_args.args[1]
        self.assertLess(expression.index("if(!sa)"),expression.index("fetch(links[0]"))

    def test_host_spoof_and_oem_prefix_do_not_match_open_dom(self):
        self.producer["_tabs"].return_value = [
            {"type":"page","url":"https://wrong.example/oemparts/partsearch/arctic_cat?partsearch=2670-265&host=www.dealercostparts.com"},
            {"type":"page","url":"https://www.dealercostparts.com/oemparts/partsearch/arctic_cat?partsearch=2670-2650"},
        ]
        self.producer["get_dealer_price"]("Arctic Cat", "2670-265")
        self.producer["_pick_authorized_dcp_tab"].assert_called_once()
        self.assertEqual(self.producer["_runtime_evaluate"].call_count,1)

    def test_zero_negative_price_not_found(self):
        for price in ("$0.00", "-$3.00"):
            self.producer["_runtime_evaluate"].return_value={"authorized":True,"found":True,"dealer_price_text":price}
            self.assertEqual(self.producer["get_dealer_price"]("Arctic Cat","2670-265").status,"NO_PRICE")

    def test_unrelated_cloudflare_does_not_poison_health(self):
        helper = SimpleNamespace(_is_dcp_page=self.producer["_is_dcp_page"], _tabs=Mock(return_value=[
            {"type":"page","url":"https://www.dealerorders.com/product","title":"Just a moment Cloudflare"},
            {"type":"page","url":"https://www.dealercostparts.com/oemparts/partsearch/arctic_cat","title":"Parts"},
        ]), _runtime_evaluate=Mock(return_value={"authorized":True,"challenged":False}))
        observations=[]
        ns = functions("dp_sync_agent.py", {"_preflight_dcp"}, {"time":time,"oemixibot_dcp_price":helper,"_CF_MARKERS":("cloudflare","just a moment"),"_DCP_STATUS":"UNKNOWN"})
        def observe(status, **kwargs):
            observations.append(status); ns["_DCP_STATUS"]=status
        ns["_set_dcp_health"]=observe
        self.assertEqual(ns["_preflight_dcp"](),"READY")
        self.assertEqual(observations,["READY"])
        expression=helper._runtime_evaluate.call_args.args[1]
        self.assertNotIn("fetch(",expression)
        self.assertNotIn("location.href=",expression)

    def test_block_before_failed_result_delivery(self):
        order=[]
        ns=functions("dp_sync_agent.py", {"run_once"}, {
            "_claim":lambda:{"request_id":"mock","manufacturer":"Arctic Cat","oem":"2670-265"},
            "oemixibot_dcp_price":SimpleNamespace(get_dealer_price_dict=lambda *a:{"status":"CLOUDFLARE"}),
            "_set_dcp_health":lambda *a,**k:order.append("blocked"),
            "_write_status":Mock(),
        })
        def fail(*a):order.append("finish");raise OSError("mock result delivery failed")
        ns["_finish"]=fail
        with self.assertRaises(OSError):ns["run_once"]()
        self.assertEqual(order,["blocked","finish"])

    def test_auto_search_spacing_and_stop_on_challenge(self):
        sleep=Mock()
        lookup=Mock(side_effect=[{"status":"NOT_FOUND"},{"status":"CLOUDFLARE"}])
        ns=functions("dp_sync_agent.py", {"_auto_identity_price"}, {
            "finder":SimpleNamespace(CATALOGS=[SimpleNamespace(kind="parts",manufacturer=x) for x in ("Arctic Cat","Honda","Yamaha")]),
            "oemixibot_dcp_price":SimpleNamespace(get_dealer_price_dict=lookup),
            "time":SimpleNamespace(sleep=sleep), "os":os, "math":math,
        })
        with patch.dict(os.environ,{"DP_SYNC_LOOKUP_GAP_SECONDS":"5"}):
            self.assertEqual(ns["_auto_identity_price"]("2670-265")["status"],"CLOUDFLARE")
        self.assertEqual(lookup.call_count,2)
        sleep.assert_called_once_with(5)


class CacheContractTests(unittest.TestCase):
    def test_procenka_fresh_cache_never_calls_live_or_msrp(self):
        live=Mock(side_effect=AssertionError("live lookup on cache hit"))
        ns=functions("extremizer_bot.py", {"enrich_found_result_with_dealer_price"}, {
            "datetime":datetime,"DCP_CACHE_ONLY":True,"get_dealer_price_cache":lambda *a,**k:{"fresh":True,"dealer_price_usd":31.25,"last_verified_at":health._now()},
            "dp_live_bridge":SimpleNamespace(request_live_dp=live),
        })
        r=asyncio.run(ns["enrich_found_result_with_dealer_price"]({"status":"FOUND","catalog":"Parts","manufacturer":"Arctic Cat","oem":"2670-265","msrp_usd":99}))
        self.assertEqual(r["_dealer_price_usd"],31.25)
        live.assert_not_called()

    def test_probnik_fresh_cache_never_calls_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            db=Path(tmp)/"mock.db"
            with sqlite3.connect(db) as conn:
                conn.execute("CREATE TABLE dealer_price_cache(manufacturer,oem,dealer_price_usd,last_verified_at)")
                conn.execute("INSERT INTO dealer_price_cache VALUES(?,?,?,?)",("Arctic Cat","2670-265",31.25,health._now()))
            live=Mock(side_effect=AssertionError("live lookup on cache hit"))
            ns=functions("probnik_app.py", {"_identity_and_price"}, {
                "ORDERS_DB":db,"sqlite3":sqlite3,"DP_MAX_AGE_HOURS":168,
                "finder":SimpleNamespace(manufacturer_alias=lambda x:x), "normalize_oem":lambda x:x,
                "_db_tables":lambda c:{r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")},
                "_customer_price_rub":lambda p:4300,"dp_live_bridge":SimpleNamespace(request_live_dp=live),
            })
            r=ns["_identity_and_price"]("2670-265")
            self.assertEqual(r["dp_usd"],31.25)
            live.assert_not_called()


if __name__ == "__main__": unittest.main()
