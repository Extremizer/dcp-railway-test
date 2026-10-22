import unittest
import dcp_stage10_disabled_supplier as supplier

class DisabledSupplierTests(unittest.TestCase):
    def test_exact_email_and_signout(self):
        email="fixture@example.invalid"
        self.assertEqual(supplier.fixture_identity("<b>SIGN OUT</b><span>"+email+"</span>",email),
                         "FIXTURE_AUTHENTICATED_NOT_PRICE_VERIFIED")
        self.assertEqual(supplier.fixture_identity("<b>SIGN OUT</b><span>other@example.invalid</span>",email),"AUTH_REQUIRED")
        self.assertEqual(supplier.fixture_identity("<span>"+email+"</span>",email),"AUTH_REQUIRED")
    def test_cf_denial(self):
        self.assertEqual(supplier.fixture_identity("<p>Verify you are human Cloudflare</p>","fixture@example.invalid"),"CLOUDFLARE")
    def test_no_live_or_found(self):
        self.assertNotEqual(supplier.fixture_price()["status"],"FOUND")
        with self.assertRaises(supplier.AdapterDisabled):
            supplier.prohibited_live()
        with self.assertRaises(supplier.AdapterDisabled):
            supplier.fixture_identity("SIGN OUT fixture@example.invalid","fixture@example.invalid",enabled=True)
