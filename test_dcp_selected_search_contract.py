"""Synthetic input only: a contract test is not authenticated DCP evidence."""
import unittest
from datetime import datetime, timedelta, timezone
from dcp_selected_search_contract import verify_observation

def evidence(brand="Arctic Cat", oem="0746-965"):
    slug={"Arctic Cat":"arctic_cat","Can-Am":"can_am"}[brand]
    return dict(authenticated=True, challenged=False, http_status=200,
        document_url=f"https://www.dealercostparts.com/oemparts/partsearch/{slug}?partsearch={oem}",
        manufacturer=brand, query_oem=oem, matched_sku=oem,
        current_oem=oem, row_count=1, found=True,
        price_label="Dealer Price", price_field_selector=".c2 .dbl",
        dealer_price_usd=123.45, currency="USD",
        verified_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))

class SelectedSearchContract(unittest.TestCase):
    def test_exact_arctic_cat_and_can_am(self):
        for b,n in (("Arctic Cat","0746-965"),("Can-Am","709200703")):
            with self.subTest(brand=b):
                r=verify_observation(b,n,evidence(b,n))
                self.assertEqual((r["status"],r["manufacturer"],r["matched_sku"]),("FOUND",b,n))
    def test_polaris_never_search(self):
        for b in ("Polaris","__AUTO__","UNKNOWN",""):
            self.assertNotEqual(verify_observation(b,"0746-965",evidence())["status"],"FOUND")
    def test_no_cross_brand_or_mismatched_oem(self):
        for o in ({"manufacturer":"Can-Am"},{"matched_sku":"0746-933"},{"current_oem":"0746-933"},
                  {"query_oem":"0746-933"},{"row_count":2}):
            self.assertNotEqual(verify_observation("Arctic Cat","0746-965",{**evidence(),**o})["status"],"FOUND")
    def test_no_generic_price_or_retail_msrp(self):
        for o in ({"price_label":"MSRP"},{"price_label":"Retail"},{"price_label":""},
                  {"price_field_selector":".price"},{"currency":"RUB"},{"dealer_price_usd":0},
                  {"dealer_price_usd":float("nan")},{"dealer_price_usd":True}):
            self.assertEqual(verify_observation("Arctic Cat","0746-965",{**evidence(),**o})["status"],"DEALER_PRICE_UNVERIFIED")
    def test_auth_challenge_wrong_host_and_not_found(self):
        for o in ({"authenticated":False},{"challenged":True},{"http_status":403},
                  {"document_url":"https://evil.example/oemparts/partsearch/arctic_cat?partsearch=0746-965"}):
            self.assertNotEqual(verify_observation("Arctic Cat","0746-965",{**evidence(),**o})["status"],"FOUND")
        self.assertEqual(verify_observation("Arctic Cat","0746-965",{**evidence(),"found":False})["status"],"NOT_FOUND")
    def test_observation_must_be_fresh(self):
        stale=(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat()
        self.assertNotEqual(verify_observation("Arctic Cat","0746-965",{**evidence(),"verified_at":stale})["status"],"FOUND")

if __name__=="__main__":
    unittest.main()
