from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
import unittest

from unified_price_contract_v1 import (
    VERSION,
    STATUS_VERIFIED,
    STATUS_MISSING,
    STATUS_INVALID,
    STATUS_STALE,
    STATUS_SOURCE_UNVERIFIED,
    STATUS_IDENTITY_MISMATCH,
    build_contract,
    from_gateway_and_msrp,
    dealer_legacy_offer,
    client_price_inputs,
    admin_price_view,
    CanonicalPriceGatewayAdapter,
)


NOW = datetime(2026, 10, 15, 10, 0, tzinfo=timezone.utc)
FRESH = (NOW - timedelta(minutes=5)).isoformat()
STALE = (NOW - timedelta(days=10)).isoformat()


class CanonicalPriceContractTests(unittest.TestCase):
    def build(self, **kw):
        base = dict(
            manufacturer="Polaris",
            requested_oem="2206328",
            matched_oem="2206328",
            item_category="parts",
            dealer_price_usd=184.07,
            dealer_price_source="owner_evidence:polaris_cart",
            dealer_verified_at=FRESH,
            msrp_usd=239.99,
            msrp_source="owner_evidence:polaris_search",
            msrp_verified_at=FRESH,
            dealer_max_age_hours=24,
            msrp_max_age_hours=24,
            gateway_status="FOUND",
            expected_manufacturer="Polaris",
            expected_requested_oem="2206328",
            now=NOW,
        )
        base.update(kw)
        return build_contract(**base)

    def test_polaris_parts_keeps_dealer_and_msrp_independent(self):
        c = self.build()
        self.assertEqual(c["version"], VERSION)
        self.assertEqual(c["dealer_price_usd"], 184.07)
        self.assertEqual(c["msrp_usd"], 239.99)
        self.assertEqual(c["dealer_price_status"], STATUS_VERIFIED)
        self.assertEqual(c["msrp_status"], STATUS_VERIFIED)
        self.assertNotEqual(c["dealer_price_source"], c["msrp_source"])

    def test_polaris_accessories_msrp_only_never_becomes_dealer_found(self):
        c = self.build(
            requested_oem="2884109",
            matched_oem="2884109",
            item_category="accessories",
            expected_requested_oem="2884109",
            dealer_price_usd=None,
            dealer_price_source=None,
            dealer_verified_at=None,
            msrp_usd=10.99,
            msrp_source="owner_evidence:polaris_details",
        )
        self.assertEqual(c["dealer_price_status"], STATUS_MISSING)
        self.assertEqual(c["msrp_status"], STATUS_VERIFIED)
        legacy = dealer_legacy_offer(c)
        self.assertNotEqual(legacy["status"], "FOUND")
        self.assertIsNone(legacy["dealer_price_usd"])
        self.assertEqual(legacy["msrp_usd"], 10.99)
        client = client_price_inputs(c)
        self.assertIsNone(client["dealer_price_usd"])
        self.assertEqual(client["msrp_usd"], 10.99)

    def test_arctic_cat_dealer_only_preserved(self):
        c = build_contract(
            manufacturer="Arctic Cat",
            requested_oem="0627-048",
            matched_oem="0627-048",
            item_category=None,
            dealer_price_usd=85.51,
            dealer_price_source="owner_evidence:arctic_cat_cart",
            dealer_verified_at=FRESH,
            msrp_usd=None,
            msrp_source=None,
            msrp_verified_at=None,
            dealer_max_age_hours=24,
            msrp_max_age_hours=24,
            gateway_status="FOUND",
            expected_manufacturer="Arctic Cat",
            expected_requested_oem="0627-048",
            now=NOW,
        )
        self.assertEqual(c["dealer_price_status"], STATUS_VERIFIED)
        self.assertEqual(c["msrp_status"], STATUS_MISSING)
        self.assertEqual(dealer_legacy_offer(c)["dealer_price_usd"], 85.51)
        self.assertIsNone(client_price_inputs(c)["msrp_usd"])

    def test_both_missing(self):
        c = self.build(
            dealer_price_usd=None,
            dealer_price_source=None,
            dealer_verified_at=None,
            msrp_usd=None,
            msrp_source=None,
            msrp_verified_at=None,
        )
        self.assertEqual(c["dealer_price_status"], STATUS_MISSING)
        self.assertEqual(c["msrp_status"], STATUS_MISSING)
        self.assertIsNone(client_price_inputs(c)["dealer_price_usd"])
        self.assertIsNone(client_price_inputs(c)["msrp_usd"])

    def test_manufacturer_mismatch_invalidates_both(self):
        c = self.build(manufacturer="Honda")
        self.assertEqual(c["dealer_price_status"], STATUS_IDENTITY_MISMATCH)
        self.assertEqual(c["msrp_status"], STATUS_IDENTITY_MISMATCH)
        self.assertIsNone(c["dealer_price_usd"])
        self.assertIsNone(c["msrp_usd"])

    def test_requested_oem_mismatch_invalidates_both(self):
        c = self.build(requested_oem="WRONG")
        self.assertEqual(c["dealer_price_status"], STATUS_IDENTITY_MISMATCH)
        self.assertEqual(c["msrp_status"], STATUS_IDENTITY_MISMATCH)

    def test_mismatched_matched_skus_fail_closed_when_combining(self):
        c = from_gateway_and_msrp(
            manufacturer="Polaris",
            requested_oem="2206328",
            item_category="parts",
            dealer_result={
                "status": "FOUND",
                "manufacturer": "Polaris",
                "current_oem": "2206328",
                "dealer_price_usd": 184.07,
                "source": "verified_local_dcp:cart_line",
                "last_verified_at": FRESH,
            },
            msrp_result={
                "manufacturer": "Polaris",
                "current_oem": "2206329",
                "msrp_usd": 239.99,
                "source": "verified_dcp_msrp:search_card",
                "last_verified_at": FRESH,
            },
            dealer_max_age_hours=24,
            msrp_max_age_hours=24,
            now=NOW,
        )
        self.assertEqual(c["dealer_price_status"], STATUS_IDENTITY_MISMATCH)
        self.assertEqual(c["msrp_status"], STATUS_IDENTITY_MISMATCH)
        self.assertIsNone(c["dealer_price_usd"])
        self.assertIsNone(c["msrp_usd"])

    def test_stale_price_is_not_usable(self):
        c = self.build(dealer_verified_at=STALE)
        self.assertEqual(c["dealer_price_status"], STATUS_STALE)
        self.assertIsNone(c["dealer_price_usd"])
        self.assertEqual(c["msrp_status"], STATUS_VERIFIED)

    def test_no_default_freshness_policy(self):
        c = self.build(dealer_max_age_hours=None, msrp_max_age_hours=None)
        self.assertIsNone(c["dealer_price_usd"])
        self.assertIsNone(c["msrp_usd"])
        self.assertNotEqual(c["dealer_price_status"], STATUS_VERIFIED)
        self.assertNotEqual(c["msrp_status"], STATUS_VERIFIED)

    def test_invalid_numeric_values_never_usable(self):
        for value in (0, -1, float("nan"), float("inf"), True, "184.07"):
            with self.subTest(value=value):
                c = self.build(dealer_price_usd=value)
                self.assertEqual(c["dealer_price_status"], STATUS_INVALID)
                self.assertIsNone(c["dealer_price_usd"])

    def test_wrong_currency_is_rejected_by_gateway_adapter_before_contract(self):
        # Contract consumes USD fields only; a legacy/shared result that declares
        # RUB must not be promoted by a compatibility adapter.
        dealer = {
            "status": "FOUND",
            "manufacturer": "Polaris",
            "current_oem": "2206328",
            "dealer_price_usd": None,
            "currency": "RUB",
            "source": "verified_local_dcp:cart_line",
            "last_verified_at": FRESH,
        }
        c = from_gateway_and_msrp(
            manufacturer="Polaris",
            requested_oem="2206328",
            item_category="parts",
            dealer_result=dealer,
            msrp_result=None,
            dealer_max_age_hours=24,
            msrp_max_age_hours=24,
            now=NOW,
        )
        self.assertNotEqual(c["dealer_price_status"], STATUS_VERIFIED)
        self.assertIsNone(c["dealer_price_usd"])

    def test_unverified_sources_fail_closed(self):
        for field, source in (
            ("dealer", "public_search:$239.99"),
            ("msrp", "random_html:$239.99"),
        ):
            with self.subTest(field=field):
                kw = {}
                if field == "dealer":
                    kw["dealer_price_source"] = source
                else:
                    kw["msrp_source"] = source
                c = self.build(**kw)
                status = c["dealer_price_status"] if field == "dealer" else c["msrp_status"]
                self.assertEqual(status, STATUS_SOURCE_UNVERIFIED)

    def test_gateway_failure_status_never_creates_dealer_found(self):
        for status in (
            "CLOUDFLARE",
            "AUTH_REQUIRED",
            "RATE_LIMITED",
            "TECHNICAL_ERROR",
            "TIMEOUT",
            "ADMISSION_CLOSED",
        ):
            with self.subTest(status=status):
                c = from_gateway_and_msrp(
                    manufacturer="Polaris",
                    requested_oem="2206328",
                    item_category="parts",
                    dealer_result={"status": status},
                    msrp_result=None,
                    dealer_max_age_hours=24,
                    msrp_max_age_hours=24,
                    now=NOW,
                )
                legacy = dealer_legacy_offer(c)
                self.assertEqual(legacy["status"], status)
                self.assertIsNone(legacy["dealer_price_usd"])

    def test_admin_view_exposes_each_status_separately(self):
        c = self.build()
        view = admin_price_view(c)
        self.assertEqual(view["dealer_price_usd"], 184.07)
        self.assertEqual(view["msrp_usd"], 239.99)
        self.assertEqual(view["dealer_price_status"], STATUS_VERIFIED)
        self.assertEqual(view["msrp_status"], STATUS_VERIFIED)

    def test_canonical_gateway_composes_existing_dealer_and_read_only_msrp(self):
        calls = []
        live_fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        def dealer(m, o, *, max_age_hours):
            calls.append(("dealer", m, o, max_age_hours))
            return {
                "status": "FOUND",
                "manufacturer": m,
                "current_oem": o,
                "dealer_price_usd": 184.07,
                "source": "verified_local_dcp:cart_line",
                "last_verified_at": live_fresh,
            }
        def msrp(m, o, *, max_age_hours):
            calls.append(("msrp", m, o, max_age_hours))
            return {
                "manufacturer": m,
                "current_oem": o,
                "msrp_usd": 239.99,
                "source": "verified_dcp_msrp:search_card",
                "last_verified_at": live_fresh,
            }
        adapter = CanonicalPriceGatewayAdapter(
            dealer, msrp, dealer_max_age_hours=24, msrp_max_age_hours=48
        )
        out = adapter.resolve("Polaris", "2206328", item_category="parts")
        self.assertEqual(out["dealer_price_usd"], 184.07)
        self.assertEqual(out["msrp_usd"], 239.99)
        self.assertEqual(
            calls,
            [
                ("dealer", "Polaris", "2206328", 24.0),
                ("msrp", "Polaris", "2206328", 48.0),
            ],
        )

    def test_gateway_failure_does_not_fallback_to_msrp_for_dealer(self):
        live_fresh = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        def dealer(*a, **k):
            raise TimeoutError("gateway down")
        def msrp(m, o, *, max_age_hours):
            return {
                "manufacturer": m,
                "current_oem": o,
                "msrp_usd": 10.99,
                "source": "verified_dcp_msrp:details",
                "last_verified_at": live_fresh,
            }
        adapter = CanonicalPriceGatewayAdapter(
            dealer, msrp, dealer_max_age_hours=24, msrp_max_age_hours=24
        )
        out = adapter.resolve("Polaris", "2884109", item_category="accessories")
        self.assertIsNone(out["dealer_price_usd"])
        self.assertEqual(out["msrp_usd"], 10.99)
        self.assertEqual(out["gateway_status"], "TECHNICAL_ERROR")
        self.assertNotEqual(dealer_legacy_offer(out)["status"], "FOUND")


if __name__ == "__main__":
    unittest.main(verbosity=2)
