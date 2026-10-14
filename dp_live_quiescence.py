"""Read-only transactional preflight; DB rows do not prove browser inactivity."""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3

import dp_live_admission as admission
import dp_live_health as health


def _preflight(db_file, *, purpose='switch', operator_evidence=None):
    if purpose not in {'switch','rollback'}:raise ValueError('invalid_preflight_purpose')
    checked=datetime.now(timezone.utc)
    with closing(sqlite3.connect(Path(db_file).resolve().as_uri()+'?mode=ro',uri=True)) as conn:
        conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
        state=admission.read_state(conn)
        rows=conn.execute("""SELECT request_id,status,claim_owner,claim_generation,claim_token,
            lease_expires_at,orphaned_at FROM dp_live_requests WHERE status!='finished' ORDER BY request_id""").fetchall()
        live=[]
        for rid,status,owner,generation,token,lease,orphaned in rows:
            deadline=health._parse_datetime(lease)
            live.append({'request_id':rid,'status':status,'producer_id':owner,'claim_generation':generation,
                         'claim_token_sha256':hashlib.sha256(token.encode()).hexdigest() if token else None,
                         'lease_expires_at':lease,'lease_expired':bool(deadline and deadline<=checked),
                         'orphaned_at':orphaned})
        counts={k:sum(r['status']==k for r in live) for k in ('pending','claimed','orphaned')}
        counts['other_unfinished']=sum(r['status'] not in counts for r in live)
        h=conn.execute('SELECT status,checked_at FROM dp_live_health WHERE singleton_id=1').fetchone()
        circuit=conn.execute('SELECT blocked_status,blocked_at FROM dp_live_coordination WHERE singleton_id=1').fetchone()
        hs={'status':h[0] if h else 'UNKNOWN','checked_at':h[1] if h else None}
        cs={'blocked_status':circuit[0] if circuit else 'UNKNOWN','blocked_at':circuit[1] if circuit else None}
        ready_at=health._parse_datetime(hs['checked_at']);blocked_at=health._parse_datetime(cs['blocked_at'])
        cs['effective_blocked']=bool(cs['blocked_status'] and not (hs['status']=='READY' and ready_at and blocked_at and ready_at>blocked_at))
        last=conn.execute("SELECT request_id,claim_owner,claim_generation,claimed_at,status FROM dp_live_requests WHERE claimed_at IS NOT NULL ORDER BY claimed_at DESC LIMIT 1").fetchone()
        historical_claim=dict(zip(('request_id','producer_id','claim_generation','claimed_at','status'),last)) if last else None
        snapshot={'version':1,'db_path':str(Path(db_file).resolve()),'admission':state,'counts':counts,
                  'requests':live,'last_claim_historical_only':historical_claim,'health':hs,'circuit':cs,'purpose':purpose}
        digest=admission._sha(snapshot)
    blockers=[]
    if state['mode']!='CLOSED':blockers.append('ADMISSION_NOT_CLOSED')
    if live:blockers.append('UNRECONCILED_REQUESTS')
    if hs['status']!='READY' or not ready_at or not 0<=(checked-ready_at).total_seconds()<=5400:
        blockers.append('HEALTH_NOT_CONFIRMED_READY')
    if cs['effective_blocked']:blockers.append('DCP_CIRCUIT_BLOCKED')
    evidence_status='MANUAL EVIDENCE REQUIRED'
    # This is an operator attestation reference, not independent automated proof.
    # RC must inspect its referenced artifact; booleans alone are insufficient.
    if isinstance(operator_evidence,dict):
        e=operator_evidence
        observed=health._parse_datetime(e.get('observed_at'))
        required={'producer_stopped','singleton_verified','no_other_producer','browser_inflight_zero',
                  'result_delivery_inflight_zero','uncertainty_journal_reconciled'}
        valid=(e.get('snapshot_sha256')==digest and e.get('source')=='independent_operator_attestation'
               and isinstance(e.get('actor'),str) and bool(re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',e['actor']))
               and bool(re.fullmatch(r'[a-f0-9]{64}',str(e.get('evidence_sha256') or '')))
               and observed is not None and 0<=(checked-observed).total_seconds()<=300
               and all(e.get(k) is True for k in required))
        if valid:evidence_status='OPERATOR ATTESTATION PROVIDED / RC REVIEW REQUIRED'
    if evidence_status=='MANUAL EVIDENCE REQUIRED':blockers.append('MANUAL_EVIDENCE_REQUIRED')
    return {**snapshot,'snapshot_sha256':digest,'checked_at':checked.isoformat(),
            'producer_ids':sorted({r['producer_id'] for r in live if r['producer_id']}),
            'runtime_identity':'NOT PROVEN BY SQLITE','evidence_status':evidence_status,
            'gate':'BLOCKED' if blockers else 'READY_FOR_RELEASE_CONTROL_REVIEW',
            'blockers':blockers,'production_authorization':False}


def preflight(db_file, *, purpose='switch', operator_evidence=None):
    try:
        return _preflight(db_file,purpose=purpose,operator_evidence=operator_evidence)
    except (OSError,sqlite3.Error,admission.AdmissionError):
        return {'gate':'BLOCKED','blockers':['DB_OR_SCHEMA_NOT_READY','MANUAL_EVIDENCE_REQUIRED'],
                'evidence_status':'MANUAL EVIDENCE REQUIRED','runtime_identity':'NOT PROVEN BY SQLITE',
                'checked_at':datetime.now(timezone.utc).isoformat(),'production_authorization':False}


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('db_file')
    p.add_argument('--purpose',choices=('switch','rollback'),default='switch');p.add_argument('--operator-evidence-file')
    args=p.parse_args()
    try:
        evidence=json.loads(Path(args.operator_evidence_file).read_text()) if args.operator_evidence_file else None
        result=preflight(args.db_file,purpose=args.purpose,operator_evidence=evidence)
        print(json.dumps(result,sort_keys=True));raise SystemExit(0 if result['gate']=='READY_FOR_RELEASE_CONTROL_REVIEW' else 2)
    except (OSError,sqlite3.Error,admission.AdmissionError,ValueError) as exc:
        print(json.dumps({'gate':'BLOCKED','reason':type(exc).__name__,'production_authorization':False}));raise SystemExit(2)
