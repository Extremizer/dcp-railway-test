"""Persisted live admission. Trusted administrative CLI only; no HTTP control route."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import sqlite3
import uuid


class AdmissionError(Exception):
    pass


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _sha(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def initialize(conn):
    """Inside broker initialization transaction. Never overwrite persisted state."""
    conn.execute("""CREATE TABLE IF NOT EXISTS dp_live_admission (
        singleton_id INTEGER PRIMARY KEY CHECK(singleton_id=1),
        instance_id TEXT NOT NULL, mode TEXT NOT NULL CHECK(mode IN ('OPEN','CLOSED')),
        revision INTEGER NOT NULL CHECK(revision>=0), changed_at TEXT NOT NULL,
        actor TEXT NOT NULL, approval_sha256 TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS dp_live_admission_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT, instance_id TEXT NOT NULL,
        previous_mode TEXT, mode TEXT NOT NULL, revision INTEGER NOT NULL,
        actor TEXT NOT NULL, created_at TEXT NOT NULL,
        plan_sha256 TEXT, approval_sha256 TEXT UNIQUE, evidence_sha256 TEXT)""")
    now=datetime.now(timezone.utc).isoformat()
    instance=uuid.uuid4().hex
    added=conn.execute("""INSERT OR IGNORE INTO dp_live_admission
        VALUES(1,?,'CLOSED',0,?,'schema_bootstrap',NULL)""",(instance,now)).rowcount
    if added:
        conn.execute("""INSERT INTO dp_live_admission_events
            (instance_id,previous_mode,mode,revision,actor,created_at)
            VALUES(?,NULL,'CLOSED',0,'schema_bootstrap',?)""",(instance,now))


def read_state(conn):
    row=conn.execute("SELECT instance_id,mode,revision,changed_at,actor,approval_sha256 FROM dp_live_admission WHERE singleton_id=1").fetchone()
    if row is None or row[1] not in {'OPEN','CLOSED'}:
        raise AdmissionError('admission_state_missing_or_invalid')
    return dict(zip(('instance_id','mode','revision','changed_at','actor','approval_sha256'),row))


def is_open(conn):
    # Unknown state is never permission to enqueue or claim.
    try:
        return read_state(conn)['mode']=='OPEN'
    except (AdmissionError, sqlite3.Error):
        return False


def _plan(conn, db_file, target):
    if target not in {'OPEN','CLOSED'}:
        raise AdmissionError('invalid_admission_mode')
    state=read_state(conn)
    value={'version':1,'db_path':str(Path(db_file).resolve()),'instance_id':state['instance_id'],
           'from_mode':state['mode'],'target_mode':target,'expected_revision':state['revision']}
    return {**value,'plan_sha256':_sha(value)}


def admission_plan(db_file, target):
    with closing(sqlite3.connect(Path(db_file).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        return _plan(conn,db_file,target)


def sign_approval(key, approval):
    if not isinstance(key,bytes) or len(key)<32:
        raise AdmissionError('dedicated_approval_key_required')
    return hmac.new(key,b'dp-live-admission-approval-v1\n'+_canonical(approval),hashlib.sha256).hexdigest()


def _verify_approval(key, approval, signature, target):
    fields={'version','authority','scope','actor','plan_sha256','issued_at','expires_at','evidence_sha256'}
    if not isinstance(approval,dict) or set(approval)!=fields:
        raise AdmissionError('invalid_approval_record')
    expected=sign_approval(key,approval)
    if not isinstance(signature,str) or not secrets.compare_digest(signature,expected):
        raise AdmissionError('unauthorized_admission_control')
    if (type(approval['version']) is not int or approval['version']!=1
        or approval['authority']!='release_control' or approval['scope']!='dp.admission.'+target.lower()
        or not isinstance(approval['actor'],str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',approval['actor'])
        or not re.fullmatch(r'[a-f0-9]{64}',str(approval['plan_sha256']))
        or not re.fullmatch(r'[a-f0-9]{64}',str(approval['evidence_sha256']))):
        raise AdmissionError('invalid_approval_scope_or_evidence')
    try:
        issued=datetime.fromisoformat(approval['issued_at']);expires=datetime.fromisoformat(approval['expires_at'])
        now=datetime.now(timezone.utc)
        if issued.tzinfo is None or expires.tzinfo is None or not issued<=now<expires or not 0<(expires-issued).total_seconds()<=600:
            raise ValueError()
    except (TypeError,ValueError):
        raise AdmissionError('approval_expired_or_invalid')


def set_admission(db_file, target, *, approval, signature, approval_key):
    """Authority supplies a dedicated RC key; producer/bot credentials cannot control.

    The signed approval binds the exact instance/state revision plan plus reviewed
    independent evidence. CLI access and approval key custody are operator gates.
    Never call schema init here, never touch requests/cache or automatically OPEN.
    """
    _verify_approval(approval_key,approval,signature,target)
    with closing(sqlite3.connect(str(db_file),timeout=10,isolation_level=None)) as conn:
        conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
        try:
            plan=_plan(conn,db_file,target)
            if plan['plan_sha256']!=approval['plan_sha256']:
                raise AdmissionError('admission_plan_changed')
            if plan['from_mode']==target:
                raise AdmissionError('admission_already_in_target_mode')
            if target=='OPEN':
                # Reopening cannot use a stale empty-queue decision.
                if conn.execute("SELECT 1 FROM dp_live_requests WHERE status!='finished' LIMIT 1").fetchone():
                    raise AdmissionError('unreconciled_requests_block_open')
            now=datetime.now(timezone.utc).isoformat();digest=_sha(approval);revision=plan['expected_revision']+1
            conn.execute("""INSERT INTO dp_live_admission_events
                (instance_id,previous_mode,mode,revision,actor,created_at,plan_sha256,approval_sha256,evidence_sha256)
                VALUES(?,?,?,?,?,?,?,?,?)""",(plan['instance_id'],plan['from_mode'],target,revision,
                    approval['actor'],now,plan['plan_sha256'],digest,approval['evidence_sha256']))
            changed=conn.execute("""UPDATE dp_live_admission SET mode=?,revision=?,changed_at=?,actor=?,approval_sha256=?
                WHERE singleton_id=1 AND instance_id=? AND revision=?""",(target,revision,now,approval['actor'],digest,
                    plan['instance_id'],plan['expected_revision'])).rowcount
            if changed!=1:raise AdmissionError('admission_revision_conflict')
            conn.commit()
            return {'mode':target,'revision':revision,'approval_sha256':digest,'requeued':False}
        except Exception:
            conn.rollback();raise


if __name__=='__main__':
    import argparse,os
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('db_file');parser.add_argument('target',choices=('OPEN','CLOSED'))
    parser.add_argument('--apply',action='store_true');parser.add_argument('--approval-file')
    args=parser.parse_args()
    try:
        if args.apply:
            if not args.approval_file:raise AdmissionError('signed_approval_file_required')
            record=json.loads(Path(args.approval_file).read_text())
            key=bytes.fromhex(os.environ.get('EXTREMIZER_DP_ADMISSION_APPROVAL_KEY',''))
            result=set_admission(args.db_file,args.target,approval=record['approval'],signature=record['signature'],approval_key=key)
        else:result=admission_plan(args.db_file,args.target)
        print(json.dumps(result,sort_keys=True))
    except (AdmissionError,KeyError,ValueError,OSError,sqlite3.Error) as exc:
        print(json.dumps({'status':'BLOCKED','error':str(exc) if isinstance(exc,AdmissionError) else type(exc).__name__}));raise SystemExit(1)
