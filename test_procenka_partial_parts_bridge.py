# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


class ProcenkaPartialPartsBridgeTests(unittest.TestCase):
    def test_partial_parts_are_allowed_into_dp_enrichment(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")
        start = source.index("async def enrich_found_result_with_dealer_price")
        end = source.index("\ndef reserve_local_order_items", start)
        block = source[start:end]

        self.assertIn('status = str(result.get("status") or "").upper()', block)
        self.assertIn('partial_parts = status == "PARTIAL"', block)
        self.assertIn('resolved_item_type in {"part", "parts"}', block)
        self.assertIn('if status != "FOUND" and not partial_parts:', block)
        self.assertIn('or partial_parts', block)
        self.assertIn("dp_live_bridge.request_live_dp", block)

    def test_other_partial_results_still_return_early(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")
        start = source.index("async def enrich_found_result_with_dealer_price")
        end = source.index("\ndef reserve_local_order_items", start)
        block = source[start:end]
        self.assertIn('if status != "FOUND" and not partial_parts:\n        return result', block)


if __name__ == "__main__":
    unittest.main()
