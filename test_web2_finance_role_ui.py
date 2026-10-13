"""Browser regression for isolated WEB2 Finance v1.4 UI fixtures.

No production imports, DB, finance writes, real users, DCP or external network.
"""
from __future__ import annotations

import contextlib
import functools
import http.server
import os
from pathlib import Path
import shutil
import socketserver
import threading
import unittest

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parent / "web2"


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

    def test_warehouse_desktop_contract_and_search(self):
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.wait_for_selector("#warehouseBody tr")
            self.assertEqual(page.locator("#warehouseTable thead th").count(), 5)
            self.assertEqual(page.locator("#warehouseBody tr").count(), 15)
            self.assertEqual(norm(page.locator("#historyTotal").inner_text()), "3 425 ₽")
            self.assertEqual(norm(page.locator("#postedBalance").inner_text()), "3 425 ₽")
            self.assertEqual(norm(page.locator("#reservedBalance").inner_text()), "1 000 ₽")
            self.assertEqual(norm(page.locator("#availableBalance").inner_text()), "2 425 ₽")

            control = page.locator('tr[data-order="I17J-5675"]')
            self.assertEqual(control.locator(".order-link").nth(0).inner_text(), "I17J-5675")
            self.assertEqual(control.locator(".order-link").nth(1).inner_text(), "5241")
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

    def test_role_order_reversal_and_same_order_card(self):
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.wait_for_selector("#warehouseBody tr")
            row = page.locator('tr[data-order="I17J-5675"]')
            row.locator(".order-link").nth(0).click()
            self.assertEqual(page.locator("#dialogTitle").inner_text(), "I17J-5675 / 5241")
            page.locator("#dialogClose").click()

            page.locator("#skladRole").click()
            self.assertEqual(
                page.locator("#ordersHeader").inner_text(),
                "№ нашего заказа / № заказа Extremizer",
            )
            row = page.locator('tr[data-order="I17J-5675"]')
            self.assertEqual(row.locator(".order-link").nth(0).inner_text(), "5241")
            self.assertEqual(row.locator(".order-link").nth(1).inner_text(), "I17J-5675")
            row.locator(".order-link").nth(0).click()
            self.assertEqual(page.locator("#dialogTitle").inner_text(), "I17J-5675 / 5241")

    def test_status_palette_exact(self):
        expected = {
            "new": "rgb(255, 255, 255)",
            "assembled-unpaid": "rgb(255, 107, 101)",
            "assembled-paid": "rgb(255, 241, 118)",
            "shipped": "rgb(191, 248, 3)",
            "system-error": "rgb(255, 48, 48)",
        }
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 1440, "height": 1000})
            page.goto(url)
            page.wait_for_selector("#warehouseBody tr")
            for key, color in expected.items():
                locator = page.locator(f'tr[data-status="{key}"]').first
                self.assertEqual(
                    locator.evaluate("el => getComputedStyle(el).backgroundColor"),
                    color,
                )
                self.assertNotEqual(locator.locator(".status-inline").inner_text(), "")

    def test_mobile_structured_cards_keep_five_fields_and_no_overflow(self):
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 390, "height": 844})
            page.goto(url)
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

    def test_client_info_events_are_zero_wallet_delta(self):
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 1200, "height": 900})
            page.goto(url)
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

    def test_dealer_negative_balance_order_and_gate_are_separate(self):
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 1200, "height": 900})
            page.goto(url)
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

    def test_acting_as_indicator_is_visible_and_fixture_exit_is_local_only(self):
        with fixture_server() as url:
            page = self.browser().new_page(viewport={"width": 1200, "height": 900})
            page.goto(url)
            banner = page.locator("#actingBanner")
            self.assertTrue(banner.is_visible())
            text = banner.inner_text()
            self.assertIn("admin-1", text)
            self.assertIn("Склад 1", text)
            self.assertIn("rf_warehouse_user", text)
            page.locator("#actingExit").click()
            self.assertFalse(banner.is_visible())
            self.assertIn(
                "Реальная сессия не изменялась",
                page.locator("#toast").inner_text(),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
