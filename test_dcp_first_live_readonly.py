import unittest
import dcp_first_live_readonly as gate

class ReadinessTests(unittest.TestCase):
    def test_default_denied_no_price(self):
        x=gate.verify({})
        self.assertEqual(x["status"],"BLOCKED")
        self.assertIsNone(x["dealer_price_usd"])
        self.assertEqual((x["browser_requests"],x["cart_mutations"],x["broker_completions"]),(0,0,0))
    def test_claimed_gates_do_not_enable_cart(self):
        x=gate.verify({"EXTREMIZER_DCP_LIVE_OWNER_GO":"1","EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT_PRESENT":"1",
            "EXTREMIZER_DCP_BROWSER_SOURCE_VERIFIED":"1","EXTREMIZER_DCP_OWNERSHIP_FENCED":"1",
            "EXTREMIZER_DCP_SIGNED_BROKER_VERIFIED":"1","EXTREMIZER_DCP_OEM":"2205250",
            "EXTREMIZER_DCP_MANUFACTURER":"Polaris","EXTREMIZER_DCP_STATE_DIR":"/tmp"})
        self.assertIn("POLARIS_READ_ONLY_PRICE_SOURCE_UNPROVEN_CART_MUTATION_HOLD",x["reason_codes"])
        self.assertIsNone(x["dealer_price_usd"])
    def test_wrong_oem_denied(self):
        self.assertIn("EXACT_OEM_MANUFACTURER_MISMATCH",gate.verify(
            {"EXTREMIZER_DCP_OEM":"2206328","EXTREMIZER_DCP_MANUFACTURER":"Polaris"})["reason_codes"])
