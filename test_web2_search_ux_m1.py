"""Isolated browser tests for WEB2 OEM search UX. Mock-only, no production."""
import json
import unittest
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server, mock_card

class SearchUxMilestoneTests(unittest.TestCase):
    def test_found_not_found_technical_and_recovery(self):
        with mock_server() as (base, _), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width":390,"height":844})
            page.route("**/api/v1/oem/MISSING", lambda route: route.fulfill(status=404, body="not found"))
            page.route("**/api/v1/oem/BROKEN", lambda route: route.fulfill(status=503, body="unavailable"))
            page.goto(base + "/web2")
            for oem, expected in [("MISSING", "не найден"), ("BROKEN", "Техническая ошибка")]:
                page.locator("#oemInput").fill(oem)
                page.locator("#searchButton").click()
                page.locator("#state.error").wait_for(state="visible")
                self.assertIn(expected, page.locator("#state").inner_text())
                self.assertTrue(page.locator("#searchButton").is_enabled())
                self.assertFalse(page.locator("#product").is_visible())
            page.locator("#oemInput").fill("417300574")
            page.locator("#searchButton").click()
            page.locator("#product").wait_for(state="visible")
            self.assertEqual(page.locator("#productOem").inner_text(), "417300574")
            self.assertFalse(page.locator("#state").is_visible())
            self.assertFalse(page.evaluate("document.documentElement.scrollWidth > innerWidth + 1"))
            browser.close()

    def test_malformed_search_response(self):
        with mock_server() as (base, _), sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            page = browser.new_page()
            page.route("**/api/v1/oem/INVALID", lambda route: route.fulfill(
                status=200, content_type="application/json", body=json.dumps({"price": None})))
            page.goto(base + "/web2")
            page.locator("#oemInput").fill("INVALID")
            page.locator("#searchButton").click()
            page.locator("#state.error").wait_for(state="visible")
            self.assertIn("Техническая ошибка", page.locator("#state").inner_text())
            self.assertTrue(page.locator("#searchButton").is_enabled())
            browser.close()

if __name__ == "__main__":
    unittest.main()
