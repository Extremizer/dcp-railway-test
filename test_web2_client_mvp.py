from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import tempfile
import time
import unittest

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent


@contextlib.contextmanager
def staging_server():
    port = "8087"
    env = os.environ.copy()
    process = subprocess.Popen(
        [
            "python",
            "-m",
            "uvicorn",
            "web2_staging_app:app",
            "--host",
            "127.0.0.1",
            "--port",
            port,
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 20
        import urllib.request
        while time.time() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/staging-health",
                    timeout=1,
                ) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.2)
        else:
            output = process.stdout.read() if process.stdout else ""
            raise RuntimeError(f"staging server failed to start\n{output}")
        yield f"http://127.0.0.1:{port}/web2"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()


class Web2ClientMvpBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def test_oem_to_usa_cart_to_handoff(self):
        with staging_server() as url:
            page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.wait_for_selector("#product:not(.hidden)")
            self.assertEqual(page.locator("#productOem").inner_text(), "417300574")
            self.assertEqual(page.locator("#customerPrice").inner_text().replace("\xa0", " "), "29 200* ₽")
            self.assertEqual(page.locator("#offers .offer-card").count(), 2)
            Path("artifacts").mkdir(exist_ok=True)
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(150)
            page.screenshot(path="artifacts/web2-client-mvp-desktop.png", full_page=True)

            usa = page.locator('[data-offer-key="usa"]')
            self.assertTrue(usa.is_visible())
            usa.click()
            self.assertTrue(page.locator("#cartPanel").get_attribute("class").endswith("open"))
            self.assertEqual(page.locator("#cartCount").inner_text(), "1")
            self.assertIn("склад США", page.locator("#cartItems").inner_text())

            with page.expect_navigation():
                page.locator("#telegramButton").click()
            self.assertIn("staging_handoff=mocked", page.url)

    def test_rf_offer_quantity_and_cart(self):
        with staging_server() as url:
            page = self.browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(url)
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.wait_for_selector("#product:not(.hidden)")
            page.locator("#qtyPlus").click()
            self.assertEqual(page.locator("#qtyValue").inner_text(), "2")
            page.evaluate("window.scrollTo(0, 0)")
            page.wait_for_timeout(150)
            page.screenshot(path="artifacts/web2-client-mvp-mobile.png", full_page=True)
            page.locator('[data-offer-key="warehouse:1"]').click()
            item = page.locator("#cartItems .cart-item").first
            self.assertIn("Склад 1", item.inner_text())
            self.assertIn("2", item.inner_text())
            self.assertIn("69 400 ₽", item.inner_text().replace("\xa0", " "))

    def test_visual_review_cart_desktop(self):
        with staging_server() as url:
            page = self.browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.wait_for_selector("#product:not(.hidden)")

            page.locator('[data-offer-key="usa"]').click()
            page.locator("#cartClose").click()
            page.locator("#qtyPlus").click()
            page.locator('[data-offer-key="warehouse:1"]').click()

            self.assertEqual(page.locator("#cartItems .cart-item").count(), 2)
            self.assertEqual(page.locator("#cartCount").inner_text(), "3")
            self.assertEqual(
                page.locator("#cartTotal").inner_text().replace("\xa0", " "),
                "98 600 ₽",
            )
            Path("artifacts").mkdir(exist_ok=True)
            page.screenshot(
                path="artifacts/web2-client-mvp-cart-desktop.png",
                full_page=False,
            )

    def test_visual_review_cart_mobile(self):
        with staging_server() as url:
            page = self.browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(url)
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.wait_for_selector("#product:not(.hidden)")

            page.locator('[data-offer-key="usa"]').click()
            page.locator("#cartClose").click()
            page.locator("#qtyPlus").click()
            page.locator('[data-offer-key="warehouse:1"]').click()

            self.assertEqual(page.locator("#cartItems .cart-item").count(), 2)
            self.assertEqual(page.locator("#cartCount").inner_text(), "3")
            self.assertEqual(
                page.locator("#cartTotal").inner_text().replace("\xa0", " "),
                "98 600 ₽",
            )
            self.assertEqual(
                page.evaluate("document.documentElement.scrollWidth"),
                390,
            )
            Path("artifacts").mkdir(exist_ok=True)
            page.screenshot(
                path="artifacts/web2-client-mvp-cart-mobile.png",
                full_page=False,
            )

    def test_visual_review_search_states_mobile(self):
        with staging_server() as url:
            page = self.browser.new_page(viewport={"width": 390, "height": 844})
            page.goto(url)
            Path("artifacts").mkdir(exist_ok=True)

            page.evaluate("showState('Ищем OEM в БАЗЕ…')")
            page.screenshot(
                path="artifacts/web2-client-mvp-loading-mobile.png",
                full_page=True,
            )

            page.evaluate(
                "showState('Этот OEM пока не найден в проверенной БАЗЕ.', true)"
            )
            page.screenshot(
                path="artifacts/web2-client-mvp-error-mobile.png",
                full_page=True,
            )

    def test_no_client_side_benefit_recalculation(self):
        with staging_server() as url:
            page = self.browser.new_page(viewport={"width": 1280, "height": 900})
            page.goto(url)
            page.route(
                "**/api/v1/oem/417300574",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body='''{
                      "manufacturer":"Ski-Doo","oem":"417300574","requested_oem":"417300574",
                      "name":"Тест","catalog":"fixture","warehouse_only":false,
                      "price":{"available":true,"customer_rub":29200,"msrp_rub":35000,"benefit_pct":null},
                      "offers":[{"key":"usa","source":"usa","warehouse_id":null,"label":"США","price_rub":29200,"available_quantity":null,"can_add":true}],
                      "stock":{"known":true,"refreshing":false,"warehouses":[]},
                      "weight":null,
                      "delivery_notice":"* - в цену не входит стоимость доставки из штатов 🚚"
                    }'''
                ),
            )
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchForm button").click()
            page.wait_for_selector("#product:not(.hidden)")
            self.assertEqual(page.locator("#benefit").inner_text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)

# Theme 3 visual review trigger
