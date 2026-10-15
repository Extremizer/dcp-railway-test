from __future__ import annotations

import hashlib
import json
from pathlib import Path
import unittest


FIXTURE = Path(__file__).with_name("DCP_THEME1_MOCK_FIXTURES_STAGE2_20261015.json")
EXPECTED_SHA256 = "826855d0abf072027f42c58cb8dd92b7e6d78733d2b08bfa6775fadf85256a0e"


class Theme5Stage2MockContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = FIXTURE.read_bytes()
        cls.payload = json.loads(raw.decode("utf-8"))
        cls.cases = {case["id"]: case for case in cls.payload["cases"]}
        cls.sha256 = hashlib.sha256(raw).hexdigest()

    def test_fixture_is_exact_supplied_stage2_payload(self):
        self.assertEqual(self.sha256, EXPECTED_SHA256)
        self.assertEqual(
            self.payload["purpose"],
            "test fixtures only; not live DCP evidence",
        )
        self.assertEqual(len(self.payload["cases"]), 16)
        self.assertEqual(len(self.cases), 16)

    def test_owner_screenshot_examples_are_not_live_route_verification(self):
        owner_observed = [
            self.cases["POLARIS_PARTS_2206328"],
            self.cases["POLARIS_ACCESSORY_2884109"],
            self.cases["ARCTIC_CAT_0627_048"],
        ]
        serialized = json.dumps(owner_observed, ensure_ascii=False)
        self.assertIn("OWNER_SCREENSHOT", serialized)
        self.assertNotIn("LIVE_VERIFIED", serialized)

    def test_accessory_unknown_subcategory_cannot_authorize_dealer_quote(self):
        case = self.cases["POLARIS_ACCESSORY_2884109"]
        self.assertIn("UNKNOWN", case["category"])
        self.assertIsNone(case["expected"]["dealer_usd"])
        self.assertTrue(case["expected"]["no_dealer_quote"])

    def test_identity_and_replacement_ambiguity_fail_closed(self):
        for case_id, expected_status in (
            ("SIMILAR_NAMES_DIFFERENT_SKU", "SKU_AMBIGUOUS"),
            ("REPLACED_OEM_UNVERIFIED", "REPLACEMENT_UNVERIFIED"),
            ("SAME_NUMBER_OTHER_MANUFACTURER", "UNKNOWN_MANUFACTURER"),
        ):
            with self.subTest(case_id=case_id):
                case = self.cases[case_id]
                self.assertEqual(case["expected_status"], expected_status)
                self.assertIsNone(case["expected_dealer_usd"])

    def test_cart_baseline_and_concurrency_fail_closed(self):
        baseline = self.cases["LONG_CART_NO_LINE_ID"]
        self.assertFalse(baseline["stable_line_identity"])
        self.assertEqual(
            baseline["expected_status"],
            "CART_BASELINE_UNVERIFIED",
        )
        self.assertIsNone(baseline["expected_dealer_usd"])

        conflict = self.cases["CONCURRENT_CART_PRODUCER"]
        self.assertFalse(conflict["cart_lock_acquired"])
        self.assertEqual(conflict["expected_status"], "CART_CONFLICT")
        self.assertEqual(conflict["expected"], "NO_MUTATION")

    def test_ambiguous_price_kind_cannot_become_dealer_price(self):
        case = self.cases["PRICE_KIND_AMBIGUOUS"]
        self.assertIsNone(case["label"])
        self.assertEqual(case["expected_status"], "PRICE_KIND_UNKNOWN")
        self.assertIsNone(case["expected_dealer_usd"])

    def test_owner_go_is_mandatory_for_cart_mutation(self):
        case = self.cases["NO_OWNER_GO"]
        self.assertTrue(case["cart_mutation_requested"])
        self.assertFalse(case["owner_go"])
        self.assertEqual(case["expected_status"], "OWNER_GO_REQUIRED")
        self.assertEqual(case["expected"], "NO_MUTATION")


if __name__ == "__main__":
    unittest.main(verbosity=2)
