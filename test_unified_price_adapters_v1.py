from __future__ import annotations

from datetime import datetime, timezone
import unittest

from unified_price_contract_v1 import build_contract
from unified_price_adapters_v1 import (
    oemixi_offer_from_contract,
    procenka_raw_price_inputs,
    probnik_raw_price_inputs,
)

NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc).isoformat()


def contract(*, dealer=184.07, msrp=239.99):
    return build_contract(
        manufacturer="Polaris",
        requested_oem="2206328",
        matched_oem="2206328",
        item_category="parts",
        dealer_price_usd=dealer,
        dealer_price_source="owner_evidence:polaris_cart" if dealer is not None else None,
        dealer_verified_at=NOW if dealer is not None else None,
        msrp_usd=msrp,
        msrp_source="owner_evidence:polaris_search" if msrp is not None else None,
        msrp_verified_at=NOW if msrp is not None else None,
        dealer_max_age_hours=24,
        msrp_max_age_hours=24,
        gateway_status="FOUND",
        expected_manufacturer="Polaris",
        expected_requested_oem="2206328",
        now=datetime(2026,10,15,10,5,tzinfo=timezone.utc),
    )


class UnifiedPriceAdaptersTests(unittest.TestCase):
    def test_oemixi_uses_dealer_only_for_sell_input(self):
        out = oemixi_offer_from_contract(contract())
        self.assertEqual(out["status"], "FOUND")
        self.assertEqual(out["dl_usd"], 184.07)
        self.assertEqual(out["msrp_usd"], 239.99)
        self.assertNotEqual(out["dl_usd"], out["msrp_usd"])

    def test_oemixi_msrp_only_is_not_found_dealer_offer(self):
        out = oemixi_offer_from_contract(contract(dealer=None, msrp=10.99))
        self.assertNotEqual(out["status"], "FOUND")
        self.assertIsNone(out["dl_usd"])
        self.assertEqual(out["msrp_usd"], 10.99)

    def test_procenka_gets_raw_verified_fields_only(self):
        out = procenka_raw_price_inputs(contract())
        self.assertEqual(out["_dealer_price_usd"], 184.07)
        self.assertEqual(out["price"], 239.99)
        self.assertEqual(out["_dealer_price_status"], "FOUND")
        self.assertNotIn("customer_rub", out)
        self.assertNotIn("rrp_rub", out)
        self.assertNotIn("benefit", out)

    def test_procenka_msrp_only_has_no_dealer_input(self):
        out = procenka_raw_price_inputs(contract(dealer=None, msrp=10.99))
        self.assertIsNone(out["_dealer_price_usd"])
        self.assertEqual(out["price"], 10.99)
        self.assertNotEqual(out["_dealer_price_status"], "FOUND")

    def test_probnik_gets_raw_verified_fields_only(self):
        out = probnik_raw_price_inputs(contract())
        self.assertEqual(out["dp_usd"], 184.07)
        self.assertEqual(out["msrp_usd"], 239.99)
        self.assertNotIn("customer_rub", out)
        self.assertNotIn("rrp_rub", out)

    def test_probnik_dealer_only_keeps_msrp_empty(self):
        out = probnik_raw_price_inputs(contract(dealer=85.51, msrp=None))
        self.assertEqual(out["dp_usd"], 85.51)
        self.assertIsNone(out["msrp_usd"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
