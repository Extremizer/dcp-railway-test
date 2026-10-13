"""Browser regression for isolated WEB2 Finance v1.4 role-consistent UI.

No production imports, DB, finance writes, real users, DCP or external network.
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import json
import os
from pathlib import Path
import shutil
import socketserver
import threading
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent / "web2"
FIXTURE = ROOT / "finance_role_preview_fixture.json"


def norm(text: str) -> str:
    return text.replace("\xa0", " ")


@contextlib.contextmanager
def fixture_server():
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(ROOT))

    class Server(socketserver.TCPServer):
        allow_reuse_address = True

    with Server(("127.0.0.1", 0), handler) as server:
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{port}/finance_role_preview.html"
        finally:
            server.shutdown()
            thread.join(timeout=3)


class Web2FinanceRoleUiTests(unittest.TestCase):
    def browser(self):
        p = sync_playwright().start()
        launch = {"headless": True}
        if os.name == "nt":
            launch["channel"] = "chrome"
        else:
            executable = shutil.which("chromium") or shutil.which("chromium-browser")
            if executable:
                launch["executable_path"] = executable
                launch["args"] = ["--no-sandbox"]
        browser = p.chromium.launch(**launch)
        self.addCleanup(p.stop)
        self.addCleanup(browser.close)
        return browser

    def page(self, browser, url, width=1440, height=1000):
        page = browser.new_page(viewport={"width": width, "height": height})
        page.goto(url)
        page.wait_for_function(
            "document.querySelector('#warehouseBody') && document.querySelector('#warehouseBody').children.length > 0"
        )
        return page

    def enable_demo(self, page):
        page.locator("#demoToggle").click()
        self.assertTrue(page.locator("#demoBanner").is_visible())
        self.assertFalse(page.locator("#actingBanner").is_visible())

    def test_effective_rf_warehouse_role_locks_initial_sklad_view(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url)
            banner = page.locator("#actingBanner")
            self.assertTrue(banner.is_visible())
            text = banner.inner_text()
            self.assertIn("admin-1", text)
            self.assertIn("Склад 1", text)
            self.assertIn("rf_warehouse_user", text)
            self.assertIn("SKLAD", text)
            self.assertEqual(page.locator("#activeViewLabel").inner_text(), "SKLAD")
            self.assertEqual(
                page.locator("#ordersHeader").inner_text(),
                "№ нашего заказа / № заказа Extremizer",
            )
            control = page.locator('tr[data-order="I17J-5675"]')
            self.assertEqual(control.locator(".order-link").nth(0).inner_text(), "5241")
            self.assertEqual(control.locator(".order-link").nth(1).inner_text(), "I17J-5675")
            self.assertFalse(page.locator('[data-panel="clientPanel"]').is_visible())
            self.assertFalse(page.locator('[data-panel="dealerPanel"]').is_visible())
            self.assertFalse(page.locator("#demoBanner").is_visible())

    def test_admin_sklad_comparison_requires_explicit_demo_mode(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url)
            self.enable_demo(page)
            self.assertTrue(page.locator("#adminRole").is_visible())
            self.assertTrue(page.locator("#skladRole").is_visible())

            page.locator("#adminRole").click()
            self.assertEqual(page.locator("#activeViewLabel").inner_text(), "ADMIN")
            self.assertEqual(
                page.locator("#ordersHeader").inner_text(),
                "№ нашего заказа / № заказа на складе",
            )
            control = page.locator('tr[data-order="I17J-5675"]')
            self.assertEqual(control.locator(".order-link").nth(0).inner_text(), "I17J-5675")
            self.assertEqual(control.locator(".order-link").nth(1).inner_text(), "5241")

            page.locator("#skladRole").click()
            self.assertEqual(page.locator("#activeViewLabel").inner_text(), "SKLAD")
            control = page.locator('tr[data-order="I17J-5675"]')
            self.assertEqual(control.locator(".order-link").nth(0).inner_text(), "5241")

            page.locator("#demoToggle").click()
            self.assertFalse(page.locator("#demoBanner").is_visible())
            self.assertTrue(page.locator("#actingBanner").is_visible())
            self.assertEqual(page.locator("#activeViewLabel").inner_text(), "SKLAD")

    def test_warehouse_desktop_contract_search_and_full_history_total(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url)
            self.assertEqual(page.locator("#warehouseTable thead th").count(), 5)
            self.assertEqual(page.locator("#warehouseBody tr").count(), 15)
            self.assertEqual(norm(page.locator("#historyTotal").inner_text()), "3 425 ₽")
            self.assertEqual(norm(page.locator("#postedBalance").inner_text()), "3 425 ₽")
            self.assertEqual(norm(page.locator("#reservedBalance").inner_text()), "1 000 ₽")
            self.assertEqual(norm(page.locator("#availableBalance").inner_text()), "2 425 ₽")

            control = page.locator('tr[data-order="I17J-5675"]')
            self.assertEqual(norm(control.locator(".balance-cell").inner_text()), "−8 500 ₽")
            self.assertEqual(norm(control.locator("td").nth(3).inner_text()), "8 500 ₽")
            self.assertEqual(
                control.locator(".customer-amount").evaluate(
                    "el => getComputedStyle(el).textDecorationLine"
                ),
                "line-through",
            )

            unpaid = page.locator('tr[data-order="I17J-5678"] .balance-cell')
            self.assertEqual(unpaid.inner_text(), "")

            page.locator("#warehouseSearch").fill("5241")
            self.assertEqual(page.locator("#warehouseBody tr").count(), 1)
            self.assertEqual(norm(page.locator("#historyTotal").inner_text()), "3 425 ₽")
            page.locator("#warehouseSearch").fill("Дмитрий")
            self.assertEqual(page.locator("#warehouseBody tr").count(), 1)
            page.locator("#warehouseSearch").fill("0746-435")
            self.assertEqual(page.locator("#warehouseBody tr").count(), 2)
            page.locator("#warehouseSearch").fill("2026-10-12")
            self.assertEqual(page.locator("#warehouseBody tr").count(), 2)

            page.locator("#warehouseSearch").fill("")
            page.locator("#pageSize").select_option("20")
            self.assertEqual(page.locator("#warehouseBody tr").count(), 18)

    def test_both_order_numbers_open_same_order_card(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url)
            row = page.locator('tr[data-order="I17J-5675"]')
            row.locator(".order-link").nth(0).click()
            self.assertEqual(page.locator("#dialogTitle").inner_text(), "I17J-5675 / 5241")
            page.locator("#dialogClose").click()
            row.locator(".order-link").nth(1).click()
            self.assertEqual(page.locator("#dialogTitle").inner_text(), "I17J-5675 / 5241")

    def test_only_four_business_statuses_and_system_error_is_separate(self):
        expected = {
            "new": "rgb(255, 255, 255)",
            "assembled-unpaid": "rgb(255, 107, 101)",
            "assembled-paid": "rgb(255, 241, 118)",
            "shipped": "rgb(191, 248, 3)",
        }
        with fixture_server() as url:
            page = self.page(self.browser(), url)
            self.assertEqual(page.locator(".legend .legend-item").count(), 4)
            self.assertEqual(page.locator('tr[data-status="system-error"]').count(), 0)
            for key, color in expected.items():
                locator = page.locator(f'tr[data-status="{key}"]').first
                self.assertEqual(
                    locator.evaluate("el => getComputedStyle(el).backgroundColor"),
                    color,
                )
                self.assertNotEqual(locator.locator(".status-inline").inner_text(), "")

            message = page.locator("[data-system-error='WAREHOUSE_READ_MODEL_STALE']")
            self.assertTrue(message.is_visible())
            self.assertIn("System Error", message.inner_text())
            self.assertIn("I17J-5672", message.inner_text())
            self.assertEqual(
                message.evaluate("el => getComputedStyle(el).borderTopColor"),
                "rgb(255, 48, 48)",
            )
            system_order = page.locator('tr[data-order="I17J-5672"]')
            self.assertEqual(system_order.get_attribute("data-status"), "new")
            self.assertIn("Новый", system_order.inner_text())

    def test_mobile_structured_cards_keep_five_fields_and_no_overflow(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url, width=390, height=844)
            page.wait_for_selector("#warehouseCards .mobile-card")
            self.assertFalse(page.locator("#warehouseTable").is_visible())
            card = page.locator("#warehouseCards .mobile-card").first
            labels = [x.upper() for x in card.locator(".field > span").all_inner_texts()]
            self.assertIn("ДАТА", labels)
            self.assertIn("БАЛАНС", labels)
            self.assertTrue(any("№ НАШЕГО ЗАКАЗА" in x for x in labels))
            self.assertIn("СУММА ДЛЯ КЛИЕНТА", labels)
            self.assertIn("СУММА ПО УСЛОВИЯМ СКЛАДА", labels)
            self.assertFalse(
                page.evaluate("document.documentElement.scrollWidth > window.innerWidth + 1")
            )
            first_pair = card.locator(".order-link").all_inner_texts()
            self.assertEqual(first_pair[0], "5245")
            self.assertEqual(first_pair[1], "I17J-5679")

    def test_client_info_events_are_zero_wallet_delta_in_demo_surface(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url, width=1200, height=900)
            self.enable_demo(page)
            page.locator('[data-panel="clientPanel"]').click()
            page.wait_for_selector("#clientEvents .event-card")
            types = page.locator("#clientEvents .event-card").evaluate_all(
                "els => els.map(x => x.dataset.eventType)"
            )
            self.assertEqual(
                types,
                ["INFO_DIRECT_WAREHOUSE_PAYMENT", "INFO_DIRECT_WAREHOUSE_REFUND"],
            )
            for card in page.locator("#clientEvents .event-card").all():
                self.assertIn("0 ₽", norm(card.inner_text()))
                self.assertNotIn("ORDER_CHARGE", card.inner_text())
            self.assertIn("47 800 ₽", norm(page.locator("#clientWalletBalance").inner_text()))

    def test_dealer_negative_balance_order_and_gate_are_separate_in_demo_surface(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url, width=1200, height=900)
            self.enable_demo(page)
            page.locator('[data-panel="dealerPanel"]').click()
            self.assertEqual(page.locator("#dealerBalance").inner_text(), "−$60.00")
            self.assertEqual(page.locator("#dealerOrderStatus").inner_text(), "Подтвержден")
            self.assertEqual(page.locator("#dealerGate").inner_text(), "BLOCKED")
            self.assertIn("ORDER_CHARGE ×1", page.locator("#dealerChargeCount").inner_text())
            self.assertNotIn("credit limit", page.locator("body").inner_text().lower())
            self.assertIn(
                "ещё не подключена",
                page.locator("#dealerPanel .guard-note").inner_text(),
            )
            page.locator("#dealerRepaid").click()
            self.assertEqual(page.locator("#dealerBalance").inner_text(), "$0.00")
            self.assertEqual(page.locator("#dealerGate").inner_text(), "READY")

    def test_acting_as_exit_control_cannot_claim_server_session_ended(self):
        with fixture_server() as url:
            page = self.page(self.browser(), url, width=1200, height=900)
            banner = page.locator("#actingBanner")
            self.assertTrue(banner.is_visible())
            page.locator("#actingExit").click()
            self.assertTrue(banner.is_visible())
            toast = page.locator("#toast")
            self.assertIn("не подключена", toast.inner_text())
            self.assertIn("не изменена", toast.inner_text())
            self.assertIn("не завершена", toast.inner_text())

    def test_missing_partner_amount_renders_blank_authoritative_value_not_zero(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["warehouse"]["rows"][0]["partnerAmountRub"] = None
        payload["warehouse"]["rows"][0]["partnerAmountAuthoritative"] = False
        payload["warehouse"]["rows"][0]["paymentSource"] = None
        payload["warehouse"]["rows"][0]["balanceDeltaRub"] = None

        with fixture_server() as url:
            browser = self.browser()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.route(
                "**/finance_role_preview_fixture.json",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload, ensure_ascii=False),
                ),
            )
            page.goto(url)
            page.wait_for_selector("#warehouseBody tr")
            first = page.locator("#warehouseBody tr").first
            self.assertEqual(first.locator(".partner-amount").inner_text(), "—")
            self.assertEqual(first.locator(".partner-amount").get_attribute("data-authoritative"), "false")
            self.assertNotEqual(norm(first.locator(".partner-amount").inner_text()), "0 ₽")

    def test_p3_nullable_history_fields_render_as_em_dash(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        row = payload["warehouse"]["rows"][0]
        row["date"] = None
        row["warehouseOrderNo"] = None
        row["customerAmountRub"] = None
        row["partnerAmountRub"] = None
        row["partnerAmountAuthoritative"] = False
        row["paymentSource"] = None
        row["paymentStatus"] = None
        row["balanceDeltaRub"] = None
        row["client"] = None
        row["oems"] = []

        with fixture_server() as url:
            browser = self.browser()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.route(
                "**/finance_role_preview_fixture.json",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload, ensure_ascii=False),
                ),
            )
            page.goto(url)
            page.wait_for_selector("#warehouseBody tr")
            first = page.locator("#warehouseBody tr").first
            cells = [norm(x) for x in first.locator("td").all_inner_texts()]
            self.assertIn("—", cells[0])
            self.assertIn("—", cells[1])
            self.assertEqual(norm(first.locator(".customer-amount").inner_text()), "—")
            self.assertEqual(norm(first.locator(".partner-amount").inner_text()), "—")
            self.assertEqual(first.locator(".balance-cell").inner_text(), "")
            self.assertNotIn("0 ₽", first.inner_text())

    def test_server_strings_are_escaped_not_inserted_as_active_html(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload["warehouse"]["rows"][0]["client"] = '<img id="xssClient" src=x onerror="window.__pwned=1">'
        payload["warehouse"]["rows"][0]["oems"] = ['<svg id="xssOem" onload="window.__pwned=2"></svg>']
        payload["warehouse"]["systemErrors"][0]["message"] = '<img id="xssError" src=x onerror="window.__pwned=3">'
        payload["clientWallet"]["events"][0]["description"] = '<img id="xssEvent" src=x onerror="window.__pwned=4">'
        payload["dealer"]["scenarios"]["debt"]["orderStatus"] = '<img id="xssDealer" src=x onerror="window.__pwned=5">'

        with fixture_server() as url:
            browser = self.browser()
            page = browser.new_page(viewport={"width": 1200, "height": 900})
            page.route(
                "**/finance_role_preview_fixture.json",
                lambda route: route.fulfill(
                    status=200,
                    content_type="application/json",
                    body=json.dumps(payload, ensure_ascii=False),
                ),
            )
            page.goto(url)
            page.wait_for_selector("#warehouseBody tr")
            self.assertEqual(page.locator("#xssClient").count(), 0)
            self.assertEqual(page.locator("#xssOem").count(), 0)
            self.assertEqual(page.locator("#xssError").count(), 0)
            self.assertIsNone(page.evaluate("window.__pwned"))

            self.enable_demo(page)
            page.locator('[data-panel="clientPanel"]').click()
            self.assertEqual(page.locator("#xssEvent").count(), 0)
            page.locator('[data-panel="dealerPanel"]').click()
            self.assertEqual(page.locator("#xssDealer").count(), 0)
            self.assertIsNone(page.evaluate("window.__pwned"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
