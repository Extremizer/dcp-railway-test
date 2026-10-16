import tempfile,unittest
from pathlib import Path
from polaris_add_form_contract import validate_form
from polaris_add_existing_session import add_one
from polaris_cart_mutation_adapter import ControlledDcpCartAdapter
from polaris_probe_cart_delta import prove_delta
from polaris_probe_cart_transaction import run_probe
from test_polaris_probe_cart_transaction import line

OEM="2206328"
def form(**kw):
    d=dict(id="add_"+OEM,action="/cart/addoemsinglepart",method="POST",manf="polaris_atv",sku=OEM,qty="1")
    d.update(kw)
    return d

def replacement_form(old="2205250",new="2206328",**kw):
    d=dict(id="add_"+old,action="/cart/addoemsinglepart",method="POST",
           manf="polaris_atv",sku=old,qty="1",old_sku=old,new_sku=new)
    d.update(kw)
    return d

class ExactAddTests(unittest.TestCase):
    def test_01_exact(self):self.assertTrue(validate_form(OEM,[form()])["exact_match"])
    def test_02_related_first(self):self.assertEqual(validate_form(OEM,[form(id="add_2204967",sku="2204967"),form()])["resolved_oem"],OEM)
    def test_03_wrong_id(self):
        with self.assertRaises(ValueError):validate_form(OEM,[form(id="add_2204967")])
    def test_04_wrong_sku(self):
        with self.assertRaises(ValueError):validate_form(OEM,[form(sku="2204967")])
    def test_05_wrong_manufacturer(self):
        with self.assertRaises(ValueError):validate_form(OEM,[form(manf="other")])
    def test_06_wrong_qty(self):
        with self.assertRaises(ValueError):validate_form(OEM,[form(qty="2")])
    def test_07_duplicate_forms(self):
        with self.assertRaises(ValueError):validate_form(OEM,[form(),form()])
    def test_08_default_off(self):
        with self.assertRaises(PermissionError):
            add_one(OEM,read_forms=lambda x:[form()],perform_post=lambda *x:None,verify=lambda:True)
    def test_09_post_is_not_delta_proof(self):
        result=add_one(OEM,read_forms=lambda x:[form()],perform_post=lambda *x:{"ok":True},verify=lambda:True,allow_mutation=True)
        self.assertIs(result["cart_delta_verified"],False)
    def test_10_new_line_delta(self):
        self.assertEqual(prove_delta({"lines":[]},{"lines":[line(OEM,"created")]},OEM)["kind"],"new")
    def test_11_merge_delta(self):
        self.assertEqual(prove_delta({"lines":[line(OEM,"x",2)]},{"lines":[line(OEM,"x",3)]},OEM)["kind"],"merge")
    def test_12_unrelated_change(self):
        with self.assertRaises(ValueError):
            prove_delta({"lines":[line("9999999","other")]},{"lines":[line("9999999","other",2),line(OEM,"created")]},OEM)
    def test_13_session_pre(self):
        calls=[]
        with self.assertRaises(PermissionError):
            add_one(OEM,read_forms=lambda x:[form()],perform_post=lambda *x:calls.append(x),verify=lambda:False,allow_mutation=True)
        self.assertFalse(calls)
    def test_14_session_post(self):
        n=[True,True,True,False]
        with self.assertRaises(PermissionError):
            add_one(OEM,read_forms=lambda x:[form()],perform_post=lambda *x:None,verify=lambda:n.pop(0),allow_mutation=True)
    def test_15_exact_payload(self):
        calls=[]
        add_one(OEM,read_forms=lambda x:[form()],perform_post=lambda *args:calls.append(args),verify=lambda:True,allow_mutation=True)
        self.assertEqual(calls,[("/cart/addoemsinglepart",{"manf":"polaris_atv","sku":OEM,"qty":"1"})])
    def test_16_authority_mismatch(self):
        a=ControlledDcpCartAdapter.from_existing_session(verify_session=lambda:True,
           read_snapshot=lambda:{"lines":[]},read_forms=lambda x:[form()],
           perform_post=lambda *args:None,find_oem=lambda o:{"manufacturer":"Honda"},
           remove_exact_line=lambda t:None,decrement_exact_one=lambda t:None)
        with self.assertRaises(ValueError):a.find_exact_oem(OEM)
    def test_17_factory_mutation_disabled(self):
        a=ControlledDcpCartAdapter.from_existing_session(verify_session=lambda:True,
           read_snapshot=lambda:{"lines":[]},read_forms=lambda x:[form()],
           perform_post=lambda *args:None,find_oem=lambda o:validate_form(o,[form()]),
           remove_exact_line=lambda t:None,decrement_exact_one=lambda t:None)
        with self.assertRaises(PermissionError):a.add_one(OEM)

    def test_18_explicit_replacement(self):
        p=validate_form("2205250",[replacement_form()])
        self.assertEqual(p["requested_oem"],"2205250")
        self.assertEqual(p["submit_oem"],"2205250")
        self.assertEqual(p["resolved_oem"],"2206328")
        self.assertTrue(p["replacement_proven"])

    def test_19_replacement_old_must_match_requested(self):
        with self.assertRaises(ValueError):
            validate_form("2205250",[replacement_form(old="2205250",old_sku="2204967")])

    def test_20_replacement_submit_stays_requested(self):
        calls=[]
        r=add_one("2205250",read_forms=lambda x:[replacement_form()],
            perform_post=lambda *args:calls.append(args),verify=lambda:True,allow_mutation=True)
        self.assertEqual(calls,[("/cart/addoemsinglepart",
            {"manf":"polaris_atv","sku":"2205250","qty":"1"})])
        self.assertEqual(r["resolved_oem"],"2206328")

    def test_21_existing_resolved_price_no_mutation(self):
        class A:
            def __init__(self):self.ops=[]
            def verify(self):return True
            def snapshot(self):return {"lines":[line("2206328","existing")]}
            def find_exact_oem(self,o):
                return {"manufacturer":"Polaris","item_category":"Parts",
                    "requested_oem":"2205250","resolved_oem":"2206328",
                    "exact_match":True,"replacement_proven":True}
            def add_one(self,o):raise AssertionError("ADD MUST NOT RUN")
            def price(self,o,s):
                self.ops.append(("price",o))
                return "184.07"
        a=A()
        with tempfile.TemporaryDirectory() as d:
            r=run_probe("2205250",a,lock_path=Path(d)/"lock",journal_path=Path(d)/"journal")
        self.assertEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
        self.assertEqual(r["admin_unit_price_usd"],"184.07")
        self.assertEqual(r["requested_oem"],"2205250")
        self.assertEqual(r["resolved_oem"],"2206328")
        self.assertFalse(r["cart_mutated"])
        self.assertEqual(a.ops,[("price","2206328")])

    def test_22_unproven_replacement_rejected(self):
        class A:
            def verify(self):return True
            def snapshot(self):return {"lines":[]}
            def find_exact_oem(self,o):
                return {"manufacturer":"Polaris","item_category":"Parts",
                    "requested_oem":"2205250","resolved_oem":"2206328",
                    "exact_match":True,"replacement_proven":False}
        with tempfile.TemporaryDirectory() as d:
            r=run_probe("2205250",A(),lock_path=Path(d)/"lock",journal_path=Path(d)/"journal")
        self.assertEqual(r["status"],"OEM_UNVERIFIED")

if __name__=="__main__":unittest.main()
