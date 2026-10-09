# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


APPROVED = "* - в цену не входит стоимость доставки из штатов 🚚"


class ClientDeliveryTextUnifiedTests(unittest.TestCase):
    def test_procenka_customer_surfaces_use_approved_text(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")

        card_start = source.index("def format_client_offer_card")
        card_end = source.index("\ndef format_result", card_start)
        self.assertIn(APPROVED, source[card_start:card_end])

        intro_start = source.index("def format_customer_delivery_intro")
        intro_end = source.index("\ndef format_customer_delivery_details", intro_start)
        self.assertIn(APPROVED, source[intro_start:intro_end])

        details_start = source.index("def format_customer_delivery_details")
        details_end = source.index("\ndef format_admin_delivery_terms", details_start)
        self.assertIn(APPROVED, source[details_start:details_end])

        checkout_start = source.index("def format_checkout_delivery_choices")
        checkout_end = source.index("\ndef ", checkout_start + 4)
        self.assertIn(APPROVED, source[checkout_start:checkout_end])

        self.assertIn('replace(" ₽", "* ₽")', source[card_start:card_end])

    def test_probnik_has_no_legacy_delivery_phrases(self):
        source = Path(__file__).with_name("probnik_app.py").read_text(encoding="utf-8")
        self.assertIn("в цену не входит стоимость доставки из штатов", source)
        self.assertNotIn("Доставка в РФ оплачивается отдельно", source)
        self.assertNotIn("Доставка из США не входит в стоимость товаров", source)

    def test_web_uses_approved_text_and_starred_usa_prices(self):
        py = Path(__file__).with_name("web_app.py").read_text(encoding="utf-8")
        js = (Path(__file__).with_name("web") / "app.js").read_text(encoding="utf-8")
        self.assertIn(APPROVED, py)
        self.assertNotIn("Доставка из США в указанную стоимость не входит", py)
        self.assertIn('replace(" ₽", "* ₽")', js)
        self.assertIn('card.delivery_notice?.startsWith("* -")', js)


if __name__ == "__main__":
    unittest.main()
