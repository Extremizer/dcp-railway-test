import unittest
import dcp_stage10_disabled_supplier as supplier

class DisabledSupplierTests(unittest.TestCase):
    def document(self, account="fixture@example.invalid", href="/account/logoff", logout=True):
        link=('<a href="'+href+'">SIGN OUT</a>') if logout else ''
        return '<nav id="persistant-nav"><span class="displayname">'+account+'</span>'+link+'</nav>'

    def test_exact_email_and_signout(self):
        email="fixture@example.invalid"
        self.assertEqual(supplier.fixture_identity(self.document(),email),
                         "FIXTURE_AUTHENTICATED_NOT_PRICE_VERIFIED")
        for html in (self.document(account="other@example.invalid"),
                     self.document(logout=False),
                     self.document(href="https://foreign.invalid/account/logoff"),
                     self.document(href="/account/logoff?bad=1"),
                     self.document()+"<div>not a dealer price</div>".replace(
                          "not a dealer price","not a dealer price")):
            status=supplier.fixture_identity(html,email)
            if "not a dealer price" in html:
                self.assertEqual(status,"FIXTURE_AUTHENTICATED_NOT_PRICE_VERIFIED")
            else:
                self.assertEqual(status,"AUTH_REQUIRED")
        self.assertEqual(supplier.fixture_identity(self.document().replace(
            '</nav>','<span class="displayname">second@example.invalid</span></nav>'),email),
            "AUTH_REQUIRED")

    def test_cf_denial(self):
        self.assertEqual(supplier.fixture_identity(self.document()+"<p>Cloudflare</p>",
            "fixture@example.invalid"),"CLOUDFLARE")

    def test_no_live_or_found(self):
        self.assertNotEqual(supplier.fixture_price()["status"],"FOUND")
        with self.assertRaises(supplier.AdapterDisabled):supplier.prohibited_live()
        with self.assertRaises(supplier.AdapterDisabled):
            supplier.fixture_identity(self.document(),"fixture@example.invalid",enabled=True)
