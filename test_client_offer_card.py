# -*- coding: utf-8 -*-
"""Execute real card/price functions without bot startup, DB or network access."""
import ast
from decimal import Decimal, ROUND_CEILING
from html import escape
from html.parser import HTMLParser
import math
from pathlib import Path
from types import SimpleNamespace
import unittest

from _prod_finance_switch_regression import require_probnik_non_ui_unchanged

ROOT = Path(__file__).resolve().parent
FOOTNOTE = "* - в цену не входит стоимость доставки из штатов 🚚"


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def visible_text(html):
    parser = TextParser()
    parser.feed(html)
    return "".join(parser.parts)


def load_functions(filename, names, namespace):
    source = (ROOT / filename).read_text(encoding="utf-8")
    functions = [
        node for node in ast.parse(source).body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    assert {node.name for node in functions} == set(names)
    module = ast.Module(body=functions, type_ignores=[])
    exec(compile(module, filename, "exec"), namespace)
    return namespace


class ClientOfferCardTests(unittest.TestCase):
    def setUp(self):
        self.rows = [
            {"is_fresh": True, "available_quantity": 26, "price_rub": 34700,
             "public_name": "Склад 1"},
            {"is_fresh": True, "available_quantity": 6, "price_rub": 38700,
             "public_name": "Склад 3"},
        ]
        self.procenka = load_functions(
            "extremizer_bot.py",
            ["format_client_offer_card", "_client_stock_oem", "format_rub",
             "customer_rub_price", "customer_rub_price_from_dp"],
            {"escape": escape, "math": math, "Decimal": Decimal,
             "ROUND_CEILING": ROUND_CEILING, "PRICE_COEFFICIENT": 1.34,
             "USD_RUB_RATE": 100.0, "load_usd_rub_rate": lambda: 100.0,
             "ORDERS_DB_FILE": "unused.db",
             "warehouse_stock_service": SimpleNamespace(
                 client_stock_summary=lambda *a, **k: self.rows),
             "format_result": lambda result: "fallback:" + result["status"]},
        )
        self.probnik = load_functions(
            "probnik_app.py", ["_compose", "_format_rub", "_customer_price_rub"],
            {"escape": escape, "math": math, "PRICE_COEFFICIENT": 1.34,
             "USD_RUB_RATE": 100.0},
        )

    def cards(self, rrp=35000, dp=217.9, status="FOUND", manufacturer="Ski-Doo"):
        # Use the real price converters as well as the real formatters. No
        # change to the existing MSRP, coefficient or 100-RUB ceiling rules.
        info = {"manufacturer": manufacturer,
                "customer_rub": self.probnik["_customer_price_rub"](dp) if dp else None,
                "rrp_rub": rrp}
        result = {"status": status, "oem": "417300574", "manufacturer": manufacturer,
                  "_dealer_price_usd": dp, "price": rrp / 100 if rrp is not None else None}
        return [self.probnik["_compose"]("417300574", info, self.rows),
                self.procenka["format_client_offer_card"](result)]

    def test_exact_approved_card_and_cross_bot_parity(self):
        expected = (
            "🔎 <b>OEM:</b> <b>417300574 (Ski-Doo)</b>\n\n"
            "🇺🇸 <b>склад США:</b>\n"
            "Ваша цена — <b>29 200* ₽</b>\n"
            "РРЦ — <b>35 000 ₽</b>\n"
            "Ваша выгода — <b>16.6%</b>\n\n"
            "<b>* -</b> в цену не входит стоимость доставки из штатов 🚚\n\n"
            "🇷🇺 <b>Наличие в РФ:</b>\n"
            "• <b>Склад 1</b> — <b>34 700 ₽</b> (<b>26 шт.</b>)\n"
            "• <b>Склад 3</b> — <b>38 700 ₽</b> (<b>6 шт.</b>)"
        )
        for card in self.cards():
            self.assertEqual(card, expected)
            self.assertIn(FOOTNOTE, visible_text(card))

    def test_rrp_and_savings_hidden_together_without_higher_rrp(self):
        for rrp in (None, 0, 28000, 29200):
            with self.subTest(rrp=rrp):
                cards = self.cards(rrp=rrp)
                self.assertEqual(*cards)
                for card in cards:
                    self.assertNotIn("РРЦ", card)
                    self.assertNotIn("Ваша выгода", card)
                    self.assertIn("29 200* ₽", card)
                    self.assertIn(FOOTNOTE, visible_text(card))

    def test_no_usa_price_never_falls_back_to_rrp(self):
        cards = self.cards(dp=None)
        self.assertEqual(*cards)
        for card in cards:
            self.assertIn("Цена сейчас недоступна. Попробуйте повторить запрос позже.", card)
            self.assertNotIn("Ваша цена", card)
            self.assertNotIn("РРЦ", card)
            self.assertNotIn("Ваша выгода", card)
            self.assertNotIn(FOOTNOTE, visible_text(card))
            self.assertIn("34 700 ₽", card)

    def test_partial_uses_same_card(self):
        self.assertEqual(self.cards(status="PARTIAL"), self.cards())

    def test_procenka_non_offer_status_keeps_existing_fallback(self):
        for status in ("NOT_FOUND", "TECHNICAL_ERROR", "CLOUDFLARE"):
            self.assertEqual(self.procenka["format_client_offer_card"]({"status": status}),
                             "fallback:" + status)

    def test_html_escaping_and_unknown_manufacturer(self):
        for card in self.cards(manufacturer="A&B <brand>"):
            self.assertIn("A&amp;B &lt;brand&gt;", card)
            self.assertNotIn("<brand>", card)
        for card in self.cards(manufacturer=None):
            self.assertIn("417300574 (—)", card)

    def test_stock_filters_and_missing_warehouse_price(self):
        self.rows[:] = [
            {"is_fresh": False, "available_quantity": 3, "public_name": "stale"},
            {"is_fresh": True, "available_quantity": 0, "public_name": "zero"},
            {"is_fresh": True, "available_quantity": -1, "public_name": "negative"},
            {"is_fresh": True, "available_quantity": None, "public_name": "unknown"},
            {"is_fresh": True, "available_quantity": 2, "public_name": "Склад <2>"},
        ]
        cards = self.cards()
        self.assertEqual(*cards)
        for card in cards:
            self.assertIn("• <b>Склад &lt;2&gt;</b> — (<b>2 шт.</b>)", card)
            for name in ("stale", "zero", "negative", "unknown"):
                self.assertNotIn(name, card)

    def test_probnik_empty_and_unconfirmed_stock_states_preserved(self):
        for rows, expected in (
            ([], "Актуальный остаток сейчас не подтверждён."),
            ([{"is_fresh": True, "available_quantity": 0}], "Нет в наличии."),
            ([{"is_fresh": False, "available_quantity": 0}],
             "Актуальный остаток сейчас не подтверждён."),
        ):
            self.rows[:] = rows
            self.assertIn(expected, self.cards()[0])
            # Procenka historically omits an empty RF section.
            self.assertNotIn("Наличие в РФ:", self.cards()[1])

    def test_customer_price_still_rounds_up_to_100_rub(self):
        for card in self.cards(dp=218.0):
            self.assertIn("Ваша цена — <b>29 300* ₽</b>", card)


class ProbnikNonUiGuardTests(unittest.TestCase):
    def setUp(self):
        self.source = (ROOT / "probnik_app.py").read_text(encoding="utf-8")

    def test_current_card_passes_original_non_ui_checkpoint(self):
        require_probnik_non_ui_unchanged(self.source)

    def test_ui_body_changes_are_allowed_by_scope_guard(self):
        changed = self.source.replace('"🇺🇸 <b>склад США:</b>"', '"UI-only mutation"', 1)
        self.assertNotEqual(changed, self.source)
        require_probnik_non_ui_unchanged(changed)
        # Card behavior is separately pinned by the exact-output tests above.

    def test_pricing_or_access_changes_are_rejected(self):
        for old, new in (("raw / 100.0", "raw / 50.0"),
                         ('os.getenv("PROBNIK_BOT_TOKEN", "")', 'os.getenv("OTHER_TOKEN", "")'),
                         ('def _compose(oem: str, info: dict, rows: list[dict])',
                          'def _compose(oem: str, info: dict, rows: list[dict], extra=None)')):
            with self.subTest(mutation=old):
                changed = self.source.replace(old, new, 1)
                self.assertNotEqual(changed, self.source)
                with self.assertRaisesRegex(AssertionError, "outside _compose body"):
                    require_probnik_non_ui_unchanged(changed)


if __name__ == "__main__":
    unittest.main()
