import unittest
from datetime import datetime,timezone
from polaris_dealer_cart_session_adapter import read_existing_cart
from test_polaris_cart_dom_layer_a import html,row
NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
def run(document=None,**changes):
    doc=dict(url_before="https://www.dealercostparts.com/cart",url_after="https://www.dealercostparts.com/cart",trusted_dealer_session=True,html=html(row()))
    if document: doc.update(document)
    session=dict(trusted_preflight=True,dealer_authenticated=True)
    category=dict(manufacturer="Polaris",oem="2206328",item_category="Parts",category_source_kind="validated_public_msrp",category_source_ref="trusted-catalog",category_verified_at=NOW.isoformat())
    session.update(changes.get("session",{}))
    category.update(changes.get("category",{}))
    return read_existing_cart(lambda:doc,requested_oem=changes.get("requested_oem","2206328"),category_evidence=category,session_evidence=session,observed_at=NOW.isoformat(),source_provenance="passive-read-only-capture",now=NOW)
class LayerCTests(unittest.TestCase):
    def reject(self,doc=None,**kw): self.assertEqual(run(doc,**kw)["status"],"DEALER_PRICE_UNVERIFIED")
    def test_01_exact_read_only(self):
        r=run()
        self.assertEqual(r["status"],"VALIDATED_DEALER_CART_CANDIDATE")
        self.assertIsNone(r["dealer_price_usd"])
        self.assertFalse(r["production_found"])
    def test_02_missing_document(self): self.reject({"html":None})
    def test_03_wrong_origin(self): self.reject({"url_before":"https://evil.invalid/cart","url_after":"https://evil.invalid/cart"})
    def test_04_not_cart(self): self.reject({"url_before":"https://www.dealercostparts.com/search","url_after":"https://www.dealercostparts.com/search"})
    def test_05_auth_required(self): self.reject(session={"dealer_authenticated":False})
    def test_06_cloudflare(self): self.reject({"challenged":True})
    def test_07_category_missing(self): self.reject(category={"category_source_ref":None})
    def test_08_wrong_manufacturer(self): self.reject(category={"manufacturer":"Honda"})
    def test_09_layer_a_reject(self): self.reject({"html":html(row(price="oops"))})
    def test_10_layer_b_reject(self): self.reject({"html":html(row(total="200.00"))})
    def test_11_context_changes(self): self.reject({"url_after":"https://www.dealercostparts.com/other"})
    def test_12_ambiguous_row(self): self.reject({"html":html(row(),row(token="another"))})
    def test_13_missing_sku(self): self.reject({"html":html(row(sku="9999999"))})
    def test_14_wrong_page_auth(self): self.reject({"trusted_dealer_session":False})
    def test_15_no_snapshot_reader(self):
        r=read_existing_cart(None,requested_oem="2206328",category_evidence={},session_evidence={},observed_at=NOW.isoformat(),source_provenance="x",now=NOW)
        self.assertEqual(r["status"],"DEALER_PRICE_UNVERIFIED")
    def test_16_qty_attribute_independent_mismatch(self):
        # Visible qty is 1; total remains consistent with visible qty.
        # Change only the observed control attribute data-qty to 2.
        document=html(row()).replace('data-qty="1"','data-qty="2"')
        self.reject({"html":document})
        self.assertEqual(run({"html":document})["reason"],"cart_contract_rejected:qty_conflict")
if __name__=="__main__": unittest.main()
