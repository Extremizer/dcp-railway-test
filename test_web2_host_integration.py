"""Real WEB1/WEB2 host and API handlers, synthetic SQLite, no live services.

Run in its own process: python -B -m unittest -v test_web2_host_integration.py
Catalog/DP are seeded via real core helpers; routes/pricing/handoff are not mocked.
"""
import importlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import tempfile
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


if __name__ == "__main__":
    unittest.main()
