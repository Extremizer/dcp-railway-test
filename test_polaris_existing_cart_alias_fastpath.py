import tempfile,unittest
from pathlib import Path
from polaris_probe_cart_transaction import run_probe
from test_polaris_probe_cart_transaction import Fake,line
A={"manufacturer":"Polaris","requested_oem":"2205250","resolved_oem":"2206328","item_category":"Parts","exact_match":True,"replacement_proven":True}
class AliasExistingCart(unittest.TestCase):
 def run_case(self,cart,authority=A):
  f=Fake(cart)
  called=[]
  old=f.find_exact_oem
  def search(oem):
   called.append(oem)
   return {"manufacturer":"Polaris","requested_oem":oem,"resolved_oem":"2206328",
           "item_category":"Parts","exact_match":True,"replacement_proven":True}
  f.find_exact_oem=search
  with tempfile.TemporaryDirectory() as d:
   r=run_probe("2205250",f,lock_path=Path(d)/"lock",journal_path=Path(d)/"journal",
               authoritative_target=authority)
  return r,called,f.operations
 def test_proven_existing_alias_zero_search_or_mutation(self):
  r,called,ops=self.run_case([line("2206328","token",1,"184.07")])
  self.assertEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
  self.assertEqual(r["admin_unit_price_usd"],"184.07")
  self.assertEqual(r["resolved_oem"],"2206328")
  self.assertTrue(r["cart_restored"])
  self.assertFalse(r["cart_mutated"])
  self.assertEqual(called,[])
  self.assertEqual(ops,["price"])
 def test_alias_absent_keeps_search(self):
  r,called,ops=self.run_case([])
  self.assertEqual(called,["2205250"])
  self.assertIn("add",ops)
 def test_authority_mismatch_does_not_fastpath(self):
  r,called,_=self.run_case([line("2206328","token")],{**A,"requested_oem":"0000000"})
  self.assertEqual(called,["2205250"])
 def test_unproven_does_not_fastpath(self):
  r,called,_=self.run_case([line("2206328","token")],{**A,"replacement_proven":False})
  self.assertEqual(called,["2205250"])
 def test_duplicated_resolved_fails_without_search(self):
  r,called,ops=self.run_case([line("2206328","a"),line("2206328","b")])
  self.assertEqual(r["status"],"AMBIGUOUS_SKU")
  self.assertEqual(called,[])
  self.assertEqual(ops,[])
 def test_qty_more_than_one_fails_without_search(self):
  r,called,_=self.run_case([line("2206328","a",2)])
  self.assertEqual(r["status"],"AMBIGUOUS_SKU")
  self.assertEqual(called,[])
 def test_invalid_price_fails_closed(self):
  r,called,_=self.run_case([line("2206328","a",1,"0")])
  self.assertNotEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
  self.assertEqual(called,[])
 def test_unverified_session_fails_before_search(self):
  f=Fake([line("2206328","a")]);f.verify=lambda:False
  f.find_exact_oem=lambda oem:self.fail("unexpected search")
  with tempfile.TemporaryDirectory() as d:
   r=run_probe("2205250",f,lock_path=Path(d)/"lock",journal_path=Path(d)/"journal",authoritative_target=A)
  self.assertNotEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
if __name__=="__main__":unittest.main()
