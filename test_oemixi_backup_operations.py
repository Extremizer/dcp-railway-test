import copy
import json
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timezone
import oemixi_backup_operations as ops
import oemixi_recurring_backup as backup

NOW = 1800000000
class Store:
    ENV_BUCKET = 'bucket'
    def __init__(self): self.objects={};self.heads={};self.deleted=[]
    def add(self, age):
        epoch = NOW-age
        stamp = datetime.fromtimestamp(epoch,timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        prefix = backup.PREFIX+stamp+'/'
        archive={'bucket':'test-bucket','key':prefix+'snapshot.tar.gz','size_bytes':123,'sha256':'a'*64,'roundtrip_sha256':'a'*64}
        receipt={'final_status':'SUCCESS','verification':'VERIFIED','restore_test':'PASS','production_touched':False,
                 'snapshot_epoch':epoch,'archive':archive,'manifest':{'service_id':'svc','environment_id':'env',
                 'snapshot_method':'sqlite_online_backup','cross_database_atomic':False,
                 'files':[{'name':n,'size':10,'sha256':'b'*64} for n in ops.DB_NAMES]}}
        self.objects[prefix+'VERIFIED_RECEIPT.json']=json.dumps(receipt).encode()
        self.heads[archive['key']]={'ContentLength':123,'Metadata':{'sha256':'a'*64}}
        return receipt
    def _required(self,k): return 'test-bucket'
    def _make_s3_client(self): return self
    def get_paginator(self,k): return self
    def paginate(self,**kw): return [{'Contents':[{'Key':k} for k in self.objects]}]
    def read_bytes_verified(self,k): return self.objects[k]
    def head_object(self,**kw): return self.heads[kw['Key']]
    def delete_object(self,**kw): self.deleted.append(kw['Key'])

class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.store=Store();self.latest=self.store.add(0)
    def clean(self,**kw): return ops.cleanup_verified(self.store,self.latest,service_id='svc',environment_id='env',now=NOW,**kw)
    def test_maintenance_explicit_dry_run_never_deletes(self):
        for day in range(31, 45): self.store.add(day * ops.DAY)
        environment = {'OEMIXIBOT_BACKUP_RETENTION_DAYS': '30', 'OEMIXIBOT_BACKUP_RETENTION_DRY_RUN': '1', 'RAILWAY_SERVICE_ID': 'svc', 'RAILWAY_ENVIRONMENT_ID': 'env'}
        with patch.dict('os.environ', environment), patch.object(ops.time, 'time', return_value=NOW):
            result = ops.maintenance(self.store, self.latest)
        self.assertTrue(result['dry_run'])
        self.assertGreater(result['eligible'], 0)
        self.assertEqual(result['deleted'], 0)
        self.assertFalse(self.store.deleted)

    def test_no_old_copies_means_no_deletion(self): self.assertEqual(self.clean()['deleted'],0)
    def test_minimum_seven_even_if_old_and_keep_latest(self):
        for day in range(31,40): self.store.add(day*ops.DAY)
        result=self.clean()
        self.assertEqual(result['deleted'],3)
        self.assertNotIn(self.latest['archive']['key'],self.store.deleted)
        self.assertEqual(len(self.store.deleted),6)
    def test_exact_thirty_days_is_retained(self):
        for day in range(1,7): self.store.add(day*ops.DAY)
        boundary=self.store.add(30*ops.DAY)
        self.assertEqual(self.clean()['deleted'],0)
        self.assertNotIn(boundary['archive']['key'],self.store.deleted)
    def test_all_recent_archives_are_kept_above_floor(self):
        for day in range(1,25): self.store.add(day*ops.DAY)
        self.assertEqual(self.clean()['deleted'],0)
    def test_stale_latest_blocks_every_delete(self):
        self.latest['snapshot_epoch']=NOW-ops.INTERVAL
        with self.assertRaises(RuntimeError): self.clean()
        self.assertFalse(self.store.deleted)
    def test_failed_latest_blocks_every_delete(self):
        self.latest['restore_test']='FAILED'
        with self.assertRaises(RuntimeError): self.clean()
        self.assertFalse(self.store.deleted)
    def test_other_prefixes_and_orphans_never_deleted(self):
        self.store.objects['core/old/VERIFIED_RECEIPT.json']=b'not json'
        self.store.objects[backup.PREFIX+'orphan.tar.gz']=b'unverified'
        self.clean();self.assertFalse(self.store.deleted)
    def test_wrong_environment_and_hash_mismatch_preserved(self):
        bad=self.store.add(40*ops.DAY);bad['manifest']['environment_id']='other'
        key=bad['archive']['key'].replace('snapshot.tar.gz','VERIFIED_RECEIPT.json')
        self.store.objects[key]=json.dumps(bad).encode()
        other=self.store.add(50*ops.DAY);self.store.heads[other['archive']['key']]['Metadata']['sha256']='c'*64
        self.assertEqual(self.clean()['skipped'],2);self.assertFalse(self.store.deleted)
    def test_missing_latest_inventory_blocks_delete(self):
        self.store.objects.clear()
        with self.assertRaisesRegex(RuntimeError,'inventory'): self.clean()
        self.assertFalse(self.store.deleted)
    def test_dry_run_inventory_preserves_aged_verified_archives(self):
        for day in range(31, 51):
            self.store.add(day * ops.DAY)
        result = self.clean(dry_run=True)
        self.assertEqual(result['status'], 'PASS')
        self.assertGreater(result['eligible'], 0)
        self.assertEqual(result['deleted'], 0)
        self.assertEqual(self.store.deleted, [])

    def test_dry_run_never_deletes(self):
        for day in range(31,41): self.store.add(day*ops.DAY)
        self.assertGreater(self.clean(dry_run=True)['eligible'],0)
        self.assertFalse(self.store.deleted)
    def test_maximum_twenty_bundles_per_pass(self):
        for day in range(31,100): self.store.add(day*ops.DAY)
        self.assertEqual(self.clean()['deleted'],20)
    def test_cleanup_failure_does_not_change_latest(self):
        before=copy.deepcopy(self.latest)
        for day in range(31,41): self.store.add(day*ops.DAY)
        with patch.object(self.store,'delete_object',side_effect=OSError('network')):
            with self.assertRaises(OSError): self.clean()
        self.assertEqual(before,self.latest)

class AlertTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.path=Path(self.tmp.name)/'state.json'
        self.clock=Mock(return_value=NOW);self.sender=Mock(return_value=123)
    def tearDown(self): self.tmp.cleanup()
    def alerts(self): return ops.TelegramAlerts(self.path,sender=self.sender,clock=self.clock)
    def test_failure_rate_limit_persists_across_restart(self):
        self.assertEqual(self.alerts().failure('backup','TimeoutError'),'SENT')
        self.assertEqual(self.alerts().failure('backup','TimeoutError'),'RATE_LIMITED')
        self.assertEqual(self.sender.call_count,1)
        self.clock.return_value=NOW+6*3600
        self.assertEqual(self.alerts().failure('backup','TimeoutError'),'SENT')
    def test_recovery_sent_once(self):
        self.alerts().failure('backup','RuntimeError')
        self.assertEqual(self.alerts().recovered(),'SENT')
        self.assertEqual(self.alerts().recovered(),'NOT_NEEDED')
        self.assertEqual(self.sender.call_count,2)
    def test_failed_send_is_not_marked_delivered(self):
        self.sender.side_effect=OSError('network')
        with self.assertRaises(OSError): self.alerts().failure('backup','RuntimeError')
        self.assertFalse(self.path.exists())
    def test_failed_recovery_remains_pending(self):
        self.alerts().failure('backup','RuntimeError');self.sender.side_effect=OSError('network')
        with self.assertRaises(OSError): self.alerts().recovered()
        self.assertIn('pending',json.loads(self.path.read_text()))
    def test_non_admin_never_calls_telegram(self):
        with patch.dict('os.environ',{'OEMIXIBOT_BACKUP_ALERT_CHAT_ID':'123'}),patch('supplier_admin_auth.is_telegram_admin',return_value=False),patch.object(ops.TelegramAlerts,'_post') as post:
            with self.assertRaisesRegex(RuntimeError,'admin'): ops.TelegramAlerts.send('test')
            post.assert_not_called()
    def test_group_is_rejected_before_send_message(self):
        with patch.dict('os.environ',{'OEMIXIBOT_BACKUP_ALERT_CHAT_ID':'123'}),patch('supplier_admin_auth.is_telegram_admin',return_value=True),patch.object(ops.TelegramAlerts,'_post',return_value={'id':123,'type':'group'}) as post:
            with self.assertRaisesRegex(RuntimeError,'private'): ops.TelegramAlerts.send('test')
            self.assertEqual(post.call_count,1)
    def test_private_admin_requires_confirmed_message_result(self):
        with patch.dict('os.environ',{'OEMIXIBOT_BACKUP_ALERT_CHAT_ID':'123'}),patch('supplier_admin_auth.is_telegram_admin',return_value=True),patch.object(ops.TelegramAlerts,'_post',side_effect=[{'id':123,'type':'private'},{'chat':{'id':123},'message_id':456}]):
            self.assertEqual(ops.TelegramAlerts.send('test'),456)
    def test_transport_error_cannot_expose_token(self):
        with patch.dict('os.environ',{'OEMIXIBOT_TOKEN':'secret-token'}),patch.object(ops.request,'urlopen',side_effect=OSError('https://api.telegram.org/botsecret-token/')):
            with self.assertRaises(RuntimeError) as ctx: ops.TelegramAlerts._post('getChat',{})
            self.assertNotIn('secret-token',str(ctx.exception))

class WorkerTests(unittest.TestCase):
    def tick(self,maintenance_error=False,backup_error=False):
        state=SimpleNamespace(backup=None)
        stop=Mock();stop.is_set.side_effect=[False,True]
        thread=Mock();thread.start.side_effect=lambda:thread.target()
        def factory(*args,**kw):thread.target=kw['target'];return thread
        last={'final_status':'SUCCESS','verification':'VERIFIED','restore_test':'PASS','snapshot_epoch':time.time()}
        store=SimpleNamespace(read_bytes_verified=Mock(return_value=json.dumps(last).encode()))
        if backup_error:store.read_bytes_verified.side_effect=FileNotFoundError
        alerts=Mock();alerts.failure.return_value="SENT";alerts.recovered.return_value="NOT_NEEDED"
        with patch.dict('os.environ',{'OEMIXIBOT_AUTO_BACKUP':'1'}),patch.dict('sys.modules',{'backup_object_store':store}),patch.object(backup.threading,'Event',return_value=stop),patch.object(backup.threading,'Thread',side_effect=factory),patch.object(ops,'get_alerts',return_value=alerts),patch.object(ops,'maintenance',side_effect=RuntimeError('retention') if maintenance_error else None,return_value={'status':'PASS'}),patch.object(backup,'run_once',side_effect=OSError('backup') if backup_error else None,return_value=last):
            backup.start_worker(state)
        return state,stop,alerts
    def test_retention_failure_keeps_backup_success_and_retries_in_fifteen_minutes(self):
        state,stop,alerts=self.tick(maintenance_error=True)
        self.assertEqual(state.backup['status'],'SUCCESS')
        self.assertEqual(state.backup['retention']['status'],'FAILED')
        stop.wait.assert_called_once_with(backup.RETRY)
        alerts.failure.assert_called_once_with('retention','RuntimeError')
    def test_backup_failure_notifies_without_starting_retention(self):
        state,stop,alerts=self.tick(backup_error=True)
        self.assertEqual(state.backup['status'],'FAILED')
        stop.wait.assert_called_once_with(backup.RETRY)
        alerts.failure.assert_called_once_with('backup','OSError')

if __name__=='__main__':unittest.main()
