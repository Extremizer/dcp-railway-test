"""Actual legacy→v2 handlers on disposable SQLite; maintenance is mock only."""
import ast,asyncio,hashlib,json,logging,os,shutil,socket,sqlite3,tempfile,unittest
from contextlib import closing
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import dcp_legacy_bootstrap_test_support as old
import dp_live_bridge as v2,dp_live_health as health,dp_live_admission as admission,dp_live_quiescence as quiescence
import dp_result_completion as completion,dcp_price_gateway as gateway
from dcp_admission_test_support import approve_mode
from test_dp_result_completion import build_http,headers as v2_headers
import test_dcp_producer_v2 as producers

class BootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.db=Path(self.tmp.name)/'orders.db'
        env=patch.dict(os.environ,{'EXTREMIZER_DP_HEALTH_ENFORCE':'1','EXTREMIZER_DP_MIN_LOOKUP_GAP_SECONDS':'0'})
        env.start();self.addCleanup(env.stop)
        network=patch.object(socket.socket,'connect',side_effect=AssertionError('external network forbidden'));network.start();self.addCleanup(network.stop)
        self.old=old.bridge();self.old.init(self.db);health.set_health(self.db,'READY')
        with sqlite3.connect(self.db) as c:
            completion._write_cache(c,'Arctic Cat','2670-265',99,'verified_local_dcp:server_html:#add_2670-265 .c2 .dbl',health._now())
            c.execute('CREATE TABLE orders(id INTEGER PRIMARY KEY,marker TEXT)');c.execute("INSERT INTO orders VALUES(1,'unchanged')")
            c.execute('CREATE TABLE finance_ledger(id INTEGER PRIMARY KEY,amount REAL)');c.execute('INSERT INTO finance_ledger VALUES(1,100)')
        self.client=old.http(self.db,self.old);self.addCleanup(self.client.close)
        self.producer=True;self.inflight=False;self.unknown=False;self.maintenance=False
        self.writers={'probnik':True,'procenka':True,'shortage':True,'oemixi_direct':True}
    def invariant(self):
        with sqlite3.connect(self.db) as c:return {t:c.execute('SELECT * FROM '+t+' ORDER BY rowid').fetchall() for t in ['orders','finance_ledger','dealer_price_cache']}
    def counts(self):
        with sqlite3.connect(self.db) as c:return dict(c.execute('SELECT status,COUNT(*) FROM dp_live_requests GROUP BY status'))
    def queue(self,oem='2670-265'):return self.old.request_live_dp(self.db,'Arctic Cat',oem,wait_seconds=0)
    def claim(self):
        r=self.client.get('/internal/dp-sync/next',headers=old.headers('/internal/dp-sync/next'));self.assertEqual(r.status_code,200);return r.json()['request']
    def finish(self,claim):
        r=self.client.post('/internal/dp-sync/result',headers=old.headers('/internal/dp-sync/result'),json={'request_id':claim['request_id'],'status':'FOUND','manufacturer':'Arctic Cat','current_oem':'2670-265','dealer_price_usd':31.25,'source':'server_html:#add_2670-265 .c2 .dbl'})
        self.assertEqual(r.status_code,200,r.text)
    def cessation(self,*,owner_window=False,independent=False,evidence=None):
        if any(self.writers.values()) or self.unknown or self.inflight or not owner_window or not independent or evidence!='a'*64:raise RuntimeError('BLOCKED: cessation not proven')
        self.maintenance=True
    def window(self):
        self.writers={k:False for k in self.writers};self.cessation(owner_window=True,independent=True,evidence='a'*64);self.producer=False
    def bootstrap(self):
        if not self.maintenance or self.producer or self.inflight or any(self.writers.values()) or self.unknown or self.counts().get('pending',0) or self.counts().get('claimed',0):raise RuntimeError('BLOCKED: cutover prerequisites')
        v2.init(self.db);client=build_http(self.db);self.addCleanup(client.close);return client
    def mock_live(self,producer,approval):
        if (not isinstance(approval,dict) or approval.get('scope')!='LIVE_E2E'
            or approval.get('owner_approved') is not True or not approval.get('independent_reviewer')):
            raise RuntimeError('BLOCKED: separate LIVE E2E approval required')
        return producer.agent.run_once()
    def test_producer_stop_does_not_stop_legacy_enqueue(self):
        self.producer=False;self.assertEqual(self.queue()['status'],'TIMEOUT');self.assertEqual(self.counts()['pending'],1)
        with self.assertRaises(RuntimeError):self.cessation(owner_window=True,independent=True,evidence='a'*64)
    def test_real_cache_only_procenka_still_enqueues(self):
        ns={'datetime':datetime,'asyncio':asyncio,'DCP_CACHE_ONLY':True,'ORDERS_DB_FILE':self.db,'get_dealer_price_cache':lambda *a,**k:None,'upsert_dealer_price_cache':Mock(),'log':logging.getLogger('legacy'),
            'dp_live_bridge':SimpleNamespace(request_live_dp=lambda db,m,o,**kw:self.old.request_live_dp(db,m,o,wait_seconds=0))}
        node=next(n for n in ast.parse(old.source('extremizer_bot.py')).body if isinstance(n,ast.AsyncFunctionDef));exec(compile(ast.Module(body=[node],type_ignores=[]),'legacy_procenka','exec'),ns)
        self.producer=False;r=asyncio.run(ns['enrich_found_result_with_dealer_price']({'status':'FOUND','catalog':'Parts','manufacturer':'Arctic Cat','oem':'2670-265'}))
        self.assertEqual(r['_dealer_price_live_status'],'TIMEOUT');self.assertEqual(self.counts()['pending'],1)
    def test_legacy_timeout_rows_do_not_prove_browser_stopped(self):
        self.queue();self.claim();self.inflight=True
        with sqlite3.connect(self.db) as c:c.execute('UPDATE dp_live_requests SET requested_at=?',((datetime.now(timezone.utc)-timedelta(seconds=35)).isoformat(),))
        self.queue('9999');self.assertEqual(self.counts().get('claimed',0),0);self.assertTrue(self.inflight)
        self.writers={k:False for k in self.writers}
        with self.assertRaises(RuntimeError):self.cessation(owner_window=True,independent=True,evidence='a'*64)
    def test_unknown_writer_or_missing_window_evidence_blocks(self):
        self.writers={k:False for k in self.writers};self.unknown=True
        with self.assertRaises(RuntimeError):self.cessation(owner_window=True,independent=True,evidence='a'*64)
        self.unknown=False
        for kw in ({'independent':True,'evidence':'a'*64},{'owner_window':True,'evidence':'a'*64},{'owner_window':True,'independent':True}):
            with self.assertRaises(RuntimeError):self.cessation(**kw)
    def test_bootstrap_requires_old_producer_stop_and_empty_queue(self):
        with self.assertRaises(RuntimeError):self.bootstrap()
        self.writers={k:False for k in self.writers};self.cessation(owner_window=True,independent=True,evidence='a'*64)
        with self.assertRaises(RuntimeError):self.bootstrap()
        self.producer=False;self.queue()
        with self.assertRaises(RuntimeError):self.bootstrap()
    def test_full_cutover_verified_checkpoint_closed_smoke_then_separate_go(self):
        self.queue();self.finish(self.claim());before=self.invariant();self.window()
        backup=Path(self.tmp.name)/'checkpoint.db'
        with closing(sqlite3.connect(self.db)) as src,closing(sqlite3.connect(backup)) as dst:src.backup(dst)
        with sqlite3.connect(backup) as c:self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        copied=Path(self.tmp.name)/'roundtrip.db';shutil.copyfile(backup,copied);self.assertEqual(hashlib.sha256(backup.read_bytes()).digest(),hashlib.sha256(copied.read_bytes()).digest())
        self.assertEqual(backup.stat().st_size,copied.stat().st_size)
        v2.init(copied) # Candidate migration on independently restored disposable checkpoint.
        with sqlite3.connect(copied) as c:
            self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(admission.read_state(c)['mode'],'CLOSED')
            self.assertEqual(c.execute('SELECT amount FROM finance_ledger').fetchone()[0],100)
            self.assertEqual(c.execute('SELECT dealer_price_usd FROM dealer_price_cache').fetchone()[0],31.25)
        client=self.bootstrap();self.assertEqual(self.invariant(),before)
        self.assertEqual(v2.request_live_dp(self.db,'Honda','91303-PA9-004',wait_seconds=0)['status'],'ADMISSION_CLOSED')
        self.assertIsNone(client.get('/internal/dp-sync/next',headers=v2_headers('GET','/internal/dp-sync/next')).json()['request'])
        self.assertIsNone(client.get('/internal/dp-sync/next',headers=old.headers('/internal/dp-sync/next')).json()['request'])
        self.assertEqual(quiescence.preflight(self.db)['gate'],'BLOCKED')
        hit=gateway.request_price(self.db,'Arctic Cat','2670-265',max_age_hours=1,live_request=Mock(side_effect=AssertionError('closed cache lookup must not enqueue')))
        self.assertEqual(hit['dealer_price_usd'],31.25)
        approve_mode(self.db,'OPEN') # Separate signed mock RC admission approval.
        fx=producers.ProducerV2Tests(methodName='runTest');fx.setUp();self.addCleanup(fx.doCleanups);fx.db=self.db;fx.client=client
        fx.browser.get_dealer_price_dict.return_value={'status':'FOUND','manufacturer':'Honda','query_oem':'91303-PA9-004','matched_sku':'91303-PA9-004','current_oem':'91303-PA9-004','dealer_price_usd':5,'source':'server_html:#add_91303-PA9-004 .c2 .dbl'}
        v2.request_live_dp(self.db,'Honda','91303-PA9-004',wait_seconds=0)
        with self.assertRaises(RuntimeError):self.mock_live(fx,None)
        fx.browser.get_dealer_price_dict.assert_not_called()
        self.assertTrue(self.mock_live(fx,{'scope':'LIVE_E2E','owner_approved':True,'independent_reviewer':'mock-reviewer'}))
        fx.browser.get_dealer_price_dict.assert_called_once()
        cached=gateway.request_price(self.db,'Honda','91303-PA9-004',max_age_hours=1,live_request=Mock(side_effect=AssertionError('second lookup')))
        self.assertEqual(cached['dealer_price_usd'],5)
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute('SELECT amount FROM finance_ledger').fetchone()[0],100)
    def test_additive_restart_preserves_old_rows_cache_finance(self):
        self.queue();self.finish(self.claim());before=self.invariant()
        with sqlite3.connect(self.db) as c:cols=[r[1] for r in c.execute('PRAGMA table_info(dp_live_requests)')];rows=c.execute('SELECT '+','.join(cols)+' FROM dp_live_requests').fetchall()
        self.window();self.bootstrap();v2.init(self.db)
        with sqlite3.connect(self.db) as c:self.assertEqual(c.execute('SELECT '+','.join(cols)+' FROM dp_live_requests').fetchall(),rows);self.assertEqual(admission.read_state(c)['mode'],'CLOSED')
        self.assertEqual(before,self.invariant())
    def test_bootstrap_audit_failure_safe_but_not_full_ddl_rollback_claim(self):
        before=self.invariant()
        with sqlite3.connect(self.db) as c:
            admission.initialize(c);c.execute('DELETE FROM dp_live_admission');c.execute('DELETE FROM dp_live_admission_events');c.execute("CREATE TRIGGER abort_bootstrap BEFORE INSERT ON dp_live_admission_events BEGIN SELECT RAISE(ABORT,'failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):v2.init(self.db)
        self.assertEqual(self.invariant(),before);self.assertEqual(quiescence.preflight(self.db)['gate'],'BLOCKED')
        with sqlite3.connect(self.db) as c:self.assertFalse(admission.is_open(c));c.execute('DROP TRIGGER abort_bootstrap')
        v2.init(self.db)
        with sqlite3.connect(self.db) as c:self.assertEqual(admission.read_state(c)['mode'],'CLOSED')
    def test_old_backup_restore_would_lose_subsequent_business_write(self):
        backup=Path(self.tmp.name)/'checkpoint.db'
        with closing(sqlite3.connect(self.db)) as src,closing(sqlite3.connect(backup)) as dst:src.backup(dst)
        with sqlite3.connect(self.db) as c:c.execute("INSERT INTO orders VALUES(2,'later legitimate write')")
        current=self.invariant();restored=Path(self.tmp.name)/'restore.db';shutil.copyfile(backup,restored)
        with sqlite3.connect(restored) as c:self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok');self.assertNotEqual(c.execute('SELECT * FROM orders ORDER BY id').fetchall(),current['orders'])
        self.assertEqual(self.invariant(),current)
    def test_v1_rollback_denied_with_fence_or_unknown_journal(self):
        self.window();self.bootstrap();approve_mode(self.db,'OPEN');v2.request_live_dp(self.db,'Honda','91303-PA9-004',wait_seconds=0);self.assertIsNotNone(v2.claim_next(self.db,producer_id='mock-v2'));self.assertIsNone(v2.claim_next(self.db,producer_id='second-v2'));approve_mode(self.db,'CLOSED')
        result=quiescence.preflight(self.db,purpose='rollback');self.assertIn('UNRECONCILED_REQUESTS',result['blockers']);self.assertIn('MANUAL_EVIDENCE_REQUIRED',result['blockers'])
        with self.assertRaises(admission.AdmissionError):approve_mode(self.db,'OPEN')

    def test_admission_open_is_not_live_e2e_permission(self):
        producer=SimpleNamespace(agent=SimpleNamespace(run_once=Mock(side_effect=AssertionError('unapproved lookup'))))
        for approval in (None,{'scope':'ADMISSION_OPEN','owner_approved':True,'independent_reviewer':'mock-reviewer'},
                         {'scope':'LIVE_E2E','owner_approved':False,'independent_reviewer':'mock-reviewer'}):
            with self.assertRaises(RuntimeError):self.mock_live(producer,approval)
        producer.agent.run_once.assert_not_called()

    def test_actual_v1_agent_functions_against_actual_v1_http_fake_browser(self):
        import io,time,urllib.request
        self.queue()
        lookup=Mock(return_value={'status':'FOUND','manufacturer':'Arctic Cat','query_oem':'2670-265',
            'matched_sku':'2670-265','current_oem':'2670-265','dealer_price_usd':31.25,
            'source':'server_html:#add_2670-265 .c2 .dbl'})
        ns={'hashlib':hashlib,'hmac':__import__('hmac'),'json':json,'time':time,'urllib':__import__('urllib'),
            '_key':lambda:old.KEY,'BASE_URL':'https://isolated-legacy.invalid',
            'oemixibot_dcp_price':SimpleNamespace(get_dealer_price_dict=lookup),
            '_set_dcp_health':Mock(),'_write_status':Mock()}
        exec(compile(old.source('dp_sync_agent.py'),'legacy_agent','exec'),ns)
        requests=[]
        def transport(req,timeout):
            path=req.full_url.removeprefix(ns['BASE_URL']);requests.append(path)
            r=self.client.request(req.get_method(),path,content=req.data,headers=dict(req.header_items()))
            self.assertEqual(r.status_code,200,r.text);return io.BytesIO(r.content)
        with patch('urllib.request.urlopen',side_effect=transport):self.assertTrue(ns['run_once']())
        lookup.assert_called_once();self.assertEqual(requests,['/internal/dp-sync/next','/internal/dp-sync/result'])
        self.assertEqual(self.counts(),{'finished':1})
        self.assertEqual(self.invariant()['dealer_price_cache'][0][2],31.25)
