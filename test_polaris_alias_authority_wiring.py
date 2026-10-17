import tempfile,unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import oemixi_admin_polaris_browser_price as mod

EVIDENCE={"manufacturer":"Polaris","oem":"2205250","resolved_oem":"2206328",
"item_category":"Parts","category_source_kind":"validated_public_msrp",
"category_source_ref":"fixture","category_verified_at":datetime.now(timezone.utc).isoformat()}
class CallerAuthority(unittest.TestCase):
 def check_caller(self,fn):
  with tempfile.TemporaryDirectory() as tmp,patch.object(mod,"run_probe",return_value={
      "status":"ADMIN_DEALER_CART_PRICE_VERIFIED","admin_unit_price_usd":"184.07",
      "cart_mutated":False,"cart_restored":True,"production_found":False,
      "dealer_price_usd":None,"resolved_oem":"2206328"}) as probe:
   kwargs={"adapter":object(),"lock_path":Path(tmp)/"lock","journal_path":Path(tmp)/"journal"}
   if fn=="admin":
    result=mod.admin_browser_price("2205250",category_resolver=lambda m,o:EVIDENCE,**kwargs)
   else:
    result=mod.producer_browser_price("2205250",category_evidence=EVIDENCE,
                                      dealer_identity_proof_ref="fixture",**kwargs)
   target=probe.call_args.kwargs["authoritative_target"]
   self.assertEqual(target,{"manufacturer":"Polaris","requested_oem":"2205250",
       "resolved_oem":"2206328","item_category":"Parts","exact_match":True,
       "replacement_proven":True})
   self.assertIn(result["status"],("FOUND","ADMIN_DEALER_CART_PRICE_VERIFIED"))
 def test_admin_forwards_authority(self):self.check_caller("admin")
 def test_producer_forwards_authority(self):self.check_caller("producer")
if __name__=="__main__":unittest.main()
