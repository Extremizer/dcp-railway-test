import unittest
from unittest.mock import patch
from dp_sync_agent import _validated_result
from polaris_dealer_cart_completion_contract import SOURCE
ITEM={"manufacturer":"Polaris","oem":"2205250","resolved_oem":"2206328","item_category":"Parts","category_source_kind":"validated_public_msrp","category_source_ref":"fixture","category_verified_at":"2026-10-17T10:00:00+00:00"}
def result():
 return {"status":"FOUND","manufacturer":"Polaris","query_oem":"2205250","matched_sku":"2206328","current_oem":"2206328","dealer_price_usd":184.07,"currency":"USD","source":SOURCE,"dealer_cart_proof":{"dealer_authenticated":True,"dealer_context_verified":True,"trusted_preflight":True,"cart_restored":True,"production_found":False,"dealer_price_usd":None,"status":"ADMIN_PROBE_CART_PRICE_VERIFIED","requested_oem":"2205250","resolved_oem":"2206328","matched_sku":"2206328","currency":"USD","quantity":1,"unit_price_usd":184.07,"subtotal_usd":184.07,"dealer_identity_proof_ref":"synthetic","category_source_kind":"validated_public_msrp","category_source_ref":"fixture","category_verified_at":"2026-10-17T10:00:00+00:00"}}
class ProducerTests(unittest.TestCase):
 def test_positive_preserves_proof(self):
  r=_validated_result(ITEM,result())
  self.assertEqual(r["status"],"FOUND")
  self.assertEqual(r["dealer_cart_proof"]["matched_sku"],"2206328")
 def test_reject_msrp(self):
  r=result();r["source"]="msrp";r["dealer_price_usd"]=239.99
  self.assertNotEqual(_validated_result(ITEM,r)["status"],"FOUND")
 def test_reject_unrestored(self):
  r=result();r["dealer_cart_proof"]["cart_restored"]=False
  self.assertNotEqual(_validated_result(ITEM,r)["status"],"FOUND")
 def test_reject_wrong_identity(self):
  r=result();r["dealer_cart_proof"]["dealer_context_verified"]=False
  self.assertNotEqual(_validated_result(ITEM,r)["status"],"FOUND")
 def test_reject_category(self):
  a=dict(ITEM,item_category="UNKNOWN")
  self.assertNotEqual(_validated_result(a,result())["status"],"FOUND")
 def test_reject_missing_proof(self):
  r=result();r.pop("dealer_cart_proof")
  self.assertNotEqual(_validated_result(ITEM,r)["status"],"FOUND")
if __name__=="__main__":unittest.main()
