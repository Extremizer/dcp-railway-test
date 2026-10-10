"""Static WEB2 API-contract regression without production database access.

Checks frontend/API field alignment and the required MSRP display policy.
Run: python -B -m unittest -v test_web2_contract.py
"""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parent
FRONT = (ROOT / "web2" / "app.js").read_text(encoding="utf-8")
BACK = (ROOT / "web_app.py").read_text(encoding="utf-8")


class Web2ContractTests(unittest.TestCase):
    def test_search_route_and_payload_fields(self):
        self.assertIn('/api/v1/oem/${encodeURIComponent(oem)}', FRONT)
        self.assertIn('@app.get("/api/v1/oem/{oem}")', BACK)
        for key in ("manufacturer", "oem", "name", "price", "offers", "stock", "weight"):
            self.assertRegex(BACK, r'["\\\']' + re.escape(key) + r'["\\\']')

    def test_stock_refresh_route(self):
        self.assertIn('/stock?${params}', FRONT)
        self.assertIn('@app.get("/api/v1/oem/{oem}/stock")', BACK)
        self.assertIn('"refreshing"', BACK)
        self.assertIn('"offers"', BACK)

    def test_msrp_only_when_above_customer_price(self):
        self.assertIn("card.price.msrp_rub > card.price.customer_rub", FRONT)
        self.assertIn("msrp_rub > customer_rub", BACK)
        self.assertIn('msrp.classList.add("hidden")', FRONT)

    def test_warehouse_and_usa_offer_contract(self):
        for field in ("offer.key", "offer.source", "offer.price_rub", "offer.can_add", "offer.available_quantity"):
            self.assertIn(field, FRONT)
        for source in ('"usa"', '"warehouse"'):
            self.assertIn(source, BACK)
        self.assertIn('offer.warehouse_id', FRONT)

    def test_cart_handoff_route_and_required_payload(self):
        self.assertIn('fetch("/api/v1/handoff"', FRONT)
        self.assertIn('@app.post("/api/v1/handoff")', BACK)
        for key in ("manufacturer:", "oem:", "offer_source:", "warehouse_id:", "qty:"):
            self.assertIn(key, FRONT)
        self.assertIn("telegram_url", FRONT)

    def test_no_finance_mutations_in_ui_module(self):
        routes = (ROOT / "web2_ui_routes.py").read_text(encoding="utf-8")
        self.assertNotIn("import web_app", routes)
        self.assertNotIn("sqlite3", routes)
        self.assertNotIn("ORDER_CHARGE", routes)


if __name__ == "__main__":
    unittest.main()
