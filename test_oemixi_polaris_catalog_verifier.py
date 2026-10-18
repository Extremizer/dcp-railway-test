"""Synthetic authorized DCP read-only verifier."""
import unittest
from unittest.mock import Mock
from oemixi_polaris_catalog_verifier import verify_exact_polaris

TAB={"type":"page","url":"https://www.dealercostparts.com/oemparts/partsearch/polaris?partsearch=3211070"}
GOOD={"origin":"https://www.dealercostparts.com",
      "before":"https://www.dealercostparts.com/oemparts/partsearch/polaris?partsearch=3211070",
      "after":"https://www.dealercostparts.com/oemparts/partsearch/polaris?partsearch=3211070",
      "exact_sku":"3211070","current_oem":"3211070","name":"DRIVE BELT",
      "msrp":"$114.99","catalog":"Parts",
      "source_path":"/oemparts/partsearch/polaris?partsearch=3211070"}
class PolarisVerifierTests(unittest.TestCase):
    def verify(self,data=None,guard=True):
        return verify_exact_polaris("Polaris","3211070",tabs=lambda:[TAB],
            evaluate=lambda tab,expr: GOOD if data is None else data,
            session_guard=lambda:guard)
    def test_exact_authenticated_read_only_evidence(self):
        result=self.verify()
        self.assertEqual(result["manufacturer"],"Polaris")
        self.assertEqual(result["current_oem"],"3211070")
        self.assertEqual(result["msrp_usd"],114.99)
        self.assertEqual(result["source_kind"],"live_public_form_metadata")
    def test_live_dom_msrp_selector_is_exact_oem_scoped(self):
        def evaluate(tab, expr):
            self.assertIn("div.c2.act_'+\"3211070\"+' span.dbl'", expr)
            self.assertIn("forms.length!==1",expr)
            self.assertIn("exactMsrp.length===1",expr)
            self.assertIn("f.querySelector('.c2 .dbl')",expr)
            return GOOD
        result=verify_exact_polaris("Polaris","3211070",tabs=lambda:[TAB],
            evaluate=evaluate,session_guard=lambda:True)
        self.assertEqual(result["msrp_usd"],114.99)

    def test_no_hidden_fetch_or_navigation(self):
        def inspect(tab, expr):
            self.assertNotIn('fetch(',expr)
            self.assertNotIn('location.assign(',expr)
            self.assertIn('const doc=document',expr)
            self.assertIn('wrong_page',expr)
            return GOOD
        self.assertIsNotNone(verify_exact_polaris('Polaris','3211070',
            tabs=lambda:[TAB],evaluate=inspect,session_guard=lambda:True))

    def test_nonmatching_page_fails_without_evaluate(self):
        calls=[]
        result=verify_exact_polaris('Polaris','3211070',
            tabs=lambda:[{'type':'page','url':'https://www.dealercostparts.com/oemparts/c/ski_doo/parts'}],
            evaluate=lambda *_:calls.append('bad'),session_guard=lambda:True)
        self.assertIsNone(result)
        self.assertEqual(calls,[])

    def test_session_fail_closed(self):
        self.assertIsNone(self.verify(guard=False))
    def test_bad_origin_fail_closed(self):
        self.assertIsNone(self.verify({**GOOD,"origin":"https://evil.example"}))
    def test_context_change_fail_closed(self):
        self.assertIsNone(self.verify({**GOOD,"after":"https://other.example"}))
    def test_mismatched_sku_fail_closed(self):
        self.assertIsNone(self.verify({**GOOD,"exact_sku":"9999999"}))
    def test_alias_not_authorized(self):
        self.assertIsNone(self.verify({**GOOD,"current_oem":"9999999"}))
    def test_cloudflare_fail_closed(self):
        self.assertIsNone(self.verify({"stop":"auth"}))
    def test_no_msrp_fail_closed(self):
        self.assertIsNone(self.verify({**GOOD,"msrp":""}))
    def test_wrong_manufacturer_fail_closed(self):
        self.assertIsNone(verify_exact_polaris("Honda","3211070",
            tabs=lambda:[TAB],evaluate=lambda t,e:GOOD,session_guard=lambda:True))
if __name__=="__main__":unittest.main()
