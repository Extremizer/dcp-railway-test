"""Isolated browser security and functional hardening checks; no production access."""
import json
import unittest
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server, mock_card

class HardeningTests(unittest.TestCase):
    def test_hostile_api_strings_are_text_not_html(self):
        with mock_server() as (base, _), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 390, "height": 844})
            hostile = '<img src=x onerror="window.__web2Pwned=true">'
            card = mock_card()
            card["name"] = hostile
            card["offers"][1]["label"] = hostile
            card["stock"]["warehouses"][0]["warehouse"] = hostile
            card["weight"] = {"actual_kg": hostile, "volume_kg": 1, "notice": hostile}
            page.route("**/api/v1/oem/417300574", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps(card)))
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchButton").click()
            page.locator("#product").wait_for(state="visible")
            self.assertIn(hostile, page.locator("#productName").inner_text())
            self.assertIn(hostile, page.locator("#stock").inner_text())
            self.assertIn(hostile, page.locator("#offers").inner_text())
            self.assertEqual(page.locator("img[src=x]").count(), 0)
            self.assertFalse(page.evaluate("Boolean(window.__web2Pwned)"))
            page.locator('button[data-offer-key="warehouse:1"]').click()
            self.assertIn(hostile, page.locator("#cartItems").inner_text())
            self.assertEqual(page.locator("#cartItems img").count(), 0)
            self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
            browser.close()

    def test_handoff_rejects_untrusted_redirect(self):
        with mock_server() as (base, _), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("dialog", lambda dialog: dialog.accept())
            page.route("**/api/v1/handoff", lambda route: route.fulfill(
                status=200, content_type="application/json",
                body=json.dumps({"telegram_url":"javascript:alert(1)"})))
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchButton").click()
            page.locator("#product").wait_for(state="visible")
            page.locator('button[data-offer-key="usa"]').click()
            page.locator("#telegramButton").click()
            self.assertTrue(page.locator("#telegramButton").is_enabled())
            self.assertIn("/web2", page.url)
            browser.close()

    def test_malformed_persisted_cart_fails_closed(self):
        with mock_server() as (base, _), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.add_init_script("localStorage.setItem('extremizer_web2_cart', '{malformed');")
            page.goto(base + "/web2")
            self.assertEqual(page.locator("#cartCount").inner_text(), "0")
            browser.close()

if __name__ == "__main__":
    unittest.main()
