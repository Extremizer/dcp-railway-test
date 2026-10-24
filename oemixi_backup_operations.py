"""Private admin backup alerts and conservative, prefix-scoped retention."""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from urllib import request, parse

from oemixi_recurring_backup import PREFIX, INTERVAL

DAY = 86400
KEEP_DAYS = 30
MIN_COPIES = 7
MAX_DELETE = 20
HEX = re.compile(r'[0-9a-f]{64}')
STAMP = re.compile(re.escape(PREFIX) + r'(\d{8}T\d{12}Z)/VERIFIED_RECEIPT\.json')
DB_NAMES = {'oemixibot.db', 'oemixibot_identity.db', 'oem_reference.db'}


def valid_receipt(receipt, store, service_id, environment_id):
    try:
        archive, manifest = receipt['archive'], receipt['manifest']
        epoch = float(receipt['snapshot_epoch'])
        if not math.isfinite(epoch):
            return False
        stamp = datetime.fromtimestamp(epoch, timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        expected = PREFIX + stamp + '/snapshot.tar.gz'
        files = manifest['files']
        return (
            receipt['final_status'] == 'SUCCESS' and receipt['verification'] == 'VERIFIED'
            and receipt['restore_test'] == 'PASS' and receipt['production_touched'] is False
            and service_id and environment_id
            and manifest['service_id'] == service_id and manifest['environment_id'] == environment_id
            and manifest['snapshot_method'] == 'sqlite_online_backup'
            and manifest['cross_database_atomic'] is False
            and len(files) == 3 and {f['name'] for f in files} == DB_NAMES
            and all(type(f['size']) is int and f['size'] > 0 and HEX.fullmatch(f['sha256']) for f in files)
            and archive['key'] == expected and archive['bucket'] == store._required(store.ENV_BUCKET)
            and type(archive['size_bytes']) is int and archive['size_bytes'] > 0
            and HEX.fullmatch(archive['sha256'])
            and archive['roundtrip_sha256'] == archive['sha256']
        )
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        return False


def cleanup_verified(store, latest, *, service_id, environment_id, now=None, dry_run=False):
    """Never touch Core/manual copies or unverified/orphan objects. Preserve >=7."""
    now = time.time() if now is None else now
    if not valid_receipt(latest, store, service_id, environment_id):
        raise RuntimeError('retention_latest_invalid')
    if not -300 <= now - latest['snapshot_epoch'] < INTERVAL:
        raise RuntimeError('retention_requires_fresh_verified_backup')
    client = store._make_s3_client()
    bucket = store._required(store.ENV_BUCKET)
    keys = []
    for page in client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=PREFIX):
        keys.extend(row['Key'] for row in page.get('Contents', []) if STAMP.fullmatch(row['Key']))
    verified = []
    skipped = 0
    for key in sorted(set(keys)):
        receipt = json.loads(store.read_bytes_verified(key))
        if not valid_receipt(receipt, store, service_id, environment_id):
            skipped += 1
            continue
        archive = receipt['archive']
        if key != archive['key'].removesuffix('snapshot.tar.gz') + 'VERIFIED_RECEIPT.json':
            skipped += 1
            continue
        try:
            head = client.head_object(Bucket=bucket, Key=archive['key'])
        except Exception as exc:
            code = str(getattr(exc, 'response', {}).get('Error', {}).get('Code', ''))
            if code in {'404', 'NoSuchKey', 'NotFound'}:
                skipped += 1
                continue
            raise
        if head.get('ContentLength') != archive['size_bytes'] or (head.get('Metadata') or {}).get('sha256') != archive['sha256']:
            skipped += 1
            continue
        verified.append((receipt['snapshot_epoch'], key, archive['key']))
    verified.sort(reverse=True)
    latest_archive = latest['archive']['key']
    if latest_archive not in {x[2] for x in verified}:
        raise RuntimeError('retention_latest_not_in_verified_inventory')
    protected = {row[2] for row in verified[:MIN_COPIES]} | {latest_archive}
    candidates = [row for row in reversed(verified) if row[0] < now - KEEP_DAYS * DAY and row[2] not in protected][:MAX_DELETE]
    deleted = 0
    if not dry_run:
        for _epoch, receipt_key, archive_key in candidates:
            # Deliberately use single-object deletes: do not trust a broad lifecycle prefix.
            client.delete_object(Bucket=bucket, Key=archive_key)
            client.delete_object(Bucket=bucket, Key=receipt_key)
            deleted += 1
    return {'status': 'PASS', 'keep_days': KEEP_DAYS, 'minimum_verified_copies': MIN_COPIES,
            'verified_copies': len(verified), 'eligible': len(candidates), 'deleted': deleted,
            'skipped': skipped, 'dry_run': dry_run}


