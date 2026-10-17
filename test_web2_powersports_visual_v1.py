"""POWERSPORTS v1 isolated browser interaction/regression. Asset fidelity reviewed separately."""
import unittest
from pathlib import Path
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server

ART=Path("artifacts/powersports-v1")
ART.mkdir(parents=True,exist_ok=True)

class PowersportsVisualV1(unittest.TestCase):
    def test_responsive_and_search_interaction(self):
        with mock_server() as (base,_),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            for width,height in [(360,800),(390,844),(430,932),(1280,900)]:
                with self.subTest(width=width):
                    page=browser.new_page(viewport={"width":width,"height":height})
                    page.goto(base+"/web2")
                    self.assertEqual(page.locator(".powersports-heading h1").inner_text(),"POWERSPORTS")
                    self.assertEqual(page.locator(".powersports-practice article").count(),3)
                    self.assertEqual(page.locator("#oemInput").get_attribute("placeholder"),"Введи каталожный номер")
                    self.assertEqual(page.locator("#searchButton span").first.inner_text(),"Искать")
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth-innerWidth"),1)
                    self.assertEqual(page.locator("#searchButton .search-lens").count(),1)
                    page.screenshot(path=str(ART/f"{width}-default.png"),full_page=True)
                    if width==1280:
                        page.locator("#oemInput").hover()
                        self.assertEqual(page.locator("#oemInput").evaluate("(e)=>getComputedStyle(e).backgroundColor"),"rgb(191, 248, 3)")
                        page.screenshot(path=str(ART/"1280-input-hover.png"),full_page=True)
                        page.locator("#searchButton").hover()
                        self.assertEqual(page.locator("#searchButton").evaluate("(e)=>getComputedStyle(e).backgroundColor"),"rgb(191, 248, 3)")
                        page.screenshot(path=str(ART/"1280-search-hover.png"),full_page=True)
                    page.locator("#oemInput").fill("417300574")
                    self.assertEqual(page.locator("#oemInput").evaluate("(e)=>getComputedStyle(e).backgroundColor"),"rgb(191, 248, 3)")
                    if width in (390,1280):page.screenshot(path=str(ART/f"{width}-input-active.png"),full_page=True)
                    page.locator("#searchButton").click()
                    page.locator("#product").wait_for(state="visible")
                    self.assertEqual(page.locator("#productOem").inner_text(),"417300574")
                    self.assertEqual(page.locator("#searchButton .search-lens").count(),1)
                    self.assertLessEqual(page.evaluate("document.documentElement.scrollWidth-innerWidth"),1)
                    if width in (390,1280):page.screenshot(path=str(ART/f"{width}-product-result.png"),full_page=True)
                    if width==390:
                        page.locator('[data-offer-key="usa"]').click()
                        page.screenshot(path=str(ART/"390-cart.png"))
                    page.close()
            browser.close()

    def test_enter_submit(self):
        with mock_server() as (base,_),sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page()
            page.goto(base+"/web2")
            page.locator("#oemInput").fill("417300574")
            page.locator("#oemInput").press("Enter")
            page.locator("#product").wait_for(state="visible")
            self.assertEqual(page.locator("#productOem").inner_text(),"417300574")
            browser.close()

if __name__=="__main__":unittest.main()
