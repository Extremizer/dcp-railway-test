import tempfile,unittest
from pathlib import Path
from polaris_probe_cart_transaction import run_probe
from polaris_probe_cart_recovery import Journal,ProbeLock
from polaris_cart_snapshot import digest

OEM="2206328"
def line(sku,token,qty=1,price="184.07"):
    from decimal import Decimal
    return dict(sku=sku,token=token,qty=qty,unit_price=price,
                subtotal=str(Decimal(price)*qty),currency="USD")
class Fake:
    def __init__(self,lines=None):
        self.lines=list(lines or []);self.operations=[];self.fail_remove=False;self.fail_add=False
    def verify(self):return True
    def find_exact_oem(self,oem):return {'manufacturer':'Polaris','requested_oem':oem,'resolved_oem':oem,'item_category':'Parts','exact_match':True}
    def snapshot(self):return {"lines":[dict(x) for x in self.lines]}
    def add_one(self,oem):
        self.operations.append("add")
        if self.fail_add:raise TimeoutError()
        self.lines.append(line(oem,"created"))
    def remove_line(self,token):
        self.operations.append("remove:"+token)
        if self.fail_remove:raise TimeoutError()
        self.lines=[x for x in self.lines if x["token"]!=token]
    def price(self,oem,snap):
        self.operations.append("price")
        return next(x["unit_price"] for x in snap["lines"] if x["sku"]==oem)
class Probes(unittest.TestCase):
    def run_case(self,f):
        with tempfile.TemporaryDirectory() as d:
            return run_probe(OEM,f,lock_path=Path(d)/"lock",journal_path=Path(d)/"journal")
    def test_existing_oem_no_mutation(self):
        f=Fake([line(OEM,"original",2)])
        r=self.run_case(f)
        self.assertEqual(r["status"],"ADMIN_DEALER_CART_PRICE_VERIFIED")
        self.assertEqual(f.operations,["price"])
    def test_add_remove_restore(self):
        f=Fake([line("9999999","other",3,"12.00")]);original=digest(f.snapshot())
        r=self.run_case(f)
        self.assertEqual(r["status"],"ADMIN_PROBE_CART_PRICE_VERIFIED")
        self.assertEqual(r["admin_unit_price_usd"],"184.07")
        self.assertTrue(r["cart_restored"])
        self.assertEqual(digest(f.snapshot()),original)
        self.assertEqual(f.operations,["add","price","remove:created"])
    def test_cleanup_failure_no_success(self):
        f=Fake();f.fail_remove=True
        self.assertEqual(self.run_case(f)["status"],"RESTORATION_FAILED")
    def test_ambiguous_existing(self):
        f=Fake([line(OEM,"a"),line(OEM,"b")])
        self.assertEqual(self.run_case(f)["status"],"AMBIGUOUS_SKU")
    def test_lock_busy(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/"lock";journal=Path(d)/"journal"
            with ProbeLock(lock):
                r=run_probe(OEM,Fake(),lock_path=lock,journal_path=journal)
            self.assertEqual(r["status"],"BUSY")
    def test_crash_marker_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/"lock";journal=Path(d)/"journal"
            Journal(journal).save({"state":"MUTATION_STARTED"})
            r=run_probe(OEM,Fake(),lock_path=lock,journal_path=journal)
            self.assertEqual(r["status"],"RECOVERY_REQUIRED")
    def test_no_found(self):
        r=self.run_case(Fake())
        self.assertFalse(r["production_found"])
        self.assertIsNone(r["dealer_price_usd"])
    def test_no_op_add(self):
        f=Fake()
        f.add_one=lambda sku:None
        self.assertNotIn(self.run_case(f)["status"],("ADMIN_PROBE_CART_PRICE_VERIFIED",))
    def test_order_independent(self):
        a={"lines":[line("9999999","x"),line(OEM,"y")]}
        b={"lines":list(reversed(a["lines"]))}
        self.assertEqual(digest(a),digest(b))
if __name__=="__main__":unittest.main()