class TelegramAlerts:
    """Rate-limit only confirmed sends, persist across worker restarts."""
    REPEAT_SECONDS = 6 * 3600

    def __init__(self, state_path, *, sender=None, clock=time.time):
        self.path = Path(state_path)
        self.sender = sender or self.send
        self.clock = clock
        self.data = json.loads(self.path.read_text()) if self.path.exists() else {}

    def _save(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix('.tmp')
        with tmp.open('w') as f:
            json.dump(self.data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    @staticmethod
    def _post(method, values):
        token = os.environ.get('OEMIXIBOT_TOKEN', '')
        if not token:
            raise RuntimeError('backup_alert_token_missing')
        try:
            req = request.Request('https://api.telegram.org/bot' + token + '/' + method,
                                  data=parse.urlencode(values).encode())
            with request.urlopen(req, timeout=10) as response:
                result = json.load(response)
            if result.get('ok') is not True:
                raise ValueError('telegram_not_ok')
            return result['result']
        except Exception:
            # Never propagate request URL/token or Telegram response contents.
            raise RuntimeError('backup_alert_transport_failed') from None

    @classmethod
    def send(cls, text):
        from supplier_admin_auth import is_telegram_admin
        raw = os.getenv('OEMIXIBOT_BACKUP_ALERT_CHAT_ID', '')
        if not raw.isdigit() or not is_telegram_admin(int(raw)):
            raise RuntimeError('backup_alert_admin_required')
        chat_id = int(raw)
        chat = cls._post('getChat', {'chat_id': chat_id})
        if chat.get('id') != chat_id or chat.get('type') != 'private':
            raise RuntimeError('backup_alert_private_chat_required')
        result = cls._post('sendMessage', {'chat_id': chat_id, 'text': text})
        if (result.get('chat') or {}).get('id') != chat_id or not result.get('message_id'):
            raise RuntimeError('backup_alert_delivery_unconfirmed')
        return result['message_id']

    def failure(self, phase, error_type):
        now = self.clock()
        previous = self.data.get('pending')
        if previous and previous['phase'] == phase and now - previous['sent_at'] < self.REPEAT_SECONDS:
            return 'RATE_LIMITED'
        message_id = self.sender('⚠️ OEMixiBOT: ' + ('ошибка резервного копирования' if phase == 'backup' else 'ошибка очистки старых backup')
                                 + '.\nКод: ' + error_type + '.\nПовторная попытка через 15 минут. Последняя успешная копия сохраняется.')
        self.data['pending'] = {'phase': phase, 'sent_at': now, 'message_id': message_id}
        self._save()
        return 'SENT'

    def recovered(self):
        if not self.data.get('pending'):
            return 'NOT_NEEDED'
        self.sender('✅ OEMixiBOT: резервное копирование и проверка хранения снова работают. Проверенная копия доступна.')
        self.data.pop('pending')
        self._save()
        return 'SENT'


def maintenance(store, last):
    if os.getenv('OEMIXIBOT_BACKUP_RETENTION_DAYS') != str(KEEP_DAYS):
        return {'status': 'DISABLED'}
    return cleanup_verified(store, last, service_id=os.environ['RAILWAY_SERVICE_ID'],
                            environment_id=os.environ['RAILWAY_ENVIRONMENT_ID'])


def get_alerts():
    if os.getenv('OEMIXIBOT_BACKUP_ALERTS') != '1':
        return None
    return TelegramAlerts(Path(os.environ['OEMIXIBOT_DB_PATH']).parent / 'backups' / 'oemixi-alert-state.json')
