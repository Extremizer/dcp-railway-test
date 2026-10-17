import json
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

import dcp_category_authority
import dcp_price_gateway
import dp_live_bridge as bridge
import dp_live_health as health
import dp_sync_agent
import extremizer_bot as core
import probnik_app as probnik
import unified_price_contract_v1 as unified
from dcp_admission_test_support import approve_mode
from oemixi_admin_polaris_browser_price import producer_browser_price
from test_dp_result_completion import build_http, headers, OWNER, PATH


class FakePolarisAdapter:
    def verify(self):
        return True

    def snapshot(self):
        return {
            "lines": [{
                "sku": "2206328",
                "token": "existing2206328",
                "qty": 1,
                "unit_price": "184.07",
                "subtotal": "184.07",
                "currency": "USD",
            }]
        }

    def find_exact_oem(self, oem):
        return {
            "manufacturer": "Polaris",
            "item_category": "Parts",
            "requested_oem": oem,
            "resolved_oem": "2206328",
            "exact_match": True,
            "replacement_proven": True,
        }

    def price(self, oem, snapshot):
        if oem != "2206328":
            raise AssertionError("resolved SKU required")
        return "184.07"

    def add_one(self, oem):
        raise AssertionError("existing resolved cart line must avoid mutation")

    def remove_line(self, token):
        raise AssertionError("no cleanup mutation expected")

    def decrement_one(self, token):
        raise AssertionError("no cleanup mutation expected")


