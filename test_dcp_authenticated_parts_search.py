"""Approved DCP business contract: SIGN OUT -> dealer SEARCH, Polaris CART-only."""
import unittest
import sqlite3
import os
from unittest.mock import patch
from dcp_authenticated_parts_search import extract_parts_rows,parts_dom_expression
from dcp_category_authority import resolve_search_route

def row(old="0746-885",current="0746-965",price="$533.32"):
    return {"form_sku":old,"current_oem":current,"price_selector_exact":True,
            "price_text":price,"name":"Clutch, Drive - Uncalibrated"}
def doc(*rows,auth=True):
    return {"http_status":200,"authorized":auth,"challenged":False,
            "rows":list(rows),"dcp_dealer_account":"dealer@example.invalid"}
class OwnerConfirmedDcpParts(unittest.TestCase):
    def setUp(self):
        context = patch.dict(os.environ, {"EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT":
                                       "dealer@example.invalid"})
        context.start()
        self.addCleanup(context.stop)

    def test_mismatched_or_missing_dealer_email_denied(self):
        for value in ("wrong@example.invalid", "", None, 42):
            with self.subTest(value=value):
                evidence={**doc(row()), "dcp_dealer_account":value}
                result=extract_parts_rows("Arctic Cat", "0746-965", evidence,
                                          origin="server_html")
                self.assertEqual(result.status,"AUTH_REQUIRED")
                self.assertIsNone(result.dealer_price_usd)

    def test_missing_expected_dealer_email_denied(self):
        with patch.dict(os.environ,{"EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT":""}):
            result=extract_parts_rows("Arctic Cat", "0746-965",doc(row()),
                                      origin="server_html")
        self.assertEqual(result.status,"AUTH_REQUIRED")

    def test_matching_email_with_case_and_whitespace(self):
        evidence={**doc(row()),"dcp_dealer_account":" DEALER@EXAMPLE.INVALID "}
        result=extract_parts_rows("Arctic Cat","0746-965",evidence,
                                  origin="live_dom")
        self.assertEqual(result.status,"FOUND")

    def test_browser_requires_exactly_one_account_name(self):
        for live in (True,False):
            js=parts_dom_expression("arctic_cat","0746-965",live=live)
            self.assertIn("accounts.length!==1",js)
            self.assertIn("dcp_dealer_account:accounts[0]",js)
            self.assertIn("resultAccounts[0].toLowerCase()!==accounts[0].toLowerCase()",js)
            self.assertNotIn("cart/add",js)
    def test_arctic_two_former_numbers_become_one_current_price(self):
        result=extract_parts_rows("Arctic Cat","0746-965",
                doc(row(),row("0746-933")),origin="server_html")
        self.assertEqual(result.status,"FOUND")
        self.assertEqual(result.current_oem,"0746-965")
        self.assertEqual(result.matched_sku,"0746-965")
        self.assertEqual(result.dealer_price_usd,533.32)
        self.assertEqual(result.source,"server_html:current_oem_0746-965")
    def test_sign_in_is_not_dealer(self):
        x=extract_parts_rows("Arctic Cat","0746-965",doc(row(price="$790.96"),auth=False),origin="server_html")
        self.assertEqual(x.status,"AUTH_REQUIRED")
        self.assertIsNone(x.dealer_price_usd)
    def test_previous_oem_returns_replacement_not_competing_product(self):
        x=extract_parts_rows("Arctic Cat","0746-885",doc(row()),origin="server_html")
        self.assertEqual(x.status,"FOUND")
        self.assertEqual(x.current_oem,"0746-965")
        self.assertEqual(x.source,"server_html:previous_oem_0746-885")
    def test_no_conflicting_supersession_prices(self):
        x=extract_parts_rows("Arctic Cat","0746-965",doc(row(),row("0746-933",price="$999.99")),origin="live_dom")
        self.assertEqual(x.status,"TECHNICAL_ERROR")
    def test_canam_one_normal_row(self):
        x=extract_parts_rows("Can-Am","709200703",doc(row("709200703","709200703","$245.88")),origin="server_html")
        self.assertEqual(x.status,"FOUND")
        self.assertEqual(x.dealer_price_usd,245.88)
    def test_challenge_and_other_fail_closed(self):
        for x in ({"challenged":True},{"authorized":False},{"http_status":403},{"context_changed":True}):
            with self.subTest(x=x):
                out=extract_parts_rows("Arctic Cat","0746-965",{**doc(row()),**x},origin="live_dom")
                self.assertNotEqual(out.status,"FOUND")
    def test_polaris_search_prohibited(self):
        x=extract_parts_rows("Polaris","2206328",doc(row("2206328","2206328")),origin="live_dom")
        self.assertNotEqual(x.status,"FOUND")
    def test_logout_identity_uses_same_origin_nav_link_not_page_text(self):
        for live in (True,False):
            with self.subTest(live=live):
                js=parts_dom_expression("arctic_cat","0746-965",live=live)
                self.assertIn("'#persistant-nav a[href]'",js)
                self.assertIn("u.pathname==='/account/logoff'",js)
                self.assertIn("u.origin===location.origin",js)
                self.assertIn("!u.search && !u.hash",js)
                self.assertIn("if(!hasLogout(document)||accounts.length!==1)return {authorized:false}",js)
                self.assertIn("if(!hasLogout(doc)||resultAccounts.length!==1",js)
                self.assertNotIn("/sign out/i.test(part)",js)
                self.assertNotIn("/sign out/i.test(text)",js)
                self.assertNotIn("cart/add",js)

    def test_not_authorized_never_uses_search_price(self):
        for origin in ("server_html","live_dom"):
            result=extract_parts_rows("Arctic Cat","0746-965",
                doc(row(price="$799.00"),auth=False),origin=origin)
            self.assertEqual(result.status,"AUTH_REQUIRED")
            self.assertIsNone(result.dealer_price_usd)

    def test_one_get_or_passive_no_cart_mutations(self):
        js=parts_dom_expression("arctic_cat","0746-965",live=False)
        self.assertEqual(js.count("fetch("),1)
        self.assertNotIn("cart/add",js)
        self.assertNotIn("location.href=",js)
        self.assertNotIn("window.location",js)
        self.assertEqual(parts_dom_expression("arctic_cat","0746-965",live=True).count("fetch("),0)
    def test_selected_manufacturer_new_parts_route_without_catalog_row(self):
        c=sqlite3.connect(":memory:")
        try:
            route=resolve_search_route(c,"Arctic Cat","0746-965")
            self.assertEqual(route["item_category"],"Parts")
            self.assertEqual(route["category_source_kind"],"explicit_manufacturer_parts_search")
            self.assertIsNone(resolve_search_route(c,"Polaris","2206328"))
            self.assertIsNone(resolve_search_route(c,"Arctic Cat","__AUTO__"))
        finally:c.close()

if __name__=="__main__": unittest.main()
