import unittest
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
from oemixi_admin_polaris_cart_price import admin_cart_price,format_admin_price
NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
OEM="2206328"
def category(**updates):
    c={"item_category":"Parts","resolved_oem":OEM,"category_source_kind":"validated_public_msrp",
       "category_source_ref":"synthetic_server_authority","category_verified_at":NOW.isoformat()}
    c.update(updates)
    return c
def validated(**updates):
    c={"status":"VALIDATED_DEALER_CART_CANDIDATE","manufacturer":"Polaris",
       "item_category":"Parts","requested_oem":OEM,"matched_sku":OEM,"current_oem":OEM,
       "quantity":1,"unit_price_usd":"184.07","currency":"USD",
       "dealer_price_usd":None,"production_found":False}
    c.update(updates)
    return c
def call(cat=None,cart=None):
    return admin_cart_price(OEM,category_resolver=lambda manufacturer,oem:category() if cat is None else cat,
        inspect=lambda **kw:validated() if cart is None else cart,now=NOW)
class ReadModelTests(unittest.TestCase):
    def test_01_verified_price(self):
        p=call()
        self.assertEqual(p["admin_unit_price_usd"],"184.07")
        self.assertIn("$184.07",format_admin_price(OEM,p))
    def test_02_no_admin_data_without_server(self):
        self.assertIsNone(admin_cart_price(OEM,now=NOW)["admin_unit_price_usd"])
    def test_03_missing_identity(self): self.assertIsNone(call(cart=validated(status="DEALER_PRICE_UNVERIFIED"))["admin_unit_price_usd"])
    def test_04_auth_required(self): self.test_03_missing_identity()
    def test_05_cloudflare(self): self.test_03_missing_identity()
    def test_06_missing_cart(self): self.test_03_missing_identity()
    def test_07_ambiguous_cart(self): self.test_03_missing_identity()
    def test_08_missing_category(self): self.assertIsNone(call(cat={})["admin_unit_price_usd"])
    def test_09_wrong_category(self): self.assertIsNone(call(cat=category(item_category="Accessories"))["admin_unit_price_usd"])
    def test_10_wrong_manufacturer(self): self.assertIsNone(call(cart=validated(manufacturer="Honda"))["admin_unit_price_usd"])
    def test_11_wrong_sku(self): self.assertIsNone(call(cart=validated(matched_sku="9999999"))["admin_unit_price_usd"])
    def test_12_layer_a_reject(self): self.test_03_missing_identity()
    def test_13_layer_b_reject(self): self.test_03_missing_identity()
    def test_14_stale_category(self): self.assertIsNone(call(cat=category(category_verified_at=(NOW-timedelta(days=2)).isoformat()))["admin_unit_price_usd"])
    def test_15_msrp_only(self): self.assertIsNone(call(cart={"status":"FOUND","msrp_usd":239.99})["admin_unit_price_usd"])
    def test_16_client_cart(self): self.assertIsNone(call(cart=validated(unit_price_usd="209.99",status="CLIENT_CART"))["admin_unit_price_usd"])
    def test_17_search_page(self): self.assertIsNone(call(cart={"status":"FOUND","dl_usd":184.07})["admin_unit_price_usd"])
    def test_18_production_found_false(self): self.assertIs(call()["production_found"],False)
    def test_19_never_production_price(self): self.assertIsNone(call()["dealer_price_usd"])
    def test_20_reject_global_found(self): self.assertIsNone(call(cart=validated(production_found=True))["admin_unit_price_usd"])
    def test_21_reject_price_promotion(self): self.assertIsNone(call(cart=validated(dealer_price_usd=184.07))["admin_unit_price_usd"])
    def test_22_no_identity_output(self):
        self.assertNotIn("SYNTHETIC_PRIVATE_ID",repr(call()))
    def test_23_exact_category_oem(self): self.assertIsNone(call(cat=category(resolved_oem="2204967"))["admin_unit_price_usd"])
    def test_24_no_positive_without_usd(self): self.assertIsNone(call(cart=validated(currency="EUR"))["admin_unit_price_usd"])
    def test_25_unavailable_format(self): self.assertIn("Дилерская цена недоступна",format_admin_price(OEM,call(cat={})))
if __name__=="__main__":unittest.main()
