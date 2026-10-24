"""Daily off-volume OEMixi SQLite backups with a mandatory restore drill.

No Telegram/DCP access. Each DB is consistent; the bundle is not cross-DB atomic.
Only publish latest.json after the downloaded archive passes all checks.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tarfile
import tempfile
import threading
import time
from datetime import datetime, timezone

PREFIX = 'oemixibot/automatic/v1/'
LATEST = PREFIX + 'latest.json'
INTERVAL = 24 * 3600
RETRY = 15 * 60
PATH_KEYS = ('OEMIXIBOT_DB_PATH', 'OEMIXIBOT_IDENTITY_DB', 'EXTREMIZER_OEM_REFERENCE_DB_FILE')


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def inspect_db(path):
    with sqlite3.connect(Path(path).as_uri() + '?mode=ro', uri=True, timeout=30) as db:
        if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise RuntimeError('backup_integrity_failed')
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            db.execute('SELECT 1 FROM "' + table.replace('"', '""') + '" LIMIT 1').fetchone()
    return len(tables)


def restore_archive(archive, directory, manifest):
    directory = Path(directory)
    directory.mkdir()
    expected = {r['name'] for r in manifest['files']} | {'manifest.json'}
    with tarfile.open(archive, 'r:gz') as tf:
        members = tf.getmembers()
        if len(members) != len(expected) or {m.name for m in members} != expected:
            raise RuntimeError('backup_archive_members_invalid')
        if not all(m.isfile() and '/' not in m.name and '\\' not in m.name for m in members):
            raise RuntimeError('backup_archive_member_unsafe')
        sizes = {r['name']: r['size'] for r in manifest['files']}
        for member in members:
            if member.name in sizes and member.size != sizes[member.name]:
                raise RuntimeError('backup_archive_size_invalid')
        tf.extractall(directory, filter='data')
    if json.loads((directory / 'manifest.json').read_text()) != manifest:
        raise RuntimeError('backup_manifest_mismatch')
    for row in manifest['files']:
        path = directory / row['name']
        if path.stat().st_size != row['size'] or digest(path) != row['sha256']:
            raise RuntimeError('backup_restored_hash_mismatch')
        if inspect_db(path) != row['tables']:
            raise RuntimeError('backup_restored_tables_mismatch')


def run_once(paths=None, store=None, now=None, tmp_root='/tmp'):
    if store is None:
        import backup_object_store as store
    paths = [Path(os.environ[k]).resolve() for k in PATH_KEYS] if paths is None else [Path(p).resolve() for p in paths]
    if len(paths) != 3 or len(set(paths)) != 3 or len({p.name for p in paths}) != 3 or not all(p.is_file() for p in paths):
        raise RuntimeError('backup_three_distinct_databases_required')
    now = time.time() if now is None else now
    stamp = datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    prefix = PREFIX + stamp + '/'
    # snapshots + extracted restore + two compressed archives, conservatively
    if shutil.disk_usage(tmp_root).free < sum(p.stat().st_size for p in paths) * 4 + 100 * 1024**2:
        raise RuntimeError('backup_temporary_space_insufficient')
    with tempfile.TemporaryDirectory(prefix='oemixi-auto-', dir=tmp_root) as work:
        work = Path(work)
        snap = work / 'snapshot'
        snap.mkdir()
        rows = []
        for source in paths:
            target = snap / source.name
            deadline = time.monotonic() + 180
            def progress(_status, _remaining, _total):
                if time.monotonic() > deadline:
                    raise TimeoutError('backup_sqlite_timeout')
            with sqlite3.connect(source.as_uri() + '?mode=ro', uri=True, timeout=30) as src:
                with sqlite3.connect(target) as dst:
                    src.backup(dst, pages=256, progress=progress, sleep=0.05)
            rows.append({'name': source.name, 'size': target.stat().st_size, 'sha256': digest(target), 'tables': inspect_db(target)})
        manifest = {'files': rows, 'created_at_utc': datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    'snapshot_method': 'sqlite_online_backup', 'cross_database_atomic': False,
                    'service_id': os.getenv('RAILWAY_SERVICE_ID'), 'environment_id': os.getenv('RAILWAY_ENVIRONMENT_ID'),
                    'deployment_id': os.getenv('RAILWAY_DEPLOYMENT_ID'), 'commit_sha': os.getenv('RAILWAY_GIT_COMMIT_SHA')}
        (snap / 'manifest.json').write_text(json.dumps(manifest, sort_keys=True))
        archive = work / 'snapshot.tar.gz'
        with tarfile.open(archive, 'w:gz') as tf:
            for name in [r['name'] for r in rows] + ['manifest.json']:
                tf.add(snap / name, arcname=name, recursive=False)
        uploaded = store.upload_file_verified(archive, prefix + 'snapshot.tar.gz')
        # Free the original snapshot before exercising the downloaded copy.
        shutil.rmtree(snap)
        downloaded = work / 'downloaded.tar.gz'
        client = store._make_s3_client()
        client.download_file(store._required(store.ENV_BUCKET), prefix + 'snapshot.tar.gz', str(downloaded))
        if downloaded.stat().st_size != archive.stat().st_size or digest(downloaded) != digest(archive):
            raise RuntimeError('backup_downloaded_hash_mismatch')
        restore_archive(downloaded, work / 'restore', manifest)
        receipt = {'final_status': 'SUCCESS', 'verification': 'VERIFIED', 'restore_test': 'PASS',
                   'production_touched': False, 'completed_at_epoch': time.time(), 'snapshot_epoch': now,
                   'archive': uploaded, 'manifest': manifest}
        body = json.dumps(receipt, sort_keys=True).encode()
        store.put_bytes_verified(body, prefix + 'VERIFIED_RECEIPT.json', content_type='application/json')
        store.put_bytes_verified(body, LATEST, content_type='application/json')
        return receipt


def due(receipt, now):
    if not receipt:
        return True
    if receipt.get('final_status') != 'SUCCESS' or receipt.get('verification') != 'VERIFIED' or receipt.get('restore_test') != 'PASS':
        raise RuntimeError('backup_latest_receipt_invalid')
    stamp = float(receipt['snapshot_epoch'])
    if stamp > now + 300:
        raise RuntimeError('backup_latest_receipt_future')
    return now - stamp >= INTERVAL


def start_worker(state):
    """Enabled explicitly; failures are observable and never take down polling."""
    if os.getenv('OEMIXIBOT_AUTO_BACKUP') != '1':
        return None
    stop = threading.Event()
    def worker():
        import backup_object_store as store
        while not stop.is_set():
            wait = RETRY
            try:
                try:
                    last = json.loads(store.read_bytes_verified(LATEST))
                except FileNotFoundError:
                    last = None
                if due(last, time.time()):
                    state.backup = {'status': 'RUNNING'}
                    last = run_once(store=store)
                state.backup = {'status': 'SUCCESS', 'verification': 'VERIFIED', 'restore_test': 'PASS',
                                'snapshot_epoch': last['snapshot_epoch']}
                wait = max(1, last['snapshot_epoch'] + INTERVAL - time.time())
                print('OEMIXI_BACKUP ' + json.dumps(state.backup), flush=True)
            except Exception as exc:
                state.backup = {'status': 'FAILED', 'error_type': type(exc).__name__, 'retry_seconds': RETRY}
                print('OEMIXI_BACKUP ' + json.dumps(state.backup), flush=True)
            stop.wait(wait)
    thread = threading.Thread(target=worker, name='oemixi-auto-backup', daemon=True)
    thread.start()
    return stop
