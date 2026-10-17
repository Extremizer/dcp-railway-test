import unittest
from polaris_dealer_cart_completion_contract import verify_polaris_cart_proof,CartProofError,SOURCE
AUTH={"item_category":"Parts","category_source_kind":"validated_public_msrp","category_source_ref":"synthetic","category_verified_at":"2026-10-17T00:00:00+00:00"}
def result(**changes):
    d={"manufacturer":"Polaris","query_oem":"2205250","current_oem":"2206328","matched_sku":"2206328",
       "source":SOURCE,"currency":"USD","dealer_price_usd":184.07,
       "dealer_cart_proof":{"status":"ADMIN_PROBE_CART_PRICE_VERIFIED","dealer_authenticated":True,
       "dealer_context_verified":True,"trusted_preflight":True,"cart_restored":True,
       "production_found":False,"dealer_price_usd":None,
       "requested_oem":"2205250","resolved_oem":"2206328","matched_sku":"2206328",
       "currency":"USD","quantity":1,"unit_price_usd":184.07,"subtotal_usd":184.07,
       "dealer_identity_proof_ref":"synthetic-proof",**{k:AUTH[k] for k in ("category_source_kind","category_source_ref","category_verified_at")}}}
    d.update(changes)
    return d
class ContractTests(unittest.TestCase):
    def good(self,d):return verify_polaris_cart_proof(d,requested_oem="2205250",resolved_oem="2206328",category_evidence=AUTH)
    def test_verified_alias(self):self.assertEqual(self.good(result()),184.07)
    def test_wrong_identity(self):
        p=result();p["dealer_cart_proof"]["dealer_context_verified"]=False
        with self.assertRaises(CartProofError):self.good(p)
    def test_unrestored(self):
        p=result();p["dealer_cart_proof"]["cart_restored"]=False
        with self.assertRaises(CartProofError):self.good(p)
    def test_msrp_rejected(self):
        with self.assertRaises(CartProofError):self.good(result(source="msrp"))
    def test_client_price_rejected(self):
        with self.assertRaises(CartProofError):self.good(result(dealer_price_usd=209.99))
    def test_wrong_sku(self):
        with self.assertRaises(CartProofError):self.good(result(matched_sku="2205250"))
    def test_unknown_category(self):
        with self.assertRaises(CartProofError):verify_polaris_cart_proof(result(),requested_oem="2205250",resolved_oem="2206328",category_evidence={"item_category":"UNKNOWN"})
    def test_missing_identity_reference(self):
        p=result();p["dealer_cart_proof"]["dealer_identity_proof_ref"]=""
        with self.assertRaises(CartProofError):self.good(p)
    def test_global_found_disallowed(self):
        p=result();p["dealer_cart_proof"]["production_found"]=True
        with self.assertRaises(CartProofError):self.good(p)
    def test_no_evidence(self):
        with self.assertRaises(CartProofError):self.good(result(dealer_cart_proof=None))
if __name__=="__main__":unittest.main()
