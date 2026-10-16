"""Crash ownership and adverse mutation fixtures; never contacts live DCP."""
import json,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from polaris_probe_cart_recovery import RecoveryOwnership,ProbeBusy,Journal
from polaris_probe_cart_transaction import recover_probe,run_probe
from test_polaris_probe_cart_transaction import Fake,line,OEM
from polaris_probe_cart_delta import prove_delta

class FaultFake(Fake):
    def __init__(self,lines=None):
        super().__init__(lines);self.raise_after=False;self.fail_cleanup=False;self.calls=0
    def add_one(self,oem):
        super().add_one(oem)
        if self.raise_after:raise TimeoutError('synthetic')
    def remove_line(self,token):
        if self.fail_cleanup:raise TimeoutError('synthetic')
        return super().remove_line(token)
class RecoverySafety(unittest.TestCase):
    def prepare(self,d,*,lock_probe='p',journal_probe='p',lock_instance='i',journal_instance='i',pid=999999999,corrupt_lock=False,corrupt_journal=False,delta=None):
        lock=Path(d)/'lock';journal=Path(d)/'journal'
        lock.write_text('broken' if corrupt_lock else json.dumps(dict(probe_id=lock_probe,owner_instance_id=lock_instance,pid=pid,created_at='2026-10-16T00:00:00Z')))
        record=dict(probe_id=journal_probe,owner_instance_id=journal_instance,mutation_state='MUTATION_CONFIRMED',requested_oem=OEM,initial_cart_snapshot={'lines':[]},delta=delta)
        journal.write_text('broken' if corrupt_journal else json.dumps(record))
        return lock,journal
    def test_dead_owner_matching(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d)
            with patch('polaris_probe_cart_recovery._pid_alive',return_value=False):
                with RecoveryOwnership(lock,j):pass
    def test_live_owner_denied(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d,pid=os.getpid())
            with self.assertRaises(ProbeBusy):RecoveryOwnership(lock,j).__enter__()
    def test_mismatch_probe(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d,journal_probe='other')
            with patch('polaris_probe_cart_recovery._pid_alive',return_value=False):
                with self.assertRaises(ProbeBusy):RecoveryOwnership(lock,j).__enter__()
    def test_mismatch_instance(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d,journal_instance='other')
            with patch('polaris_probe_cart_recovery._pid_alive',return_value=False):
                with self.assertRaises(ProbeBusy):RecoveryOwnership(lock,j).__enter__()
    def test_corrupt_lock(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d,corrupt_lock=True)
            with self.assertRaises(ProbeBusy):RecoveryOwnership(lock,j).__enter__()
    def test_corrupt_journal(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d,corrupt_journal=True)
            with self.assertRaises(ProbeBusy):RecoveryOwnership(lock,j).__enter__()
    def test_racing_recovery(self):
        with tempfile.TemporaryDirectory() as d:
            lock,j=self.prepare(d)
            with patch('polaris_probe_cart_recovery._pid_alive',return_value=False):
                with RecoveryOwnership(lock,j):
                    with self.assertRaises(ProbeBusy):RecoveryOwnership(lock,j).__enter__()
    def test_add_mutates_then_raises_no_price(self):
        f=FaultFake();f.raise_after=True
        with tempfile.TemporaryDirectory() as d:
            lock=Path(d)/'lock';j=Path(d)/'journal'
            r=run_probe(OEM,f,lock_path=lock,journal_path=j)
            self.assertNotIn(r['status'],('ADMIN_PROBE_CART_PRICE_VERIFIED',))
            self.assertIsNone(r['admin_unit_price_usd'])
    def test_recovery_ambiguous_current(self):
        with tempfile.TemporaryDirectory() as d:
            f=Fake([line(OEM,'created'),line('9999999','intruder')])
            delta=prove_delta({'lines':[]},{'lines':[line(OEM,'created')]},OEM)
            lock,j=self.prepare(d,delta=delta)
            with patch('polaris_probe_cart_recovery._pid_alive',return_value=False):
                r=recover_probe(f,lock_path=lock,journal_path=j)
            self.assertEqual(r['status'],'RECOVERY_REQUIRED')
            self.assertEqual(len(f.lines),2)
    def test_remove_timeout_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            f=FaultFake();f.fail_cleanup=True
            r=run_probe(OEM,f,lock_path=Path(d)/'lock',journal_path=Path(d)/'journal')
            self.assertEqual(r['status'],'RESTORATION_FAILED')
            self.assertTrue((Path(d)/'journal').exists())
            self.assertTrue((Path(d)/'lock').exists())
if __name__=='__main__':unittest.main()
