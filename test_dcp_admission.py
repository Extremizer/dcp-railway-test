"""Real SQLite locks + signed mock RC approval; no network or browser."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch

import dp_live_admission as admission
import dp_live_quiescence as quiescence
import dp_live_bridge as bridge
import dp_live_health as health
import dp_claim_recovery as recovery
from dcp_admission_test_support import MOCK_APPROVAL_KEY, approve_mode, approval_for
from test_dp_result_completion import CompletionFixture


class AdmissionTests(CompletionFixture):
    def state(self):
        with sqlite3.connect(self.db) as c:return admission.read_state(c)

    def test_default_closed_and_init_restart_never_open(self):
        db=Path(self.tmp.name)/'new.db';bridge.init(db)
        for _ in range(3):bridge.init(db);health.set_health(db,'READY')
        with sqlite3.connect(db) as c:
            self.assertEqual(admission.read_state(c)['mode'],'CLOSED')
            self.assertEqual(c.execute('SELECT COUNT(*) FROM dp_live_admission_events').fetchone()[0],1)
        self.assertEqual(bridge.request_live_dp(db,'Honda','91303-PA9-004',wait_seconds=0)['status'],'ADMISSION_CLOSED')
        self.assertIsNone(bridge.claim_next(db))

    def test_closed_rejects_enqueue_and_preserves_all_request_rows(self):
        claimed=self.claim();self.enqueue('Honda','91303-PA9-004')
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO dp_live_requests(request_id,manufacturer,oem,status,requested_at,claim_generation) VALUES('orphan','Honda','9999','orphaned',?,1)",(health._now(),))
        before=self.snapshot()['dp_live_requests'];approve_mode(self.db,'CLOSED')
        self.assertEqual(bridge.request_live_dp(self.db,'Yamaha','9999',wait_seconds=0)['status'],'ADMISSION_CLOSED')
        self.assertIsNone(bridge.claim_next(self.db))
        self.assertEqual(self.snapshot()['dp_live_requests'],before)
        self.assertEqual(self.post(self.payload(claimed)).status_code,200)

    def test_existing_fenced_completion_works_while_closed(self):
        claim=self.claim();approve_mode(self.db,'CLOSED')
        self.assertEqual(self.post(self.payload(claim)).status_code,200)
        self.assertEqual(self.state()['mode'],'CLOSED')
        self.assertTrue(self.snapshot()['dp_live_requests'][0][-1])
        before=self.snapshot();self.assertEqual(self.post(self.payload(claim,dealer_price_usd=999)).status_code,409)
        self.assertEqual(self.snapshot(),before)

    def test_expired_claim_quarantines_while_closed_without_unfreeze(self):
        claim=self.claim();approve_mode(self.db,'CLOSED');self.expire_and_quarantine(claim)
        self.assertEqual(self.state()['mode'],'CLOSED')
        self.assertEqual(quiescence.preflight(self.db)['counts']['orphaned'],1)
        self.assertEqual(self.post(self.payload(claim)).status_code,409)

    def test_no_open_from_ready_restart_expiry_or_manual_recovery(self):
        claim=self.claim();approve_mode(self.db,'CLOSED');self.expire_and_quarantine(claim)
        with sqlite3.connect(self.db) as c:c.execute('UPDATE dp_live_requests SET orphaned_at=?',((datetime.now(timezone.utc)-timedelta(seconds=2)).isoformat(),))
        health.set_health(self.db,'READY');plan=recovery.recovery_plan(self.db,claim['request_id'])
        recovery.revoke_orphan(self.db,claim['request_id'],approved_plan_sha256=plan['plan_sha256'],operator='mock-admin',producer_inactive_confirmed=True,evidence_sha256='b'*64)
        bridge.init(self.db);health.set_health(self.db,'READY')
        self.assertEqual(self.state()['mode'],'CLOSED');self.assertIsNone(bridge.claim_next(self.db))

    def test_unsigned_wrong_key_wrong_scope_and_body_tampering_denied(self):
        record,sig=approval_for(self.db,'CLOSED');before=self.snapshot()
        cases=[(record,''),(record,admission.sign_approval(b'producer-key-is-not-rc-authority-xxx',record)),
               ({**record,'actor':'different'},sig)]
        wrong,wsig=approval_for(self.db,'CLOSED',authority='producer')
        cases.append((wrong,wsig))
        wrong,wsig=approval_for(self.db,'CLOSED',scope='dp.admission.open')
        cases.append((wrong,wsig))
        for value,signature in cases:
            with self.assertRaises(admission.AdmissionError):admission.set_admission(self.db,'CLOSED',approval=value,signature=signature,approval_key=MOCK_APPROVAL_KEY)
            self.assertEqual(self.snapshot(),before)

    def test_replay_stale_revision_cross_db_and_expired_approval_denied(self):
        record,sig=approval_for(self.db,'CLOSED');approve_mode(self.db,'CLOSED')
        with self.assertRaises(admission.AdmissionError):admission.set_admission(self.db,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
        other=Path(self.tmp.name)/'other.db';bridge.init(other)
        with self.assertRaises(admission.AdmissionError):admission.set_admission(other,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
        now=datetime.now(timezone.utc)
        for times in ({'issued_at':(now-timedelta(minutes=10)).isoformat(),'expires_at':(now-timedelta(minutes=5)).isoformat()},
                      {'issued_at':now.isoformat(),'expires_at':(now+timedelta(hours=1)).isoformat()}):
            value,sig=approval_for(self.db,'OPEN',**times)
            with self.assertRaises(admission.AdmissionError):admission.set_admission(self.db,'OPEN',approval=value,signature=sig,approval_key=MOCK_APPROVAL_KEY)

    def test_open_blocks_pending_claimed_and_orphaned(self):
        r=self.enqueue();approve_mode(self.db,'CLOSED')
        for status in ('pending','claimed','orphaned'):
            with sqlite3.connect(self.db) as c:c.execute('UPDATE dp_live_requests SET status=?',(status,))
            before=self.snapshot()
            with self.assertRaisesRegex(admission.AdmissionError,'unreconciled'):approve_mode(self.db,'OPEN')
            self.assertEqual(self.snapshot(),before)

    def test_audit_failure_rolls_back_mode_and_requests(self):
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TRIGGER reject_admission_audit BEFORE INSERT ON dp_live_admission_events BEGIN SELECT RAISE(ABORT,'audit failure'); END")
        before=self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):approve_mode(self.db,'CLOSED')
        self.assertEqual(self.snapshot(),before)

    def test_state_update_failure_rolls_back_inserted_audit(self):
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TRIGGER reject_admission_update BEFORE UPDATE ON dp_live_admission BEGIN SELECT RAISE(ABORT,'state failure'); END")
        before=self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):approve_mode(self.db,'CLOSED')
        self.assertEqual(self.snapshot(),before)

    def test_deferred_commit_failure_rolls_back_audit_and_mode(self):
        with sqlite3.connect(self.db) as c:
            c.execute('CREATE TABLE audit_parent(id INTEGER PRIMARY KEY)')
            c.execute('CREATE TABLE audit_fk(id INTEGER REFERENCES audit_parent(id) DEFERRABLE INITIALLY DEFERRED)')
            c.execute('CREATE TRIGGER invalid_audit_fk AFTER INSERT ON dp_live_admission_events BEGIN INSERT INTO audit_fk VALUES(999); END')
        before=self.snapshot()
        with self.assertRaises(sqlite3.IntegrityError):approve_mode(self.db,'CLOSED')
        self.assertEqual(self.snapshot(),before)

    def test_concurrent_control_same_revision_has_one_audit_change(self):
        record,sig=approval_for(self.db,'CLOSED');barrier=threading.Barrier(2)
        def close():
            barrier.wait(timeout=2)
            try:return admission.set_admission(self.db,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)['mode']
            except admission.AdmissionError:return 'DENIED'
        with ThreadPoolExecutor(max_workers=2) as p:values=list(p.map(lambda _:close(),range(2)))
        self.assertCountEqual(values,['CLOSED','DENIED'])
        self.assertEqual(len(self.snapshot()['dp_live_admission_events']),3) # bootstrap, mock OPEN, one CLOSED

    def test_freeze_enqueue_race_serialized_at_sqlite_write_lock(self):
        # Force freeze to win its transaction while enqueue waits at BEGIN IMMEDIATE.
        entered=threading.Event();release=threading.Event();original=admission._plan
        def holding_plan(c,db,target):
            value=original(c,db,target)
            entered.set();self.assertTrue(release.wait(timeout=3));return value
        record,sig=approval_for(self.db,'CLOSED')
        with ThreadPoolExecutor(max_workers=2) as p, patch.object(admission,'_plan',side_effect=holding_plan):
            close=p.submit(admission.set_admission,self.db,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
            self.assertTrue(entered.wait(timeout=2))
            enqueue=p.submit(bridge.request_live_dp,self.db,'Honda','91303-PA9-004',wait_seconds=0)
            release.set();self.assertEqual(close.result(timeout=4)['mode'],'CLOSED')
            self.assertEqual(enqueue.result(timeout=4)['status'],'ADMISSION_CLOSED')
        self.assertEqual(len(self.snapshot()['dp_live_requests']),0)

    def test_enqueue_before_freeze_preserved_but_cannot_claim_after_closed(self):
        queued=self.enqueue();approve_mode(self.db,'CLOSED')
        self.assertIsNone(bridge.claim_next(self.db))
        self.assertEqual(self.snapshot()['dp_live_requests'][0][0],queued['request_id'])
        self.assertEqual(self.snapshot()['dp_live_requests'][0][3],'pending')

    def test_freeze_claim_race_serialized(self):
        self.enqueue();entered=threading.Event();release=threading.Event();original=admission._plan
        record,sig=approval_for(self.db,'CLOSED')
        def holding(c,db,target):
            value=original(c,db,target);entered.set();self.assertTrue(release.wait(timeout=3));return value
        with ThreadPoolExecutor(max_workers=2) as p,patch.object(admission,'_plan',side_effect=holding):
            close=p.submit(admission.set_admission,self.db,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
            self.assertTrue(entered.wait(timeout=2));claim=p.submit(bridge.claim_next,self.db)
            release.set();close.result(timeout=4);self.assertIsNone(claim.result(timeout=4))
        self.assertEqual(self.snapshot()['dp_live_requests'][0][3],'pending')

    def test_public_and_producer_control_routes_do_not_exist(self):
        before=self.snapshot()
        for path in ('/internal/dp-sync/admission','/internal/dp-sync/freeze','/api/dcp/admission'):
            self.assertEqual(self.client.post(path,json={'mode':'OPEN'}).status_code,404)
        self.assertEqual(self.snapshot(),before)

    def test_enqueue_wins_lock_then_close_preserves_pending(self):
        entered=threading.Event();release=threading.Event();original=admission.is_open
        record,sig=approval_for(self.db,'CLOSED')
        def hold(c):
            value=original(c);entered.set();self.assertTrue(release.wait(timeout=3));return value
        with ThreadPoolExecutor(max_workers=2) as p,patch.object(admission,'is_open',side_effect=hold):
            request=p.submit(bridge.request_live_dp,self.db,'Honda','91303-PA9-004',wait_seconds=0)
            self.assertTrue(entered.wait(timeout=2))
            close=p.submit(admission.set_admission,self.db,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
            release.set();self.assertEqual(request.result(timeout=4)['status'],'TIMEOUT');close.result(timeout=4)
        self.assertEqual(self.state()['mode'],'CLOSED');self.assertEqual(self.snapshot()['dp_live_requests'][0][3],'pending')

    def test_claim_wins_lock_then_close_existing_claim_completes(self):
        self.enqueue();entered=threading.Event();release=threading.Event();original=admission.is_open
        record,sig=approval_for(self.db,'CLOSED')
        def hold(c):
            value=original(c);entered.set();self.assertTrue(release.wait(timeout=3));return value
        with ThreadPoolExecutor(max_workers=2) as p,patch.object(admission,'is_open',side_effect=hold):
            claim=p.submit(bridge.claim_next,self.db,producer_id='mock-producer-1')
            self.assertTrue(entered.wait(timeout=2))
            close=p.submit(admission.set_admission,self.db,'CLOSED',approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
            release.set();owned=claim.result(timeout=4);close.result(timeout=4)
        self.assertEqual(self.state()['mode'],'CLOSED');self.assertEqual(self.post(self.payload(owned)).status_code,200)


class QuiescenceTests(CompletionFixture):
    def evidence(self,snapshot):
        return {'source':'independent_operator_attestation','actor':'mock-independent-operator',
                'snapshot_sha256':snapshot['snapshot_sha256'],'evidence_sha256':'b'*64,
                'observed_at':datetime.now(timezone.utc).isoformat(),
                'producer_stopped':True,'singleton_verified':True,'no_other_producer':True,
                'browser_inflight_zero':True,'result_delivery_inflight_zero':True,'uncertainty_journal_reconciled':True}

    def test_preflight_is_read_only_and_empty_sqlite_is_not_runtime_proof(self):
        approve_mode(self.db,'CLOSED');before=self.snapshot()
        result=quiescence.preflight(self.db)
        self.assertEqual(result['gate'],'BLOCKED');self.assertIn('MANUAL_EVIDENCE_REQUIRED',result['blockers'])
        self.assertEqual(result['runtime_identity'],'NOT PROVEN BY SQLITE')
        self.assertEqual(self.snapshot(),before)

    def test_claim_metadata_only_contains_fence_hash_and_runtime_unproven(self):
        claim=self.claim();approve_mode(self.db,'CLOSED');before=self.snapshot()
        result=quiescence.preflight(self.db);dump=json.dumps(result)
        self.assertNotIn(claim['claim_token'],dump)
        self.assertEqual(result['producer_ids'],[claim['producer_id']])
        self.assertEqual(result['counts']['claimed'],1)
        self.assertIn('UNRECONCILED_REQUESTS',result['blockers']);self.assertEqual(self.snapshot(),before)

    def test_operator_attestation_only_means_ready_for_rc_review(self):
        approve_mode(self.db,'CLOSED');s=quiescence.preflight(self.db)
        result=quiescence.preflight(self.db,operator_evidence=self.evidence(s))
        self.assertEqual(result['gate'],'READY_FOR_RELEASE_CONTROL_REVIEW')
        self.assertFalse(result['production_authorization'])
        self.assertEqual(result['runtime_identity'],'NOT PROVEN BY SQLITE')

    def test_missing_wrong_stale_future_or_boolean_only_evidence_blocked(self):
        approve_mode(self.db,'CLOSED');s=quiescence.preflight(self.db);base=self.evidence(s)
        for change in ({'evidence_sha256':None},{'snapshot_sha256':'a'*64},{'no_other_producer':1},
                       {'observed_at':(datetime.now(timezone.utc)-timedelta(minutes=6)).isoformat()},
                       {'observed_at':(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()}):
            self.assertEqual(quiescence.preflight(self.db,operator_evidence={**base,**change})['gate'],'BLOCKED')

    def test_pending_orphan_and_unreconciled_claim_block_rollback(self):
        claim=self.claim();approve_mode(self.db,'CLOSED')
        result=quiescence.preflight(self.db,purpose='rollback')
        self.assertIn('UNRECONCILED_REQUESTS',result['blockers'])
        self.assertEqual(result['gate'],'BLOCKED')

    def test_read_only_preflight_does_not_quarantine_or_expire_rows(self):
        claim=self.claim();approve_mode(self.db,'CLOSED')
        with sqlite3.connect(self.db) as c:c.execute('UPDATE dp_live_requests SET lease_expires_at=?',((datetime.now(timezone.utc)-timedelta(seconds=5)).isoformat(),))
        before=self.snapshot();result=quiescence.preflight(self.db)
        self.assertTrue(result['requests'][0]['lease_expired'])
        self.assertEqual(result['counts']['claimed'],1);self.assertEqual(self.snapshot(),before)

    def test_cloudflare_auth_stale_health_and_open_admission_block_gate(self):
        self.assertIn('ADMISSION_NOT_CLOSED',quiescence.preflight(self.db)['blockers'])
        approve_mode(self.db,'CLOSED')
        for status in ('CLOUDFLARE','AUTH_REQUIRED'):
            health.set_health(self.db,status)
            self.assertIn('HEALTH_NOT_CONFIRMED_READY',quiescence.preflight(self.db)['blockers'])
        health.set_health(self.db,'READY',checked_at=(datetime.now(timezone.utc)-timedelta(hours=2)).isoformat())
        self.assertIn('HEALTH_NOT_CONFIRMED_READY',quiescence.preflight(self.db)['blockers'])

    def test_missing_db_or_schema_is_blocked_without_creating_schema(self):
        missing=Path(self.tmp.name)/'missing.db'
        self.assertEqual(quiescence.preflight(missing)['gate'],'BLOCKED')
        self.assertFalse(missing.exists())
        empty=Path(self.tmp.name)/'empty.db'
        with sqlite3.connect(empty) as c:c.execute('CREATE TABLE unrelated(marker TEXT)')
        self.assertEqual(quiescence.preflight(empty)['gate'],'BLOCKED')
        with sqlite3.connect(empty) as c:self.assertEqual(c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(),[('unrelated',)])
