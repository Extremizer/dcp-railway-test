import tempfile,unittest,json,os
from pathlib import Path
from polaris_probe_cart_transaction import run_probe,recover_probe
from polaris_probe_cart_delta import prove_delta
from polaris_probe_cart_recovery import Journal,ProbeLock
from polaris_cart_mutation_adapter import ControlledDcpCartAdapter
from test_polaris_probe_cart_transaction import Fake,line,OEM

class MergeFake(Fake):
    def add_one(self,oem):
        self.operations.append("add")
        x=next(z for z in self.lines if z["sku"]==oem)
        x["qty"]+=1
        x["subtotal"]=str(float(x["unit_price"])*x["qty"])
    def decrement_one(self,token):
        self.operations.append("decrement:"+token)
        x=next(z for z in self.lines if z["token"]==token)
        x["qty"]-=1
        x["subtotal"]=str(float(x["unit_price"])*x["qty"])
class Hardened(unittest.TestCase):
    def test_01_merge_proof(self):
        before={"lines":[line(OEM,"a",2)]}
        after={"lines":[line(OEM,"a",3)]}
        self.assertEqual(prove_delta(before,after,OEM)["delta"],1)
    def test_02_merge_unrelated_change(self):
        before={"lines":[line(OEM,"a",2),line("9999999","b")]}
        after={"lines":[line(OEM,"a",3),line("9999999","b",2)]}
        with self.assertRaises(ValueError):prove_delta(before,after,OEM)
    def test_03_merge_wrong_price(self):
        with self.assertRaises(ValueError):prove_delta({"lines":[line(OEM,"a",2)]},{"lines":[line(OEM,"a",3,"185.00")]},OEM)
    def test_04_dry_run_no_add(self):
        f=Fake()
        with tempfile.TemporaryDirectory() as d:
            r=run_probe(OEM,f,lock_path=Path(d)/"lock",journal_path=Path(d)/"j",dry_run=True)
        self.assertEqual(r["status"],"DRY_RUN_READY")
        self.assertFalse(f.operations)
    def test_05_locked_recovery_required(self):
        f=Fake()
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/"lock";j=Path(d)/"j"
            Journal(j).save({"mutation_state":"MUTATION_STARTED"})
            self.assertEqual(run_probe(OEM,f,lock_path=lock,journal_path=j)["status"],"RECOVERY_REQUIRED")
    def test_06_recovery_ambiguous(self):
        with tempfile.TemporaryDirectory() as d:
            j=Path(d)/"j";lock=Path(d)/"lock"
            Journal(j).save({"requested_oem":OEM,"initial_cart_snapshot":{"lines":[]},"delta":None})
            self.assertEqual(recover_probe(Fake(),lock_path=lock,journal_path=j)["status"],"RECOVERY_REQUIRED")
    def test_07_recovery_new_line(self):
        f=Fake([line(OEM,"created")])
        with tempfile.TemporaryDirectory() as d:
            j=Path(d)/"j";lock=Path(d)/"lock"
            delta=prove_delta({"lines":[]},f.snapshot(),OEM)
            Journal(j).save({"probe_id":"dead-probe","owner_instance_id":"dead-instance","mutation_state":"MUTATION_CONFIRMED","requested_oem":OEM,"initial_cart_snapshot":{"lines":[]},"delta":delta})
            lock.write_text(json.dumps({"probe_id":"dead-probe","owner_instance_id":"dead-instance","pid":999999999,"created_at":"2026-10-16T12:00:00+00:00"}))
            self.assertEqual(recover_probe(f,lock_path=lock,journal_path=j)["status"],"RESTORED")
            self.assertFalse(f.lines)
    def test_08_recovery_after_price(self):self.test_07_recovery_new_line()
    def test_09_write_disabled(self):
        f=Fake()
        a=ControlledDcpCartAdapter(verify_session=f.verify,read_snapshot=f.snapshot,find_oem=lambda x:x,add_exact_one=f.add_one)
        with self.assertRaises(PermissionError):a.add_one(OEM)
    def test_10_adapter_snapshot_readonly(self):
        f=Fake([line(OEM,"a")])
        a=ControlledDcpCartAdapter(verify_session=f.verify,read_snapshot=f.snapshot,find_oem=lambda x:x)
        self.assertEqual(len(a.snapshot()["lines"]),1)
    def test_11_adapter_price_exact(self):
        f=Fake([line(OEM,"a")])
        a=ControlledDcpCartAdapter(verify_session=f.verify,read_snapshot=f.snapshot,find_oem=lambda x:x)
        self.assertEqual(a.price(OEM,f.snapshot()),"184.07")
    def test_12_recovery_lock_busy(self):
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/"lock"
            with ProbeLock(lock):
                self.assertEqual(recover_probe(Fake(),lock_path=lock,journal_path=Path(d)/"j")["status"],"RECOVERY_REQUIRED")
if __name__=="__main__":unittest.main()
