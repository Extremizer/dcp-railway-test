"""Stage 2 runtime-boundary safety regressions; no browser, DB or LIVE DCP."""
import json
import unittest
from pathlib import Path
from dcp_category_safety import evaluate

ROOT=Path(__file__).resolve().parent
def source(name): return (ROOT/name).read_text(encoding="utf-8")
CASES=json.loads(source("DCP_THEME1_MOCK_FIXTURES_STAGE2_20261015.json"))["cases"]

class RuntimeGuardTests(unittest.TestCase):
    def test_original_fixtures_are_unmodified(self):
        self.assertEqual(len(CASES),16)
        p=next(c for c in CASES if c["id"]=="POLARIS_PARTS_2206328")
        self.assertEqual((p["manufacturer"],p["category"],p["search"]["price_usd"],p["cart"]["price_usd"]),
                         ("Polaris","Parts","239.99","184.07"))
        self.assertIsNone(p["matched_sku"])
        self.assertTrue(p["expected"]["require_exact_sku_for_live_promotion"])

    def test_request_boundary_rejects_auto(self):
        import dp_live_bridge
        for m in ("__AUTO__","AUTO","UNKNOWN","","MadeUpBrand"):
            self.assertEqual(dp_live_bridge._request_key(m,"2206328"),("",""))

    def test_producer_no_auto_dispatch(self):
        src=source("dp_sync_agent.py")
        fn=src.split("def run_once()",1)[1].split("def main()",1)[0]
        self.assertNotIn("_auto_identity_price(",fn)
        self.assertIn('oemixibot_dcp_price.get_dealer_price_dict(item["manufacturer"], item["oem"], category_evidence=item)',fn)
        claim=src.split("def _claim()",1)[1].split("def _validated_result",1)[0]
        self.assertNotIn('"__AUTO__"',claim)

    def test_completion_no_auto_identity_and_no_replacement_cache(self):
        fn=source("dp_result_completion.py").split("def complete_result(",1)[1]
        self.assertIn("manufacturer != requested_manufacturer",fn)
        self.assertIn("matched_sku != requested_oem or current_oem != requested_oem",fn)
        self.assertNotIn('_write_identity(conn, manufacturer, requested_oem, current_oem, name, source, now)',fn)

    def test_adapter_denies_unverified_route_before_legacy_parser(self):
        src=source("oemixibot_dcp_price.py")
        entry=src.split("def get_dealer_price(",1)[1].split("def _legacy_get_dealer_price_unverified_route",1)[0]
        self.assertIn('"CATEGORY_UNVERIFIED"',entry)
        self.assertIn('"DEALER_PRICE_UNVERIFIED"',entry)
        self.assertNotIn("_parts_slug(",entry)
        self.assertNotIn("_runtime_evaluate(",entry)

    def test_price_gate_never_promotes_msrp_or_unknown_source(self):
        b=dict(manufacturer="Polaris",manufacturer_verified=True,category="Parts",category_verified=True,
            requested_oem="2206328",matched_sku="2206328",price_usd=239.99)
        self.assertIsNone(evaluate(dict(b,price_kind="MSRP",msrp_source_verified=True)).dealer_usd)
        self.assertEqual(evaluate(dict(b,price_kind="UNKNOWN")).status,"PRICE_KIND_UNKNOWN")
        self.assertEqual(evaluate(dict(b,price_kind="DEALER")).status,"DEALER_PRICE_UNVERIFIED")

if __name__=="__main__": unittest.main()
