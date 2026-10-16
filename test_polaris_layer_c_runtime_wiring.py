import unittest
from datetime import datetime,timezone
from polaris_cart_existing_session_wiring import inspect_existing_cart
from test_polaris_cart_dom_layer_a import html,row
NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
TAB={"type":"page","url":"https://www.dealercostparts.com/cart"}
CATEGORY=dict(manufacturer="Polaris",oem="2206328",item_category="Parts",category_source_kind="validated_public_msrp",category_source_ref="verified",category_verified_at=NOW.isoformat())
SESSION=dict(trusted_preflight=True,dealer_authenticated=True,dealer_context_verified=True)
def probe(**kw):
    doc=dict(url_before=TAB["url"],url_after=TAB["url"],html=html(row()),context_changed=False)
    doc.update(kw.get("document",{}))
    return inspect_existing_cart(requested_oem="2206328",category_evidence=kw.get("category",CATEGORY),session_evidence=kw.get("session",SESSION),observed_at=NOW.isoformat(),now=NOW,tabs=lambda:kw.get("tabs",[TAB]),evaluate=lambda tab,expression:doc)
class WiringTests(unittest.TestCase):
    def test_01_valid_candidate_internal(self):
        r=probe()
        self.assertEqual(r["status"],"VALIDATED_DEALER_CART_CANDIDATE")
        self.assertIsNone(r["dealer_price_usd"])
        self.assertFalse(r["production_found"])
    def test_02_missing_dealer_proof(self):
        self.assertEqual(probe(session={**SESSION,"dealer_context_verified":False})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_03_missing_preflight(self):
        self.assertEqual(probe(session={**SESSION,"trusted_preflight":False})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_04_wrong_origin(self): self.assertEqual(probe(tabs=[{**TAB,"url":"https://example.org/cart"}])["status"],"DEALER_PRICE_UNVERIFIED")
    def test_05_no_cart(self): self.assertEqual(probe(tabs=[])["status"],"DEALER_PRICE_UNVERIFIED")
    def test_06_two_tabs(self): self.assertEqual(probe(tabs=[TAB,TAB])["status"],"DEALER_PRICE_UNVERIFIED")
    def test_07_document_changed(self): self.assertEqual(probe(document={"url_after":"https://www.dealercostparts.com/other"})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_08_challenge(self): self.assertEqual(probe(session={**SESSION,"challenged":True})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_09_missing_category(self): self.assertEqual(probe(category={**CATEGORY,"category_source_ref":None})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_10_wrong_manufacturer(self): self.assertEqual(probe(category={**CATEGORY,"manufacturer":"Honda"})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_11_wrong_page(self): self.assertEqual(probe(tabs=[{**TAB,"url":"https://www.dealercostparts.com/search"}])["status"],"DEALER_PRICE_UNVERIFIED")
    def test_12_bad_html(self): self.assertEqual(probe(document={"html":"bad"})["status"],"DEALER_PRICE_UNVERIFIED")
if __name__=="__main__":unittest.main()
