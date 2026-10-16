"""WEB2 responsive M4 isolated browser evidence; mocked API only."""
import json
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server, mock_card

SHOTS = Path("artifacts/mobile-m4")
SHOTS.mkdir(parents=True, exist_ok=True)
LONG = "AAAAAAAAAAAAAAAAAAAAAAAAAAAA"
class MobileM4(unittest.TestCase):
    def assert_fit(self, page):
        dims=page.evaluate("""() => ({width:innerWidth, scroll:document.documentElement.scrollWidth})""")
        self.assertLessEqual(dims["scroll"], dims["width"]+1)
    def assert_touch(self, locator):
        box=locator.bounding_box()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box["width"],44)
        self.assertGreaterEqual(box["height"],44)
    def test_responsive_search_product_error_desktop(self):
        with mock_server() as (base,_),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            for width,height in [(360,800),(390,844),(430,932),(1280,900)]:
                with self.subTest(width=width):
                    page=browser.new_page(viewport={"width":width,"height":height})
                    card=mock_card()
                    card.update(oem=LONG,name="Очень длинное название детали Polaris BRP Yamaha "*7,
                        manufacturer="EXTRA-LONG-MANUFACTURER-"*6,requested_oem=LONG)
                    card["stock"]["warehouses"]=[{"warehouse_id":1,"warehouse":"Очень длинное название склада / источника "*6,"quantity":4,"price_rub":34800},
                         {"warehouse_id":2,"warehouse":"Склад 2","quantity":3,"price_rub":35500}]
                    card["offers"]=[{"key":"usa","source":"usa","warehouse_id":None,"label":"США","price_rub":29200,"available_quantity":None,"can_add":True},
                        {"key":"warehouse:1","source":"warehouse","warehouse_id":1,"label":"Очень длинное название склада "*5,"price_rub":34800,"available_quantity":4,"can_add":True}]
                    page.route("**/api/v1/oem/"+LONG,lambda r:r.fulfill(status=200,content_type="application/json",body=json.dumps(card)))
                    page.route("**/api/v1/oem/MISSING",lambda r:r.fulfill(status=404,body="Not found"))
                    page.goto(base+"/web2")
                    self.assert_fit(page)
                    self.assert_touch(page.locator("#searchButton"))
                    if width==390: page.screenshot(path=str(SHOTS/"390-empty-search.png"),full_page=True)
                    page.locator("#oemInput").fill(LONG)
                    page.locator("#searchButton").click()
                    page.locator("#product").wait_for(state="visible")
                    self.assert_fit(page)
                    self.assert_touch(page.locator('[data-offer-key="usa"]'))
                    self.assert_touch(page.locator('[data-offer-key="warehouse:1"]'))
                    self.assertTrue(page.locator("#productOem").is_visible())
                    if width==390:
                        page.screenshot(path=str(SHOTS/"390-product-found-rf.png"),full_page=True)
                    if width==1280:page.screenshot(path=str(SHOTS/"1280-product.png"),full_page=True)
                    page.locator("#oemInput").fill("MISSING")
                    page.locator("#searchButton").click()
                    page.locator("#state.error").wait_for(state="visible")
                    self.assert_fit(page)
                    self.assert_touch(page.locator("#searchButton"))
                    if width==390:page.screenshot(path=str(SHOTS/"390-error.png"),full_page=True)
                    page.close()
            browser.close()

    def test_mobile_cart_three_oem_scroll_resize_and_empty(self):
        with mock_server() as (base,_),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":390,"height":844})
            def handler(route):
                oem=route.request.url.split("/")[-1]
                card=mock_card()
                card.update(oem=oem,requested_oem=oem,name="Длинное имя "+oem*15)
                route.fulfill(status=200,content_type="application/json",body=json.dumps(card))
            page.route("**/api/v1/oem/*",handler)
            page.goto(base+"/web2")
            for oem,qty in [("AAA",2),("BBB",1),("CCC",3)]:
                page.locator("#oemInput").fill(oem)
                page.locator("#searchButton").click()
                page.wait_for_function("(v)=>document.querySelector('#productOem').textContent===v",arg=oem)
                for _ in range(qty-1):page.locator("#qtyPlus").click()
                page.locator('[data-offer-key="usa"]').click()
                page.locator("#cartClose").click()
            page.locator("#cartButton").click()
            self.assertEqual(page.locator("#cartItems .cart-item").count(),3)
            self.assert_touch(page.locator("#cartClose"))
            self.assert_touch(page.locator('[data-plus="0"]'))
            self.assert_touch(page.locator('[data-minus="0"]'))
            self.assert_touch(page.locator('[data-remove="0"]'))
            self.assert_touch(page.locator("#telegramButton"))
            self.assert_fit(page)
            page.screenshot(path=str(SHOTS/"390-cart-three-oem.png"))
            page.set_viewport_size({"width":360,"height":640})
            self.assert_fit(page)
            self.assertTrue(page.locator("#cartPanel").is_visible())
            page.set_viewport_size({"width":430,"height":932})
            self.assert_fit(page)
            page.locator("#cartClose").click()
            self.assertFalse(page.locator("#cartPanel").get_attribute("class").endswith("open"))
            page.locator("#cartButton").click()
            self.assertEqual(page.locator("#cartItems .cart-item").count(),3)
            for _ in range(3):
                page.locator('[data-remove="0"]').click()
            self.assertTrue(page.locator("#cartEmpty").is_visible())
            self.assertFalse(page.locator("#cartFooter").is_visible())
            page.locator("#cartClose").click()
            page.locator("#oemInput").fill("DDD")
            page.locator("#searchButton").click()
            page.wait_for_function("()=>document.querySelector('#productOem').textContent==='DDD'")
            browser.close()

    def test_mobile_loading_and_technical_error(self):
        with mock_server() as (base,_),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":390,"height":844})
            page.route("**/api/v1/oem/BAD",lambda r:r.fulfill(status=503,body="unavailable"))
            page.route("**/api/v1/oem/SLOW",lambda r:r.fulfill(status=200,content_type="application/json",body=json.dumps(mock_card())))
            page.goto(base+"/web2")
            page.route("**/api/v1/oem/PENDING",lambda r: __import__("time").sleep(0.7) or r.fulfill(status=200,content_type="application/json",body=json.dumps(mock_card())))
            page.locator("#oemInput").fill("PENDING")
            page.locator("#searchButton").click(no_wait_after=True)
            page.wait_for_function("() => document.querySelector('#state').classList.contains('loading')")
            page.screenshot(path=str(SHOTS/"390-loading.png"))
            self.assert_fit(page)
            page.locator("#product").wait_for(state="visible")
            page.locator("#oemInput").fill("SLOW")
            page.locator("#searchButton").click()
            page.locator("#product").wait_for(state="visible")
            page.locator("#oemInput").fill("BAD")
            page.locator("#searchButton").click()
            page.locator("#state.error").wait_for(state="visible")
            self.assertIn("Техническая ошибка",page.locator("#state").inner_text())
            self.assert_fit(page)
            browser.close()

if __name__=="__main__":unittest.main()
