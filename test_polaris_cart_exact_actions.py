import unittest
from decimal import Decimal
from polaris_cart_exact_actions import ExactCartActions
from test_polaris_probe_cart_transaction import line,OEM

class Actions(unittest.TestCase):
    def setUp(self):
        self.rows=[line(OEM,"synthetic_token",3),line("9999999","unrelated",2,"10.00")]
        self.calls=[]
        self.ready=True
    def snapshot(self):return {"lines":[dict(x) for x in self.rows]}
    def perform(self,path):
        self.calls.append(path)
        op,token=path.split("/")[-2:]
        if op=="remove":self.rows=[x for x in self.rows if x["token"]!=token]
        if op=="decrease":
            x=next(x for x in self.rows if x["token"]==token)
            x["qty"]-=1;x["subtotal"]=str(Decimal(x["unit_price"])*x["qty"])
    def make(self,allow=True):
        return ExactCartActions(verify=lambda:self.ready,snapshot=self.snapshot,perform_existing_session_get=self.perform,allow_mutation=allow)
    def test_remove_exact(self):
        self.assertTrue(self.make().remove_line("synthetic_token",expected_itemid=OEM,expected_qty=3))
        self.assertEqual(self.calls,["/cart/remove/synthetic_token"])
        self.assertEqual(len(self.rows),1)
    def test_decrement_exact(self):
        self.assertTrue(self.make().decrement_one("synthetic_token",expected_itemid=OEM,expected_qty=3))
        self.assertEqual(self.calls,["/cart/decrease/synthetic_token"])
        self.assertEqual(self.rows[0]["qty"],2)
    def test_disabled_default(self):
        with self.assertRaises(PermissionError):self.make(False).remove_line("synthetic_token",expected_itemid=OEM,expected_qty=3)
        self.assertFalse(self.calls)
    def test_wrong_token(self):
        with self.assertRaises(ValueError):self.make().remove_line("wrong",expected_itemid=OEM,expected_qty=3)
        self.assertFalse(self.calls)
    def test_wrong_itemid(self):
        with self.assertRaises(ValueError):self.make().remove_line("synthetic_token",expected_itemid="WRONG",expected_qty=3)
    def test_wrong_qty(self):
        with self.assertRaises(ValueError):self.make().decrement_one("synthetic_token",expected_itemid=OEM,expected_qty=2)
    def test_auth_lost(self):
        self.ready=False
        with self.assertRaises(PermissionError):self.make().decrement_one("synthetic_token",expected_itemid=OEM,expected_qty=3)
    def test_unrelated_change_rejected(self):
        def bad(path):
            self.perform(path);self.rows[1]["qty"]+=1;self.rows[1]["subtotal"]="30.00"
        a=ExactCartActions(verify=lambda:True,snapshot=self.snapshot,perform_existing_session_get=bad,allow_mutation=True)
        with self.assertRaises(ValueError):a.decrement_one("synthetic_token",expected_itemid=OEM,expected_qty=3)
    def test_post_action_auth_loss(self):
        def loses(path):self.perform(path);self.ready=False
        a=ExactCartActions(verify=lambda:self.ready,snapshot=self.snapshot,perform_existing_session_get=loses,allow_mutation=True)
        with self.assertRaises(PermissionError):a.remove_line("synthetic_token",expected_itemid=OEM,expected_qty=3)
    def test_token_injection_rejected(self):
        with self.assertRaises(ValueError):self.make().remove_line("../checkout",expected_itemid=OEM,expected_qty=3)
        self.assertFalse(self.calls)
if __name__=="__main__":unittest.main()
