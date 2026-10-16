import io
import logging
import unittest
from contextlib import redirect_stdout,redirect_stderr
from unittest.mock import patch
from datetime import datetime,timezone
from polaris_dealer_session_identity import verify_dealer_identity,inspect_verified_dealer_cart
from test_polaris_cart_dom_layer_a import row,html
URL="https://www.dealercostparts.com/cart"
ACCOUNT="synthetic dealer secret A"
TAB={"type":"page","url":URL}
NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
CATEGORY=dict(manufacturer="Polaris",oem="2206328",item_category="Parts",category_source_kind="validated_public_msrp",category_source_ref="synthetic",category_verified_at=NOW.isoformat())
def run(*,status="READY",names=None,expected=ACCOUNT,logoff=True,url=URL,tabs=None):
    proof={"url_before":url,"url_after":url,"names":[ACCOUNT] if names is None else names,"logoff":logoff}
    return verify_dealer_identity(preflight=lambda:status,expected_account=expected,
       tabs=lambda:[TAB] if tabs is None else tabs,evaluate=lambda tab,expression:proof)
class IdentityTests(unittest.TestCase):
    def test_01_ready_matching(self): self.assertTrue(run()["dealer_context_verified"])
    def test_02_auth_required(self): self.assertFalse(run(status="AUTH_REQUIRED")["dealer_context_verified"])
    def test_03_cloudflare(self): self.assertFalse(run(status="CLOUDFLARE")["dealer_context_verified"])
    def test_04_missing_config(self): self.assertFalse(run(expected="")["dealer_context_verified"])
    def test_05_missing_displayname(self): self.assertFalse(run(names=[])["dealer_context_verified"])
    def test_06_wrong_identity(self): self.assertFalse(run(names=["wrong"])["dealer_context_verified"])
    def test_07_signout_wrong_identity(self): self.assertFalse(run(names=["wrong"],logoff=True)["dealer_context_verified"])
    def test_08_case_whitespace(self): self.assertTrue(run(names=["  SYNTHETIC DEALER SECRET A  "])["dealer_context_verified"])
    def test_09_error_no_identity(self): self.assertNotIn(ACCOUNT,str(run(names=["wrong"])))
    def test_10_no_secret_logs(self):
        out=io.StringIO()
        with redirect_stdout(out),redirect_stderr(out):
            result=run(names=["wrong"])
        self.assertNotIn(ACCOUNT,out.getvalue()+repr(result))
    def test_11_conflicting_identity(self): self.assertFalse(run(names=[ACCOUNT,"other"])["dealer_context_verified"])
    def test_12_missing_logout(self): self.assertFalse(run(logoff=False)["dealer_context_verified"])
    def test_13_no_cart(self): self.assertFalse(run(tabs=[])["dealer_context_verified"])
    def test_14_env_config(self):
        with patch.dict("os.environ",{"EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT":ACCOUNT}):
            result=verify_dealer_identity(preflight=lambda:"READY",tabs=lambda:[TAB],evaluate=lambda tab,expression:dict(url_before=URL,url_after=URL,names=[ACCOUNT],logoff=True))
        self.assertTrue(result["dealer_context_verified"])
    def test_15_composed_wiring_read_only(self):
        def evaluate(tab,expression):
            if "querySelectorAll" in expression:
                return dict(url_before=URL,url_after=URL,names=[ACCOUNT],logoff=True)
            return dict(url_before=URL,url_after=URL,html=html(row()),context_changed=False)
        r=inspect_verified_dealer_cart(requested_oem="2206328",category_evidence=CATEGORY,
            preflight=lambda:"READY",expected_account=ACCOUNT,tabs=lambda:[TAB],evaluate=evaluate,
            observed_at=NOW.isoformat(),now=NOW)
        self.assertEqual(r["status"],"VALIDATED_DEALER_CART_CANDIDATE")
        self.assertIsNone(r["dealer_price_usd"])
        self.assertFalse(r["production_found"])
if __name__=="__main__":unittest.main()
