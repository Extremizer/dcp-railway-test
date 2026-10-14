from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

# Force every import-time DB initializer into a disposable directory before
# importing the actual WEB1 app.
_TMP = tempfile.TemporaryDirectory()
os.environ["EXTREMIZER_ORDERS_DB_FILE"] = str(Path(_TMP.name) / "orders.db")
os.environ["EXTREMIZER_DCP_MODE"] = "cache_only"
os.environ["EXTREMIZER_TELEGRAM_BOT_USERNAME"] = "Extremizer_bot"

from fastapi.testclient import TestClient  # noqa: E402
import web_app  # noqa: E402
import web2_runtime_app  # noqa: E402


def _real_api_fixture() -> dict:
    return {
        "manufacturer": "Ski-Doo",
        "oem": "417300574",
        "requested_oem": "417300574",
        "name": "Isolated WEB1 API fixture",
        "catalog": "isolated",
        "previous_oems": [],
        "warehouse_only": False,
        "price": {
            "available": True,
            "customer_rub": 29200,
            "msrp_rub": 35000,
            "benefit_pct": 16.6,
        },
        "offers": [
            {
                "key": "usa",
                "source": "usa",
                "warehouse_id": None,
                "label": "США",
                "price_rub": 29200,
                "available_quantity": None,
                "can_add": True,
            },
            {
                "key": "warehouse:1",
                "source": "warehouse",
                "warehouse_id": 1,
                "label": "Склад 1",
                "price_rub": 34700,
                "available_quantity": 26,
                "can_add": True,
            },
        ],
        "stock": {
            "known": True,
            "refreshing": False,
            "warehouses": [
                {
                    "warehouse_id": 1,
                    "warehouse": "Склад 1",
                    "quantity": 26,
                    "price_rub": 34700,
                }
            ],
        },
        "weight": None,
        "delivery_notice": "* - в цену не входит стоимость доставки из штатов 🚚",
    }


class Web2RealWeb1ApiWiringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(web2_runtime_app.app)

    def test_disposable_db_is_used_not_repository_or_production_db(self):
        db_path = Path(web_app.core.ORDERS_DB_FILE).resolve()
        self.assertTrue(str(db_path).startswith(str(Path(_TMP.name).resolve())))

    def test_web2_and_real_web1_routes_share_one_fastapi_app(self):
        self.assertIs(web2_runtime_app.app, web_app.app)
        paths = {getattr(route, "path", None) for route in web2_runtime_app.app.routes}
        self.assertIn("/web2", paths)
        self.assertIn("/api/v1/oem/{oem}", paths)
        self.assertIn("/api/v1/oem/{oem}/stock", paths)
        self.assertIn("/api/v1/handoff", paths)

    def test_real_web1_oem_route_contract_reaches_web2_unchanged(self):
        with patch.object(web_app, "build_oem_card", side_effect=lambda *a, **k: _real_api_fixture()), \
             patch.object(web_app, "_stock_refresh_needed", return_value=False):
            response = self.client.get("/api/v1/oem/417300574")
        self.assertEqual(response.status_code, 200)
        card = response.json()
        self.assertEqual(card["price"]["customer_rub"], 29200)
        self.assertEqual(card["price"]["benefit_pct"], 16.6)
        self.assertEqual([x["source"] for x in card["offers"]], ["usa", "warehouse"])

    def test_real_web1_stock_route_contract(self):
        with patch.object(web_app, "build_oem_card", side_effect=lambda *a, **k: _real_api_fixture()), \
             patch.object(web_app, "_stock_refresh_needed", return_value=False), \
             patch.object(web_app, "_stock_refreshing", return_value=False):
            response = self.client.get("/api/v1/oem/417300574/stock")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["warehouses"][0]["warehouse"], "Склад 1")
        self.assertEqual(len(data["offers"]), 2)

    def test_real_web1_handoff_route_revalidates_offer_before_token(self):
        captured = {}

        def fake_handoff(items, *, db_file):
            captured["items"] = items
            captured["db_file"] = str(db_file)
            return "isolated-token"

        with patch.object(web_app, "build_oem_card", side_effect=lambda *a, **k: _real_api_fixture()), \
             patch.object(web_app.web_handoff, "create_handoff", side_effect=fake_handoff):
            response = self.client.post(
                "/api/v1/handoff",
                json={
                    "items": [
                        {
                            "manufacturer": "Ski-Doo",
                            "oem": "417300574",
                            "requested_oem": "417300574",
                            "offer_source": "warehouse",
                            "warehouse_id": 1,
                            "qty": 2,
                        }
                    ]
                },
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["telegram_url"], "https://t.me/Extremizer_bot?start=web_isolated-token")
        self.assertEqual(captured["items"][0]["price_snapshot_rub"], 34700)
        self.assertEqual(captured["items"][0]["qty"], 2)
        self.assertTrue(captured["db_file"].startswith(str(Path(_TMP.name).resolve())))


if __name__ == "__main__":
    unittest.main(verbosity=2)
