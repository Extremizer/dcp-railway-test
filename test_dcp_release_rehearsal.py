"""Paired release rehearsal: actual HTTP producer + broker, fake browser only."""
from datetime import datetime, timezone, timedelta
import hashlib
import hmac
import sqlite3
import time
import unittest
from unittest.mock import Mock

from dcp_admission_test_support import approve_mode
import dp_live_admission as admission
import dp_live_bridge as bridge
import dp_live_quiescence as quiescence
import dp_live_health as health
import test_dcp_producer_v2 as producer_tests
import test_dcp_three_adapters as adapter_tests
from test_dp_result_completion import KEY, RAW_SOURCE
import dp_result_completion as completion


class PairedReleaseRehearsalTests(unittest.TestCase):
    def fixture(self,cls=producer_tests.ProducerV2Tests):
        fixture=cls(methodName='runTest');fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def assert_legacy_denied(self,fx):
        ts=int(time.time());path='/internal/dp-sync/next'
        signature=hmac.new(KEY,f'v1\n{path}\n{ts}'.encode(),hashlib.sha256).hexdigest()
        r=fx.client.get(path,headers={'X-DP-Sync-Ts':str(ts),'X-DP-Sync-Sig':signature})
        self.assertEqual(r.status_code,200);self.assertIsNone(r.json()['request'])
        self.assertEqual(r.json()['error_code'],'producer_protocol_upgrade_required')

    def test_full_open_closed_existing_completion_empty_queue_v2_smoke_explicit_go(self):
        fx=self.fixture();browser_price=fx.browser.get_dealer_price_dict.return_value.copy()
        fx.enqueue();before=fx.snapshot()['finance_ledger']
        with sqlite3.connect(fx.db) as c:
            c.execute('CREATE TABLE dp_write_probe(marker TEXT)')
            c.execute("CREATE TRIGGER count_dp_writes AFTER UPDATE OF dealer_price_usd,source,last_verified_at ON dealer_price_cache BEGIN INSERT INTO dp_write_probe VALUES('dp_write'); END")
        def browser(manufacturer,oem):
            approve_mode(fx.db,'CLOSED')
            self.assertEqual(bridge.request_live_dp(fx.db,'Honda','91303-PA9-004',wait_seconds=0)['status'],'ADMISSION_CLOSED')
            self.assertIsNone(bridge.claim_next(fx.db,producer_id='second-producer'))
            return browser_price
        fx.browser.get_dealer_price_dict.side_effect=browser
        self.assertTrue(fx.agent.run_once())
        fx.browser.get_dealer_price_dict.assert_called_once()
        self.assertEqual(len(fx.requests),2) # one signed claim, one signed result
        self.assertEqual(fx.snapshot()['finance_ledger'],before)
        s=quiescence.preflight(fx.db)
        self.assertEqual(s['counts'],{'pending':0,'claimed':0,'orphaned':0,'other_unfinished':0})
        self.assertEqual(s['gate'],'BLOCKED') # SQLite alone cannot authorize runtime switch.
        self.assert_legacy_denied(fx)
        self.assertIsNone(fx.agent._claim()) # v2 smoke on CLOSED returns no request.
        with sqlite3.connect(fx.db) as c:self.assertEqual(admission.read_state(c)['mode'],'CLOSED')
        explicit_mock_go=approve_mode(fx.db,'OPEN')
        self.assertEqual(explicit_mock_go['mode'],'OPEN')
        fx.browser.get_dealer_price_dict.assert_called_once()
        self.assertEqual(fx.snapshot()['dealer_price_cache'][0][2],31.25)
        self.assertEqual(len(fx.snapshot()['dp_write_probe']),1)

    def test_three_real_adapters_preserve_fresh_cache_hits_when_closed(self):
        fx=self.fixture(adapter_tests.ThreeAdapterTests)
        with sqlite3.connect(fx.db) as c:completion._write_cache(c,'Arctic Cat','2670-265',31.25,'verified_local_dcp:'+RAW_SOURCE,health._now())
        approve_mode(fx.db,'CLOSED')
        with sqlite3.connect(fx.db) as c:
            c.execute('CREATE TABLE dp_write_probe(marker TEXT)')
            c.execute("CREATE TRIGGER count_dp_writes AFTER UPDATE OF dealer_price_usd,source,last_verified_at ON dealer_price_cache BEGIN INSERT INTO dp_write_probe VALUES('dp_write'); END")
        forbidden=Mock(side_effect=AssertionError('fresh cache must not enqueue/live lookup'))
        def lookup(m,o,*,max_age_hours=168):
            import dcp_price_gateway
            return dcp_price_gateway.request_price(fx.db,m,o,max_age_hours=max_age_hours,live_request=forbidden)
        values=[fn() for fn in fx.adapters(lookup)]
        self.assertEqual(values[0]['dp_usd'],31.25)
        self.assertEqual(values[1]['_dealer_price_usd'],31.25)
        self.assertEqual(values[2]['dl_usd'],31.25)
        forbidden.assert_not_called();fx.browser.get_dealer_price_dict.assert_not_called()
        self.assertEqual(len(fx.snapshot()['dp_live_requests']),0)
        self.assertEqual(len(fx.snapshot()['dp_write_probe']),0)

    def test_three_real_adapters_reject_new_misses_while_closed(self):
        fx=self.fixture(adapter_tests.ThreeAdapterTests);approve_mode(fx.db,'CLOSED')
        def lookup(m,o,*,max_age_hours=168):
            import dcp_price_gateway
            return dcp_price_gateway.request_price(fx.db,m,o,max_age_hours=max_age_hours,wait_seconds=0)
        values=[fn() for fn in fx.adapters(lookup)]
        self.assertIsNone(values[0]['dp_usd']);self.assertNotIn('_dealer_price_usd',values[1]);self.assertIsNone(values[2]['dl_usd'])
        self.assertEqual(len(fx.snapshot()['dp_live_requests']),0)
        fx.browser.get_dealer_price_dict.assert_not_called()

    def test_uncertain_commit_keeps_closed_and_requires_manual_journal_evidence(self):
        fx=self.fixture();raw=fx.browser.get_dealer_price_dict.return_value.copy();fx.enqueue()
        def lookup(*args):approve_mode(fx.db,'CLOSED');return raw
        fx.browser.get_dealer_price_dict.side_effect=lookup;fx.drop_after_commit=True
        with self.assertRaises(TimeoutError):fx.agent.run_once()
        self.assertFalse(fx.agent.run_once());fx.browser.get_dealer_price_dict.assert_called_once()
        self.assertTrue(fx.agent.JOURNAL_FILE.exists())
        s=quiescence.preflight(fx.db)
        self.assertEqual(s['counts']['claimed'],0);self.assertEqual(s['gate'],'BLOCKED')
        self.assertIn('MANUAL_EVIDENCE_REQUIRED',s['blockers'])
        bridge.init(fx.db);health.set_health(fx.db,'READY')
        with sqlite3.connect(fx.db) as c:self.assertEqual(admission.read_state(c)['mode'],'CLOSED')

    def test_orphan_and_rollback_cannot_reopen_or_retry(self):
        fx=self.fixture();claim=fx.claim();approve_mode(fx.db,'CLOSED');fx.expire_and_quarantine(claim)
        result=quiescence.preflight(fx.db,purpose='rollback')
        self.assertEqual(result['counts']['orphaned'],1);self.assertEqual(result['gate'],'BLOCKED')
        self.assertIn('UNRECONCILED_REQUESTS',result['blockers'])
        with self.assertRaises(admission.AdmissionError):approve_mode(fx.db,'OPEN')
        self.assertIsNone(bridge.claim_next(fx.db,producer_id='replacement'))
        fx.browser.get_dealer_price_dict.assert_not_called()

    def test_cloudflare_auth_stop_and_no_automatic_open(self):
        for status in ('CLOUDFLARE','AUTH_REQUIRED'):
            fx=self.fixture();fx.enqueue()
            def blocked(*args):approve_mode(fx.db,'CLOSED');return {'status':status}
            fx.browser.get_dealer_price_dict.side_effect=blocked
            self.assertTrue(fx.agent.run_once());self.assertTrue(fx.agent._PRODUCER_STOP)
            self.assertFalse(fx.agent.run_once());fx.browser.get_dealer_price_dict.assert_called_once()
            bridge.init(fx.db);health.set_health(fx.db,'READY')
            with sqlite3.connect(fx.db) as c:self.assertEqual(admission.read_state(c)['mode'],'CLOSED')
