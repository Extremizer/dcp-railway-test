"""Real WEB1/WEB2 host and API handlers, synthetic SQLite, no live services.

Run in its own process: python -B -m unittest -v test_web2_host_integration.py
Catalog/DP are seeded via real core helpers; routes/pricing/handoff are not mocked.
"""
import importlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class Web2HostIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix="web2-host-")
        cls.addClassCleanup(cls.tmp.cleanup)
        root = Path(cls.tmp.name)
        cls.db = root / "orders.db"
        env = {
            "EXTREMIZER_ORDERS_DB_FILE": str(cls.db),
            "EXTREMIZER_OEM_REFERENCE_DB_FILE": str(root / "reference.db"),
            "EXTREMIZER_DATA_DIR": str(root),
            "RAILWAY_VOLUME_MOUNT_PATH": str(root),
            "OEM_IMPORT_TMP_DIR": str(root / "tmp"),
            "EXTREMIZER_USD_RUB_RATE": "100",
            "EXTREMIZER_PRICE_COEFFICIENT": "1",
        }
        cls.env_patch = patch.dict(os.environ, env, clear=True)
        cls.env_patch.start()
        cls.addClassCleanup(cls.env_patch.stop)
        cls.network_patch = patch.object(socket.socket, "connect",
                                         side_effect=AssertionError("external network forbidden"))
        cls.network_patch.start()
        cls.addClassCleanup(cls.network_patch.stop)
        cls.host = importlib.import_module("web_app")
        cls.app = importlib.import_module("web2_runtime_app").app
        # Eliminate optional local bootstrap/config files from this fixture.
        core = cls.host.core
        core.PUBLIC_MSRP_CACHE_FILE = root / "absent.json"
        core.RATE_FILE = root / "absent-rate.txt"
        core.PRICE_COEFFICIENT_FILE = root / "absent-coefficient.txt"
        cls.client_context = TestClient(cls.app)
        cls.client = cls.client_context.__enter__()
        cls.addClassCleanup(cls.client_context.__exit__, None, None, None)
        for oem, msrp in (("417300574", 350), ("417300575", 250)):
            assert core.upsert_oem_catalog_cache({
                "status": "FOUND", "manufacturer": "Ski-Doo", "item_sku": oem,
                "query_oem": oem, "name": "Synthetic fixture", "catalog": "Parts",
            }, msrp, source_kind="isolated-fixture")
            assert core.upsert_dealer_price_cache("Ski-Doo", oem, 292, "isolated-fixture")

    def test_same_host_keeps_web1_health_admin_and_web2_assets(self):
        self.assertIs(self.app, self.host.app)
        for path in ("/", "/health", "/admin/login", "/web2", "/web2-static/app.js"):
            self.assertEqual(self.client.get(path).status_code, 200, path)
        self.assertTrue(self.client.get("/health").json()["ok"])

    def test_real_oem_pricing_and_stock_api(self):
        response = self.client.get("/api/v1/oem/417300574")
        self.assertEqual(response.status_code, 200)
        card = response.json()
        self.assertEqual(card["price"]["customer_rub"], 29200)
        self.assertEqual(card["price"]["msrp_rub"], 35000)
        self.assertEqual(card["price"]["benefit_pct"], 16.6)
        stock = self.client.get("/api/v1/oem/417300574/stock").json()
        self.assertEqual(stock["offers"][0]["source"], "usa")
        self.assertEqual(stock["offers"][0]["price_rub"], 29200)
        lower = self.client.get("/api/v1/oem/417300575").json()
        self.assertIsNone(lower["price"]["msrp_rub"])
        self.assertIsNone(lower["price"]["benefit_pct"])

    def test_real_handoff_persists_verified_price_without_order_charge(self):
        response = self.client.post("/api/v1/handoff", json={"items": [{
            "manufacturer": "Ski-Doo", "oem": "417300574", "qty": 2,
            "offer_source": "usa", "price_snapshot_rub": 1,
        }]})
        self.assertEqual(response.status_code, 200)
        url = response.json()["telegram_url"]
        token = url.split("?start=web_", 1)[1]
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT payload_json FROM web_cart_handoffs WHERE token=?",
                               (token,)).fetchone()
            payload = json.loads(row[0])
            items = payload["items"] if isinstance(payload, dict) else payload
            self.assertEqual(items[0]["price_snapshot_rub"], 29200)
            self.assertEqual(items[0]["qty"], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 0)

    def _dp_result(self, oem, *, msrp=50.95, source="dcp_public_form_metadata", signed=True):
        self.host.dp_live_bridge.init(self.db)
        request_id = "metadata-" + oem
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO dp_live_requests(request_id,manufacturer,oem,status,requested_at) "
                      "VALUES(?,?,?,'claimed',?)", (request_id, "Arctic Cat", oem,
                      self.host.dp_live_bridge._now()))
        payload = {"request_id": request_id, "status": "FOUND", "dealer_price_usd": 30.05,
                   "current_oem": oem, "name": "Synthetic metadata fixture",
                   "source": "server_html:exact_row", "msrp_usd": msrp, "msrp_source": source}
        path = "/internal/dp-sync/result"
        timestamp = str(int(time.time()))
        key = hashlib.sha256(b"extremizer-dp-sync-v1\0synthetic-metadata-token").digest()
        signature = hmac.new(key, f"v1\n{path}\n{timestamp}".encode(), hashlib.sha256).hexdigest()
        headers = {"X-DP-Sync-Ts": timestamp, "X-DP-Sync-Sig": signature} if signed else {}
        with patch.dict(os.environ, {"OEMIXIBOT_TOKEN": "synthetic-metadata-token"}):
            response = self.client.post(path, json=payload, headers=headers)
        return response, payload, headers

    def test_signed_dp_result_persists_explicit_msrp_without_business_writes(self):
        from client_finance_schema import ensure_client_finance_schema
        ensure_client_finance_schema(self.db)
        tables = ("orders", "order_items", "finance_ledger", "warehouse_stock_reservations")
        def business_rows():
            with sqlite3.connect(self.db) as c:
                return {t: c.execute(f'SELECT * FROM "{t}" ORDER BY rowid').fetchall() for t in tables}
        before = business_rows()
        response, _, _ = self._dp_result("2670-METADATA")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(business_rows(), before)
        with sqlite3.connect(self.db) as c:
            row = c.execute("SELECT msrp_usd,msrp_verified,source_kind FROM oem_catalog_cache "
                            "WHERE manufacturer='Arctic Cat' AND current_oem='2670-METADATA'").fetchone()
            self.assertEqual(row, (50.95, 1, "verified_local_dcp_public_msrp"))
            self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache WHERE "
                                      "manufacturer='Arctic Cat' AND oem='2670-METADATA'").fetchone()[0], 30.05)

    def test_missing_and_untrusted_msrp_preserve_successful_dp(self):
        for oem, msrp, source in (("2670-MISSING", None, None),
                                  ("2670-PRIVATE", 30.05, "server_html:.c2 .dbl")):
            with self.subTest(oem=oem):
                response, _, _ = self._dp_result(oem, msrp=msrp, source=source)
                self.assertEqual(response.status_code, 200, response.text)
                with sqlite3.connect(self.db) as c:
                    self.assertEqual(c.execute("SELECT COUNT(*) FROM oem_catalog_cache "
                                               "WHERE current_oem=?", (oem,)).fetchone()[0], 0)
                    self.assertEqual(c.execute("SELECT dealer_price_usd FROM dealer_price_cache "
                                               "WHERE oem=?", (oem,)).fetchone()[0], 30.05)

    def test_unsigned_metadata_result_cannot_write_cache(self):
        response, _, _ = self._dp_result("2670-UNSIGNED", signed=False)
        self.assertEqual(response.status_code, 401)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM oem_catalog_cache "
                                       "WHERE current_oem='2670-UNSIGNED'").fetchone()[0], 0)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM dealer_price_cache "
                                       "WHERE oem='2670-UNSIGNED'").fetchone()[0], 0)

    def test_finished_request_replay_cannot_replace_verified_msrp(self):
        response, payload, headers = self._dp_result("2670-REPLAY")
        self.assertEqual(response.status_code, 200, response.text)
        payload["msrp_usd"] = 999
        with patch.dict(os.environ, {"OEMIXIBOT_TOKEN": "synthetic-metadata-token"}):
            replay = self.client.post("/internal/dp-sync/result", json=payload, headers=headers)
        self.assertEqual(replay.status_code, 409)
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT msrp_usd FROM oem_catalog_cache "
                                       "WHERE current_oem='2670-REPLAY'").fetchone()[0], 50.95)


if __name__ == "__main__":
    unittest.main()
