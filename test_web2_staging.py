"""Staging isolation and mocked API tests."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from web2_staging_app import app

client = TestClient(app)


class Web2StagingTests(unittest.TestCase):
    def test_staging_health(self):
        self.assertEqual(client.get("/staging-health").json()["mode"], "mock")

    def test_oem_and_stock(self):
        card = client.get("/api/v1/oem/417300574").json()
        self.assertEqual(card["price"]["customer_rub"], 29200)
        self.assertEqual(card["stock"]["warehouses"][0]["warehouse"], "Склад 1")
        self.assertEqual(client.get("/api/v1/oem/417300574/stock").status_code, 200)

    def test_msrp_and_warehouse_only(self):
        self.assertIsNone(client.get("/api/v1/oem/NO-MSRP").json()["price"]["msrp_rub"])
        self.assertTrue(client.get("/api/v1/oem/WAREHOUSE-ONLY").json()["warehouse_only"])

    def test_unknown_oem_is_not_fetched_live(self):
        self.assertEqual(client.get("/api/v1/oem/unknown").status_code, 404)

    def test_handoff_is_mock_only(self):
        response = client.post("/api/v1/handoff", json={"items": [{
            "manufacturer": "Ski-Doo", "oem": "417300574",
            "offer_source": "warehouse", "warehouse_id": 1, "qty": 2,
        }]})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["telegram_url"].startswith("/web2?"))
        self.assertEqual(client.get("/staging-health").json()["orders_created"], False)

    def test_handoff_rejects_invalid_stock(self):
        response = client.post("/api/v1/handoff", json={"items": [{
            "manufacturer": "Ski-Doo", "oem": "417300574",
            "offer_source": "warehouse", "warehouse_id": 1, "qty": 27,
        }]})
        self.assertEqual(response.status_code, 400)


    def test_powersports_logo_hook_is_inactive_without_exact_theme7_asset(self):
        with patch.dict(os.environ, {
            "EXTREMIZER_POWERSPORTS_LOGO_PATH": "",
            "EXTREMIZER_POWERSPORTS_LOGO_SHA256": "",
        }, clear=False):
            self.assertEqual(client.get("/web2-powersports-logo").status_code, 404)

    def test_powersports_logo_rejects_wrong_sha_and_serves_exact_png(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "theme7-horizontal.png"
            data = b"\x89PNG\r\n\x1a\nTHEME7-ISOLATED-EXACT-ASSET"
            path.write_bytes(data)
            digest = hashlib.sha256(data).hexdigest()

            with patch.dict(os.environ, {
                "EXTREMIZER_POWERSPORTS_LOGO_PATH": str(path),
                "EXTREMIZER_POWERSPORTS_LOGO_SHA256": "0" * 64,
            }, clear=False):
                self.assertEqual(client.get("/web2-powersports-logo").status_code, 404)

            with patch.dict(os.environ, {
                "EXTREMIZER_POWERSPORTS_LOGO_PATH": str(path),
                "EXTREMIZER_POWERSPORTS_LOGO_SHA256": digest,
            }, clear=False):
                response = client.get("/web2-powersports-logo")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["content-type"], "image/png")
                self.assertEqual(response.content, data)


if __name__ == "__main__":
    unittest.main()
