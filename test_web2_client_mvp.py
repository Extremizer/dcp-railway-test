from __future__ import annotations

import contextlib
import os
from pathlib import Path
import subprocess
import time
import unittest

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parent
ARTIFACTS = ROOT / "artifacts"


@contextlib.contextmanager
def staging_server():
    port = "8087"
    env = os.environ.copy()
    process = subprocess.Popen(
        [
            "python", "-m", "uvicorn", "web2_staging_app:app",
            "--host", "127.0.0.1", "--port", port,
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
                    f"http://127.0.0.1:{port}/staging-health", timeout=1
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
        ARTIFACTS.mkdir(exist_ok=True)
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def new_page(self, width, height):
        return self.browser.new_page(viewport={"width": width, "height": height})

    def search(self, page, oem="417300574"):
        page.locator("#oemInput").fill(oem)
        page.locator("#searchButton").click()
        page.wait_for_selector("#product:not(.hidden)")

    def assert_no_horizontal_overflow(self, page):
        result = page.evaluate(
            "() => ({sw: document.documentElement.scrollWidth, iw: window.innerWidth})"
        )
        self.assertLessEqual(result["sw"], result["iw"])

    def assert_touch_target(self, locator):
        box = locator.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box["width"], 44)
        self.assertGreaterEqual(box["height"], 44)

    def add_usa_and_rf(self, page):
        page.locator('[data-offer-key="usa"]').click()
        page.locator("#cartClose").click()
        page.locator("#qtyPlus").click()
        self.assertEqual(page.locator("#qtyValue").inner_text(), "2")
        page.locator('[data-offer-key="warehouse:1"]').click()
        page.wait_for_selector("#cartPanel.open")
        page.wait_for_timeout(300)

    def assert_cart_layering(self, page):
        layers = page.evaluate(
            """() => {
              const z = (s) => Number(getComputedStyle(document.querySelector(s)).zIndex || 0);
              const cart = document.querySelector('#cartPanel').getBoundingClientRect();
              return {
                header: z('.topbar'),
                scrim: z('#scrim'),
                cart: z('#cartPanel'),
                cartTop: cart.top,
                cartBottom: cart.bottom,
                viewport: window.innerHeight,
                cartLeft: cart.left,
                cartRight: cart.right,
                viewportWidth: window.innerWidth,
              };
            }"""
        )
        self.assertGreater(layers["scrim"], layers["header"])
        self.assertGreater(layers["cart"], layers["scrim"])
        self.assertAlmostEqual(layers["cartTop"], 0, delta=1)
        self.assertAlmostEqual(layers["cartBottom"], layers["viewport"], delta=2)
        self.assertAlmostEqual(layers["cartRight"], layers["viewportWidth"], delta=2)

    def test_01_product_desktop_approved_usa_format(self):
        with staging_server() as url:
            page = self.new_page(1440, 1000)
            page.goto(url)
            self.search(page)
            self.assertEqual(page.locator("#productOem").inner_text(), "417300574")
            self.assertEqual(
                page.locator(".customer-price-line").inner_text().replace("\xa0", " "),
                "Ваша цена — 29 200* ₽",
            )
            self.assertEqual(
                page.locator(".price-detail").inner_text().replace("\xa0", " "),
                "РРЦ — 35 000 ₽",
            )
            self.assertEqual(page.locator("#benefitLine").inner_text(), "Ваша выгода — 16.6%")
            self.assertEqual(
                page.locator("#usaDeliveryNotice").inner_text(),
                "* - в цену не входит стоимость доставки из штатов 🚚",
            )
            self.assertTrue(page.locator("#deliveryNotice").get_attribute("class").endswith("hidden"))
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(ARTIFACTS / "01-product-desktop-1440.png"), full_page=True)

    def test_02_product_mobile_390_touch_and_no_overflow(self):
        with staging_server() as url:
            page = self.new_page(390, 844)
            page.goto(url)
            self.search(page)
            self.assert_no_horizontal_overflow(page)
            self.assert_touch_target(page.locator("#qtyMinus"))
            self.assert_touch_target(page.locator("#qtyPlus"))
            self.assert_touch_target(page.locator('[data-offer-key="usa"]'))
            self.assert_touch_target(page.locator('[data-offer-key="warehouse:1"]'))
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(ARTIFACTS / "02-product-mobile-390.png"), full_page=True)

    def test_03_open_cart_desktop_usa_rf_quantity_total_remove_close(self):
        with staging_server() as url:
            page = self.new_page(1440, 1000)
            page.goto(url)
            self.search(page)
            self.add_usa_and_rf(page)

            self.assertEqual(page.locator("#cartItems .cart-item").count(), 2)
            self.assertIn("склад США", page.locator("#cartItems").inner_text())
            self.assertIn("Склад 1", page.locator("#cartItems").inner_text())
            self.assertEqual(page.locator("#cartCount").inner_text(), "3")
            self.assertEqual(page.locator("#cartTotal").inner_text().replace("\xa0", " "), "98 600 ₽")
            self.assert_cart_layering(page)
            page.screenshot(path=str(ARTIFACTS / "03-open-cart-desktop-1440.png"))

            page.locator("[data-plus='0']").click()
            self.assertEqual(page.locator("#cartTotal").inner_text().replace("\xa0", " "), "127 800 ₽")
            page.locator("[data-remove='1']").click()
            self.assertEqual(page.locator("#cartItems .cart-item").count(), 1)
            self.assertEqual(page.locator("#cartTotal").inner_text().replace("\xa0", " "), "58 400 ₽")
            page.locator("#cartClose").click()
            self.assertFalse(page.locator("#cartPanel").get_attribute("class").endswith("open"))

    def test_04_open_cart_mobile_390_layering_touch_scroll(self):
        with staging_server() as url:
            page = self.new_page(390, 844)
            page.goto(url)
            self.search(page)
            self.add_usa_and_rf(page)
            self.assert_no_horizontal_overflow(page)
            self.assert_cart_layering(page)
            self.assert_touch_target(page.locator("#cartClose"))
            self.assert_touch_target(page.locator("[data-minus='0']"))
            self.assert_touch_target(page.locator("[data-plus='0']"))
            self.assert_touch_target(page.locator("[data-remove='0']"))
            cart_box = page.locator("#cartPanel").bounding_box()
            self.assertIsNotNone(cart_box)
            self.assertAlmostEqual(cart_box["x"], 0, delta=2)
            self.assertAlmostEqual(cart_box["width"], 390, delta=2)
            self.assertTrue(page.locator("#telegramButton").is_visible())
            page.screenshot(path=str(ARTIFACTS / "04-open-cart-mobile-390.png"))

    def test_05_search_loading_busy_state(self):
        with staging_server() as url:
            page = self.new_page(1440, 850)
            page.goto(url)
            page.locator("#oemInput").fill("SLOW")
            page.locator("#searchButton").click(no_wait_after=True)
            page.wait_for_function(
                "() => document.querySelector('#searchForm').getAttribute('aria-busy') === 'true'"
            )
            self.assertTrue(page.locator("#searchButton").is_disabled())
            self.assertEqual(page.locator("#searchButton").inner_text(), "Ищем…")
            self.assertEqual(page.locator("#state").inner_text(), "Ищем…")
            page.screenshot(path=str(ARTIFACTS / "05-search-loading.png"))
            page.wait_for_selector("#product:not(.hidden)")
            self.assertEqual(page.locator("#searchForm").get_attribute("aria-busy"), "false")
            self.assertFalse(page.locator("#searchButton").is_disabled())

    def test_06_search_error_recovers_controls(self):
        with staging_server() as url:
            page = self.new_page(1440, 850)
            page.goto(url)
            page.locator("#oemInput").fill("UNKNOWN-P0")
            page.locator("#searchButton").click()
            page.wait_for_selector("#state.error")
            self.assertIn("не найден", page.locator("#state").inner_text())
            self.assertEqual(page.locator("#searchForm").get_attribute("aria-busy"), "false")
            self.assertFalse(page.locator("#searchButton").is_disabled())
            self.assertTrue(page.locator("#oemInput").is_editable())
            page.screenshot(path=str(ARTIFACTS / "06-search-error.png"))

    def test_07_no_msrp_and_warehouse_only_states(self):
        with staging_server() as url:
            page = self.new_page(1280, 900)
            page.goto(url)
            self.search(page, "NO-MSRP")
            self.assertTrue(page.locator("#msrpBlock").get_attribute("class").endswith("hidden"))
            self.assertTrue(page.locator("#benefitLine").get_attribute("class").endswith("hidden"))
            self.assertTrue(page.locator("#usaDeliveryNotice").is_visible())

            page.locator("#oemInput").fill("WAREHOUSE-ONLY")
            page.locator("#searchButton").click()
            page.wait_for_function(
                "() => document.querySelector('#productOem').textContent === 'WAREHOUSE-ONLY'"
            )
            self.assertTrue(page.locator(".price-panel").get_attribute("class").endswith("hidden"))
            self.assertTrue(page.locator("#usaDeliveryNotice").get_attribute("class").endswith("hidden"))
            self.assertTrue(page.locator("#deliveryNotice").get_attribute("class").endswith("hidden"))
            self.assertEqual(page.locator('[data-offer-key="warehouse:1"]').count(), 1)

    def test_08_no_client_side_benefit_recalculation(self):
        with staging_server() as url:
            page = self.new_page(1280, 900)
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
            self.search(page)
            self.assertTrue(page.locator("#benefitLine").get_attribute("class").endswith("hidden"))
            self.assertEqual(page.locator("#benefit").inner_text(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
