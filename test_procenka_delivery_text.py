# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


class ProcenkaDeliveryTextTests(unittest.TestCase):
    def test_customer_offer_uses_star_delivery_marker(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")
        start = source.index("def format_client_offer_card")
        end = source.index("\ndef format_result", start)
        block = source[start:end]
        self.assertIn('replace(" ₽", "* ₽")', block)
        self.assertIn('* - в цену не входит стоимость доставки из штатов 🚚', block)
        self.assertNotIn('🚚 Доставка из США оплачивается отдельно.', block)


if __name__ == "__main__":
    unittest.main()
