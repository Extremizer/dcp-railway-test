import unittest
from datetime import datetime, timedelta, timezone
from polaris_dealer_cart_contract import validate, STATUS_OK, STATUS_NO, LOGICAL_SOURCE

NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
def candidate(**kw):
    c=dict(dealer_authenticated=True,context="dealer_cart",manufacturer="Polaris",
      item_category="Parts",category_server_verified=True,requested_oem="2206328",
      matched_sku="2206328",current_oem="2206328",cart_line_unique=True,
      cart_line_stable=True,cart_line_id="cart-line-1",quantity=1,currency="USD",
      source=LOGICAL_SOURCE,source_provenance="authenticated-owner-cart-observation",
      price_kind="DEALER_CART_UNIT",unit_price_usd="184.07",
      unit_price_separately_identified=True,subtotal_usd="184.07",
      observed_at=NOW.isoformat())
    c.update(kw)
    return c

class PolarisCartContractTests(unittest.TestCase):
    def check_rejected(self, **kw):
        r=validate(candidate(**kw),now=NOW)
        self.assertEqual(r["status"],STATUS_NO)
        self.assertIsNone(r["dealer_price_usd"])
    def test_01_verified_candidate_not_production_found(self):
        r=validate(candidate(),now=NOW)
        self.assertEqual(r["status"],STATUS_OK)
        self.assertFalse(r["production_found"])
        self.assertIsNone(r["dealer_price_usd"])
        self.assertEqual(r["unit_price_usd"],"184.07")
    def test_02_client_context(self): self.check_rejected(context="client_cart")
    def test_03_search_msrp(self): self.check_rejected(source="search_page:MSRP")
    def test_04_search_dbl(self): self.check_rejected(source="search_page:#add_2206328 .c2 .dbl")
    def test_05_wrong_manufacturer(self): self.check_rejected(manufacturer="Arctic Cat")
    def test_06_wrong_category(self): self.check_rejected(item_category="Accessories")
    def test_07_unverified_category(self): self.check_rejected(category_server_verified=False)
    def test_08_sku_mismatch(self): self.check_rejected(matched_sku="9999999")
    def test_09_unverified_replacement(self): self.check_rejected(current_oem="9999999")
    def test_10_duplicate_rows(self): self.check_rejected(cart_line_unique=False)
    def test_11_missing_line(self): self.check_rejected(cart_line_id=None)
    def test_12_missing_qty(self): self.check_rejected(quantity=None)
    def test_13_invalid_qty(self): self.check_rejected(quantity=0)
    def test_14_subtotal_contradiction(self): self.check_rejected(subtotal_usd="209.99")
    def test_15_non_usd(self): self.check_rejected(currency="RUB")
    def test_16_missing_currency(self): self.check_rejected(currency=None)
    def test_17_missing_provenance(self): self.check_rejected(source_provenance="")
    def test_18_stale_timestamp(self): self.check_rejected(observed_at=(NOW-timedelta(hours=25)).isoformat())
    def test_19_future_timestamp(self): self.check_rejected(observed_at=(NOW+timedelta(seconds=1)).isoformat())
    def test_20_invalid_timestamp(self): self.check_rejected(observed_at="garbage")
    def test_21_exact_ttl_boundary(self):
        self.assertEqual(validate(candidate(observed_at=(NOW-timedelta(hours=24)).isoformat()),now=NOW)["status"],STATUS_OK)
    def test_22_missing_unit_distinction(self): self.check_rejected(unit_price_separately_identified=False)
    def test_23_wrong_price_kind(self): self.check_rejected(price_kind="MSRP")
    def test_24_qty_subtotal_consistency(self):
        r=validate(candidate(quantity=2,subtotal_usd="368.14"),now=NOW)
        self.assertEqual(r["status"],STATUS_OK)
    def test_25_no_dealer_auth(self): self.check_rejected(dealer_authenticated=False)

if __name__=="__main__": unittest.main()
