import unittest
from polaris_dp_verified_readonly_producer import inspect_claim
from polaris_dealer_cart_completion_contract import verify_polaris_cart_proof,SOURCE

ITEM={"manufacturer":"Polaris","oem":"2205250","resolved_oem":"2206328","item_category":"Parts",
      "category_source_kind":"validated_public_msrp","category_source_ref":"fixture-authority",
      "category_verified_at":"2026-10-17T10:00:00+00:00"}
def identity(ok=True):
    return {"status":"DEALER_IDENTITY_VERIFIED" if ok else "DEALER_IDENTITY_MISMATCH",
            "dealer_context_verified":ok}
def candidate(**changes):
    r={"status":"VALIDATED_DEALER_CART_CANDIDATE","manufacturer":"Polaris",
       "item_category":"Parts","requested_oem":"2206328","matched_sku":"2206328",
       "current_oem":"2206328","quantity":1,"currency":"USD",
       "unit_price_usd":"184.07","dealer_price_usd":None,"production_found":False}
    r.update(changes)
    return r
class ReadOnlyProducerTests(unittest.TestCase):
    def invoke(self,cart=None,checker=None,item=None):
        observed=[]
        def inspect(**kw):
            observed.append(kw)
            return candidate() if cart is None else cart
        r=inspect_claim(item or ITEM,identity=checker or (lambda:identity()),inspect=inspect)
        return r,observed
    def test_positive_alias_proof(self):
        r,called=self.invoke()
        self.assertEqual(r["status"],"FOUND")
        self.assertEqual(r["dealer_price_usd"],184.07)
        self.assertEqual(called[0]["requested_oem"],"2206328")
        self.assertEqual(r["query_oem"],"2205250")
        self.assertEqual(r["source"],SOURCE)
        self.assertEqual(verify_polaris_cart_proof(r,requested_oem="2205250",resolved_oem="2206328",
            category_evidence={**ITEM}),184.07)
    def test_identity_denied_without_cart(self):
        r,calls=self.invoke(checker=lambda:identity(False))
        self.assertNotEqual(r["status"],"FOUND")
        self.assertEqual(calls,[])
    def test_identity_lost_after_cart(self):
        states=iter([identity(),identity(False)])
        r,_=self.invoke(checker=lambda:next(states))
        self.assertNotEqual(r["status"],"FOUND")
    def test_wrong_sku(self):
        r,_=self.invoke(cart=candidate(matched_sku="2205250"))
        self.assertNotEqual(r["status"],"FOUND")
    def test_wrong_category(self):
        r,_=self.invoke(item={**ITEM,"item_category":"UNKNOWN"})
        self.assertNotEqual(r["status"],"FOUND")
    def test_no_alias_authority(self):
        r,_=self.invoke(item={**ITEM,"resolved_oem":None})
        self.assertNotEqual(r["status"],"FOUND")
    def test_quantity_two(self):
        r,_=self.invoke(cart=candidate(quantity=2))
        self.assertNotEqual(r["status"],"FOUND")
    def test_msrp_source_not_cart(self):
        r,_=self.invoke(cart={"status":"FOUND","msrp_usd":239.99})
        self.assertNotEqual(r["status"],"FOUND")
    def test_client_price_not_cart(self):
        r,_=self.invoke(cart=candidate(unit_price_usd="209.99",status="CLIENT_CART"))
        self.assertNotEqual(r["status"],"FOUND")
    def test_context_missing(self):
        r,_=self.invoke(cart=candidate(dealer_price_usd=184.07))
        self.assertNotEqual(r["status"],"FOUND")
    def test_no_real_account_identity_output(self):
        r,_=self.invoke()
        self.assertNotIn("real-account-name",str(r))
if __name__=="__main__":unittest.main()