class PolarisDealerPriceE2EIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "orders.db"
        self.snapshot = Path(__file__).with_name("dcp_public_msrp_cache.json")

        self.old_core_db = core.ORDERS_DB_FILE
        self.old_core_snapshot = core.PUBLIC_MSRP_CACHE_FILE
        self.old_core_ttl = core.DP_CACHE_MAX_AGE_HOURS
        self.old_probnik_db = probnik.ORDERS_DB
        self.old_probnik_ttl = probnik.DP_MAX_AGE_HOURS
        self.old_rate = probnik.USD_RUB_RATE
        self.old_coef = probnik.PRICE_COEFFICIENT

        core.ORDERS_DB_FILE = self.db
        core.PUBLIC_MSRP_CACHE_FILE = self.snapshot
        core.DP_CACHE_MAX_AGE_HOURS = 24.0
        probnik.ORDERS_DB = self.db
        probnik.DP_MAX_AGE_HOURS = 24.0
        probnik.USD_RUB_RATE = 100.0
        probnik.PRICE_COEFFICIENT = 1.34
        self.addCleanup(self._restore_globals)

        core.init_orders_db()
        bridge.init(self.db)
        health.set_health(self.db, "READY")
        approve_mode(self.db, "OPEN")

        self.env = patch.dict(os.environ, {
            "EXTREMIZER_DP_HEALTH_ENFORCE": "1",
            "EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS": "0",
        })
        self.env.start()
        self.addCleanup(self.env.stop)

        self.client = build_http(self.db)
        self.addCleanup(self.client.close)

    def _restore_globals(self):
        core.ORDERS_DB_FILE = self.old_core_db
        core.PUBLIC_MSRP_CACHE_FILE = self.old_core_snapshot
        core.DP_CACHE_MAX_AGE_HOURS = self.old_core_ttl
        probnik.ORDERS_DB = self.old_probnik_db
        probnik.DP_MAX_AGE_HOURS = self.old_probnik_ttl
        probnik.USD_RUB_RATE = self.old_rate
        probnik.PRICE_COEFFICIENT = self.old_coef

    def claim(self):
        queued = bridge.request_live_dp(
            self.db, "Polaris", "2205250", wait_seconds=0
        )
        self.assertIn(queued["status"], {"TIMEOUT", "PENDING"})
        response = self.client.get(
            "/internal/dp-sync/next",
            headers=headers("GET", "/internal/dp-sync/next"),
        )
        self.assertEqual(response.status_code, 200, response.text)
        claim = response.json()["request"]
        self.assertIsNotNone(claim)
        self.assertEqual(claim["manufacturer"], "Polaris")
        self.assertEqual(claim["oem"], "2205250")
        self.assertEqual(claim["item_category"], "Parts")
        return claim

    def signed_post(self, payload):
        body = json.dumps(payload, separators=(",", ":")).encode()
        return self.client.post(
            PATH,
            content=body,
            headers=headers("POST", PATH, body),
        )

    def test_01_alias_producer_protocol_fence_completion_cache_pricesource_consumers(self):
        with sqlite3.connect(self.db) as conn:
            authority = dcp_category_authority.resolve(conn, "Polaris", "2205250")
        self.assertEqual(authority["resolved_oem"], "2206328")
        self.assertEqual(authority["item_category"], "Parts")

        claim = self.claim()
        raw = producer_browser_price(
            "2205250",
            category_evidence=claim,
            adapter=FakePolarisAdapter(),
            dealer_identity_proof_ref="sha256:isolated-dealer-proof",
        )
        self.assertEqual(raw["status"], "FOUND")
        self.assertEqual(raw["dealer_price_usd"], 184.07)
        self.assertEqual(raw["current_oem"], "2206328")
        self.assertTrue(raw["dealer_cart_proof"]["cart_restored"])
        self.assertFalse(raw["dealer_cart_proof"]["production_found"])

        validated = dp_sync_agent._validated_result(claim, raw)
        self.assertEqual(validated["status"], "FOUND")
        self.assertEqual(validated["dealer_price_usd"], 184.07)
        self.assertEqual(validated["matched_sku"], "2206328")
        self.assertIn("dealer_cart_proof", validated)

        payload = {
            **validated,
            "request_id": claim["request_id"],
            "protocol_version": 2,
            "producer_id": OWNER,
            "claim_token": claim["claim_token"],
            "claim_generation": claim["claim_generation"],
        }
        response = self.signed_post(payload)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "FOUND")
        self.assertTrue(response.json()["server_cache_committed"])

        with sqlite3.connect(self.db) as conn:
            rows = conn.execute(
                """SELECT oem,dealer_price_usd,source
                     FROM dealer_price_cache
                    WHERE manufacturer='Polaris'
                    ORDER BY oem"""
            ).fetchall()
            request = conn.execute(
                """SELECT status,result_status,current_oem,result_manufacturer,
                          claim_owner,item_category,category_source_ref,cache_committed_at
                     FROM dp_live_requests WHERE request_id=?""",
                (claim["request_id"],),
            ).fetchone()
        self.assertEqual([r[0] for r in rows], ["2205250", "2206328"])
        self.assertEqual([r[1] for r in rows], [184.07, 184.07])
        for _, _, source in rows:
            self.assertIn("requested_oem=2205250", source)
            self.assertIn("resolved_oem=2206328", source)
            self.assertIn("request_id=" + claim["request_id"], source)
            self.assertIn("dealer_identity_ref=sha256:isolated-dealer-proof", source)
            self.assertIn("category_ref=", source)
        self.assertEqual(request[0:4], ("finished", "FOUND", "2206328", "Polaris"))
        self.assertEqual(request[4], OWNER)
        self.assertEqual(request[5], "Parts")
        self.assertTrue(request[6])
        self.assertTrue(request[7])

        gateway = dcp_price_gateway.request_price(
            self.db, "Polaris", "2205250",
            max_age_hours=24.0,
            live_request=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("fresh cache must win")
            ),
        )
        self.assertEqual(gateway["status"], "FOUND")
        self.assertEqual(gateway["dealer_price_usd"], 184.07)

        adapter = dcp_price_gateway.SharedDealerPriceAdapter(
            lambda m, o, *, max_age_hours: dcp_price_gateway.request_price(
                self.db, m, o, max_age_hours=max_age_hours,
                live_request=lambda *a, **k: {"status": "TECHNICAL_ERROR"},
            ),
            max_age_hours=24.0,
        )
        dealer = adapter.get_dealer_price_dict("Polaris", "2206328")
        self.assertEqual(dealer["status"], "FOUND")
        self.assertEqual(dealer["dealer_price_usd"], 184.07)

        client = core.get_dealer_price_cache("Polaris", "2205250")
        self.assertTrue(client["fresh"])
        self.assertEqual(client["dealer_price_usd"], 184.07)

        trial = probnik._identity_and_price("2205250")
        self.assertEqual(trial["manufacturer"], "Polaris")
        self.assertEqual(trial["current_oem"], "2206328")
        self.assertEqual(trial["dp_usd"], 184.07)
        self.assertTrue(trial["price_fresh"])
        self.assertIsNotNone(trial["customer_rub"])

        duplicate = self.signed_post(payload)
        self.assertEqual(duplicate.status_code, 409)

    def test_02_bad_cart_evidence_never_writes(self):
        claim = self.claim()
        raw = producer_browser_price(
            "2205250",
            category_evidence=claim,
            adapter=FakePolarisAdapter(),
            dealer_identity_proof_ref="sha256:isolated-dealer-proof",
        )
        raw["dealer_cart_proof"]["cart_restored"] = False
        validated = dp_sync_agent._validated_result(claim, raw)
        self.assertEqual(validated["status"], "TECHNICAL_ERROR")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM dealer_price_cache WHERE manufacturer='Polaris'"
                ).fetchone()[0],
                0,
            )

    def test_03_ttl_23_24_25_and_no_timestamp_refresh_on_read(self):
        now = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
        self.assertEqual(
            unified._fresh((now - timedelta(hours=23)).isoformat(),
                           max_age_hours=24, now=now),
            (True, "VERIFIED"),
        )
        self.assertEqual(
            unified._fresh((now - timedelta(hours=24)).isoformat(),
                           max_age_hours=24, now=now),
            (False, "STALE"),
        )
        self.assertEqual(
            unified._fresh((now - timedelta(hours=25)).isoformat(),
                           max_age_hours=24, now=now),
            (False, "STALE"),
        )

        stamp = (datetime.now(timezone.utc) - timedelta(hours=23)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO dealer_price_cache(
                    manufacturer,oem,dealer_price_usd,source,
                    first_seen_at,last_verified_at,last_used_at,use_count
                ) VALUES(?,?,?,?,?,?,NULL,0)""",
                ("Polaris", "2206328", 184.07,
                 "verified_local_dcp:dealer_cart:authenticated_exact_line",
                 stamp, stamp),
            )
        before = core.get_dealer_price_cache("Polaris", "2206328")["last_verified_at"]
        after = core.get_dealer_price_cache("Polaris", "2206328")["last_verified_at"]
        self.assertEqual(before, after)

    def test_04_stale_or_failed_refresh_never_promotes_msrp_to_dealer(self):
        stale = (datetime.now(timezone.utc) - timedelta(hours=25)).isoformat()
        with sqlite3.connect(self.db) as conn:
            conn.execute(
                """INSERT INTO dealer_price_cache(
                    manufacturer,oem,dealer_price_usd,source,
                    first_seen_at,last_verified_at,last_used_at,use_count
                ) VALUES(?,?,?,?,?,?,NULL,0)""",
                ("Polaris", "2206328", 184.07,
                 "verified_local_dcp:dealer_cart:authenticated_exact_line",
                 stale, stale),
            )
        self.assertFalse(core.get_dealer_price_cache("Polaris", "2206328")["fresh"])
        trial = probnik._identity_and_price("2205250")
        self.assertEqual(trial["msrp_usd"], 239.99)
        self.assertIsNone(trial["dp_usd"])
        self.assertFalse(trial["price_fresh"])

        adapter = dcp_price_gateway.SharedDealerPriceAdapter(
            lambda m, o, *, max_age_hours: {
                "status": "NO_PRICE", "msrp_usd": 239.99
            },
            max_age_hours=24.0,
        )
        result = adapter.get_dealer_price_dict("Polaris", "2206328")
        self.assertEqual(result["status"], "NO_PRICE")
        self.assertNotIn("dealer_price_usd", result)
        self.assertNotIn("msrp_usd", result)


if __name__ == "__main__":
    unittest.main()
