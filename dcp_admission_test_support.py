"""Explicit isolated Release Control approvals. Never imported by runtime modules."""
from datetime import datetime, timezone, timedelta
import dp_live_admission as admission

MOCK_APPROVAL_KEY=b'isolated-release-control-key-32-bytes-no-production-secret'


def approval_for(db_file,target,**changes):
    plan=admission.admission_plan(db_file,target)
    now=datetime.now(timezone.utc)
    record={'version':1,'authority':'release_control','scope':'dp.admission.'+target.lower(),
            'actor':'mock-rc-operator','plan_sha256':plan['plan_sha256'],
            'issued_at':now.isoformat(),'expires_at':(now+timedelta(minutes=5)).isoformat(),
            'evidence_sha256':'a'*64,**changes}
    return record,admission.sign_approval(MOCK_APPROVAL_KEY,record)


def approve_mode(db_file,target):
    record,sig=approval_for(db_file,target)
    return admission.set_admission(db_file,target,approval=record,signature=sig,approval_key=MOCK_APPROVAL_KEY)
