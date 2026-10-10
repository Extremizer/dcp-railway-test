"""Browser-level WEB2 smoke with mocked API: no production imports or DB.

Run with: python -m playwright install chromium
          python -m unittest -v test_web2_browser.py
"""
import threading
import json
import unittest
from contextlib import contextmanager
from socket import socket

import uvicorn
from fastapi import FastAPI
from playwright.sync_api import sync_playwright

from web2_ui_routes import attach_web2_ui


def mock_card(msrp=35000, price=29200):
    benefit = round((msrp - price) / msrp * 100, 1) if msrp and msrp > price else None
    return {
        "manufacturer": "Ski-Doo", "oem": "417300574",
        "requested_oem": "417300574", "name": "Тестовая OEM позиция",
        "catalog": "Test", "price": {
            "customer_rub": price,
            "msrp_rub": msrp if benefit is not None else None,
            "benefit_pct": benefit,
        },
        "offers": [
            {"key": "usa", "source": "usa", "warehouse_id": None,
             "label": "США", "price_rub": price,
             "available_quantity": None, "can_add": True},
            {"key": "warehouse:1", "source": "warehouse", "warehouse_id": 1,
             "label": "Склад 1", "price_rub": 34700,
             "available_quantity": 26, "can_add": True},
        ],
        "stock": {"known": True, "refreshing": False, "warehouses": [
            {"warehouse_id": 1, "warehouse": "Склад 1",
             "quantity": 26, "price_rub": 34700}]},
        "weight": None,
        "delivery_notice": "* - в цену не входит стоимость доставки из штатов 🚚",
    }


@contextmanager
def mock_server():
    app = FastAPI()
    attach_web2_ui(app)
    handoffs = []

    @app.get("/api/v1/oem/{oem}")
    def search(oem: str):
        return mock_card(msrp=25000 if oem == "NO-MSRP" else 35000)

    @app.get("/api/v1/oem/{oem}/stock")
    def stock(oem: str):
        return {**mock_card()["stock"], "offers": mock_card()["offers"]}

    @app.post("/api/v1/handoff")
    def handoff(payload: dict):
        handoffs.append(payload)
        return {"telegram_url": "https://t.me/Extremizer_bot"}

    with socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        import time
        for _ in range(100):
            if server.started:
                break
            time.sleep(0.05)
        if not server.started:
            raise RuntimeError("Mock server failed to start")
        yield f"http://127.0.0.1:{port}", handoffs
    finally:
        server.should_exit = True
        thread.join(timeout=5)


class Web2BrowserTests(unittest.TestCase):
    def test_oem_price_warehouse_cart_and_handoff(self):
        with mock_server() as (base, handoffs), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.locator("#product").wait_for(state="visible")
            self.assertIn("417300574", page.locator("#productOem").inner_text())
            self.assertIn("29", page.locator("#customerPrice").inner_text())
            self.assertTrue(page.locator("#msrpBlock").is_visible())
            self.assertIn("Склад 1", page.locator("#stock").inner_text())
            page.locator('button[data-offer-key="warehouse:1"]').click()
            self.assertTrue(page.locator("#cartPanel").evaluate("(el) => el.classList.contains('open')"))
            self.assertIn("34", page.locator("#cartTotal").inner_text())
            page.route("https://t.me/**", lambda route: route.fulfill(status=200, body="Telegram mocked"))
            page.locator("#telegramButton").click()
            page.wait_for_url("https://t.me/Extremizer_bot")
            self.assertEqual(handoffs[0]["items"][0]["offer_source"], "warehouse")
            self.assertEqual(handoffs[0]["items"][0]["warehouse_id"], 1)
            browser.close()

    def test_msrp_hidden_when_below_customer_price(self):
        with mock_server() as (base, _), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("NO-MSRP")
            page.locator("#searchForm button").click()
            page.locator("#product").wait_for(state="visible")
            self.assertFalse(page.locator("#msrpBlock").is_visible())
            browser.close()


    def test_cart_quantity_cannot_exceed_warehouse_stock(self):
        with mock_server() as (base, _), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.locator("#product").wait_for(state="visible")
            page.locator('button[data-offer-key="warehouse:1"]').click()
            for _ in range(30):
                page.locator('[data-plus="0"]').click()
            self.assertEqual(page.locator("#cartCount").inner_text(), "26")
            page.locator('[data-remove="0"]').click()
            self.assertEqual(page.locator("#cartCount").inner_text(), "0")
            browser.close()

    def test_stock_refresh_replaces_offers(self):
        with mock_server() as (base, _), sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.route("**/api/v1/oem/417300574", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({**mock_card(), "stock": {
                    "known": False, "refreshing": True, "warehouses": []}})))
            page.route("**/api/v1/oem/417300574/stock?*", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({**mock_card()["stock"], "offers": mock_card()["offers"]})))
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.locator("#product").wait_for(state="visible")
            page.get_by_text("Проверяем склады…").wait_for(state="visible")
            page.locator("#stock").get_by_text("Склад 1").wait_for(state="visible", timeout=10000)
            browser.close()


if __name__ == "__main__":
    unittest.main()
