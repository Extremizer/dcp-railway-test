# -*- coding: utf-8 -*-
from pathlib import Path
import unittest


class ProcenkaDPBridgeTests(unittest.TestCase):
    def test_cache_only_miss_routes_through_shared_bridge(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")
        self.assertIn("import dp_live_bridge", source)
        start = source.index("    if DCP_CACHE_ONLY:\n")
        end = source.index("\n    live_status = None\n", start)
        block = source[start:end]

        self.assertIn("get_dealer_price_cache(", block)
        self.assertIn("if cached and cached.get(\"fresh\"):", block)
        self.assertIn("dp_live_bridge.request_live_dp", block)
        self.assertIn("upsert_dealer_price_cache(", block)
        self.assertLess(
            block.index("if cached and cached.get(\"fresh\"):"),
            block.index("dp_live_bridge.request_live_dp"),
        )
        self.assertNotIn("get_dealer_price(", block)
        self.assertNotIn("public_msrp_cache_result(", block)


if __name__ == "__main__":
    unittest.main()
