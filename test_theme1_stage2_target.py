import json
import unittest
from pathlib import Path
from dcp_category_safety import evaluate, allow_cart_mutation

FIXTURES=json.loads(Path(__file__).with_name("DCP_THEME1_MOCK_FIXTURES_STAGE2_20261015.json").read_text())["cases"]
EXPECTED={
"POLARIS_PARTS_2206328":"NO_EXACT_SKU",
"POLARIS_ACCESSORY_2884109":"CATEGORY_UNVERIFIED",
"ARCTIC_CAT_0627_048":"CATEGORY_UNVERIFIED",
"SIMILAR_NAMES_DIFFERENT_SKU":"SKU_AMBIGUOUS",
"REPLACED_OEM_UNVERIFIED":"REPLACEMENT_UNVERIFIED",
"SAME_NUMBER_OTHER_MANUFACTURER":"UNKNOWN_MANUFACTURER",
"LONG_CART_NO_LINE_ID":"CART_BASELINE_UNVERIFIED",
"PRICE_MISSING":"DEALER_PRICE_UNVERIFIED",
"CLOUDFLARE":"CLOUDFLARE",
"AUTH_REQUIRED":"AUTH_REQUIRED",
"HTTP_429":"HTTP_429",
"HTTP_5XX":"HTTP_5XX",
"SKU_MISMATCH":"NO_EXACT_SKU",
"PRICE_KIND_AMBIGUOUS":"PRICE_KIND_UNKNOWN",
"NO_OWNER_GO":"OWNER_GO_REQUIRED",
"CONCURRENT_CART_PRODUCER":"CART_CONFLICT",
}
def input_for(case):
    id=case["id"]
    ev={"manufacturer":"Polaris","manufacturer_verified":True,"category":"Parts",
        "category_verified":True,"requested_oem":"SKU-A","matched_sku":"SKU-A",
        "price_kind":"DEALER","price_usd":100,"dealer_source_verified":True,
        "line_verified":True}
    if id=="POLARIS_PARTS_2206328": ev.update(requested_oem="2206328",matched_sku=None)
    elif id=="POLARIS_ACCESSORY_2884109": ev.update(requested_oem="2884109",category_verified=False)
    elif id=="ARCTIC_CAT_0627_048": ev.update(manufacturer="Arctic Cat",requested_oem="0627-048",category_verified=False)
    elif id=="SIMILAR_NAMES_DIFFERENT_SKU": ev.update(matched_sku=None,results=case["results"])
    elif id=="REPLACED_OEM_UNVERIFIED": ev.update(requested_oem="OLD-A",matched_sku="OLD-A",current_oem="NEW-B",replacement_chain_verified=False)
    elif id=="SAME_NUMBER_OTHER_MANUFACTURER": ev.update(manufacturer="",manufacturer_verified=False)
    elif id=="LONG_CART_NO_LINE_ID": ev.update(cart_mutation_requested=True,owner_go=True,baseline_verified=False)
    elif id=="PRICE_MISSING": ev.update(price_usd=None)
    elif id in ("CLOUDFLARE","AUTH_REQUIRED"): ev.update(provider_status=id)
    elif id=="HTTP_429": ev.update(http_status=429)
    elif id=="HTTP_5XX": ev.update(http_status=503)
    elif id=="SKU_MISMATCH": ev.update(matched_sku="SKU-B")
    elif id=="PRICE_KIND_AMBIGUOUS": ev.update(price_kind="UNKNOWN")
    elif id=="NO_OWNER_GO": ev.update(cart_mutation_requested=True,owner_go=False)
    elif id=="CONCURRENT_CART_PRODUCER": ev.update(cart_mutation_requested=True,owner_go=True,baseline_verified=True,stable_line_identity=True,cart_lock_acquired=False)
    return ev

class Stage2TargetTests(unittest.TestCase):
    def test_all_16_approved_mock_fixtures_against_gate(self):
        self.assertEqual(len(FIXTURES),16)
        self.assertEqual(len(EXPECTED),16)
        for case in FIXTURES:
            with self.subTest(case=case["id"]):
                result=evaluate(input_for(case))
                self.assertEqual(result.status, EXPECTED[case["id"]])
                self.assertIsNone(result.dealer_usd)
                self.assertFalse(result.mutation)

    def test_known_verified_route_and_provenance(self):
        self.assertEqual(evaluate(dict(manufacturer="Polaris",manufacturer_verified=True,
            category="Parts",category_verified=True,requested_oem="2206328",
            matched_sku="2206328",price_kind="DEALER",price_usd=184.07,
            dealer_source_verified=True,line_verified=True)).dealer_usd,184.07)
        self.assertEqual(evaluate(dict(manufacturer="Polaris",manufacturer_verified=True,
            category="Parts",category_verified=True,requested_oem="2206328",
            matched_sku="2206328",price_kind="MSRP",price_usd=239.99,
            msrp_source_verified=True)).status,"MSRP_ONLY")

    def test_guardrails(self):
        base=dict(manufacturer="Polaris",manufacturer_verified=True,category="Parts",category_verified=True,
            requested_oem="2206328",matched_sku="2206328",price_kind="DEALER",
            price_usd=184.07,dealer_source_verified=True,line_verified=True)
        for change, status in [
            (dict(manufacturer="__AUTO__"),"UNKNOWN_MANUFACTURER"),
            (dict(manufacturer_verified=False),"UNKNOWN_MANUFACTURER"),
            (dict(category_verified=False),"CATEGORY_UNVERIFIED"),
            (dict(dealer_source_verified=False),"DEALER_PRICE_UNVERIFIED"),
            (dict(current_oem="999",replacement_chain_verified=False),"REPLACEMENT_UNVERIFIED"),
        ]:
            with self.subTest(change=change):
                self.assertEqual(evaluate(dict(base,**change)).status,status)
        self.assertFalse(allow_cart_mutation(owner_go=True,baseline_verified=True,stable_line_identity=True,cart_lock_acquired=True))
if __name__=="__main__":
    unittest.main()
