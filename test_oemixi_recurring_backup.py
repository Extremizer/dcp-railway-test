import io
import json
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import oemixi_recurring_backup as b

class Store:
    ENV_BUCKET='bucket'
    def __init__(self):
        self.objects={}; self.fail_download=False; self.fail_receipt=False
    def upload_file_verified(self,p,k):
        self.objects[k]=Path(p).read_bytes()
        return {'key':k,'sha256':b.digest(p),'size_bytes':Path(p).stat().st_size}
    def _make_s3_client(self): return self
    def _required(self,k): return 'isolated-test'
    def download_file(self,bucket,k,p):
        Path(p).write_bytes(b'corrupt' if self.fail_download else self.objects[k])
    def put_bytes_verified(self,v,k,**kw):
        if self.fail_receipt and k.endswith('VERIFIED_RECEIPT.json'): raise OSError('upload failed')
        self.objects[k]=v

class BackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.paths=[]
        for name in ('oemixibot.db','oemixibot_identity.db','oem_reference.db'):
            p=self.root/name
            with sqlite3.connect(p) as db:
                db.execute('CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT)')
                db.execute("INSERT INTO records(value) VALUES ('preserve')")
            self.paths.append(p)
        self.store=Store(); self.hashes=[b.digest(p) for p in self.paths]
    def tearDown(self): self.tmp.cleanup()
    def run_backup(self): return b.run_once(self.paths,self.store,now=1000000000,tmp_root=self.root)
    def test_round_trip_three_databases_and_live_files_unchanged(self):
        r=self.run_backup()
        self.assertEqual((r['final_status'],r['verification'],r['restore_test']),('SUCCESS','VERIFIED','PASS'))
        self.assertEqual(len(r['manifest']['files']),3)
        self.assertFalse(r['manifest']['cross_database_atomic'])
        self.assertEqual([b.digest(p) for p in self.paths],self.hashes)
        self.assertEqual(json.loads(self.store.objects[b.LATEST]),r)
        self.assertFalse(list(self.root.glob('oemixi-auto-*')))
    def test_download_corruption_preserves_previous_success_pointer(self):
        self.store.objects[b.LATEST]=b'previous';self.store.fail_download=True
        with self.assertRaisesRegex(RuntimeError,'downloaded_hash'): self.run_backup()
        self.assertEqual(self.store.objects[b.LATEST],b'previous')
    def test_receipt_upload_failure_never_promotes_latest(self):
        self.store.fail_receipt=True
        with self.assertRaises(OSError): self.run_backup()
        self.assertNotIn(b.LATEST,self.store.objects)
    def test_invalid_database_never_uploads(self):
        self.paths[1].write_bytes(b'not a database')
        with self.assertRaises(sqlite3.DatabaseError): self.run_backup()
        self.assertFalse(self.store.objects)
    def test_missing_source_fails_without_creating_database(self):
        self.paths[0].unlink()
        with self.assertRaisesRegex(RuntimeError,'three_distinct'): self.run_backup()
        self.assertFalse(self.paths[0].exists())
    def test_duplicate_sources_rejected(self):
        with self.assertRaises(RuntimeError): b.run_once([self.paths[0]]*3,self.store,tmp_root=self.root)
    def test_disk_space_failure_uploads_nothing(self):
        with patch.object(shutil,'disk_usage',return_value=shutil._ntuple_diskusage(100,99,1)):
            with self.assertRaisesRegex(RuntimeError,'space'): self.run_backup()
        self.assertFalse(self.store.objects)
    def test_restore_integrity_failure_never_promotes_latest(self):
        with patch.object(b,'restore_archive',side_effect=RuntimeError('restore failed')):
            with self.assertRaisesRegex(RuntimeError,'restore failed'): self.run_backup()
        self.assertNotIn(b.LATEST,self.store.objects)
    def test_archive_path_traversal_is_rejected(self):
        archive=self.root/'unsafe.tgz'; info=tarfile.TarInfo('../escape');info.size=1
        with tarfile.open(archive,'w:gz') as tf: tf.addfile(info,io.BytesIO(b'x'))
        with self.assertRaises(RuntimeError): b.restore_archive(archive,self.root/'restore',{'files':[]})
        self.assertFalse((self.root.parent/'escape').exists())
    def test_twenty_four_hour_boundary_and_future_guard(self):
        r={'final_status':'SUCCESS','verification':'VERIFIED','restore_test':'PASS','snapshot_epoch':1000}
        self.assertFalse(b.due(r,1000+86399)); self.assertTrue(b.due(r,1000+86400))
        self.assertTrue(b.due(None,1000))
        with self.assertRaisesRegex(RuntimeError,'future'): b.due(r,0)
    def test_failed_receipt_cannot_suppress_backup(self):
        with self.assertRaisesRegex(RuntimeError,'invalid'): b.due({'final_status':'FAILED'},1000)
    def test_worker_disabled_by_default(self):
        with patch.dict('os.environ',{},clear=True): self.assertIsNone(b.start_worker(object()))

if __name__=='__main__':unittest.main()
