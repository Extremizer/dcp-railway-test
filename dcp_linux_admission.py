"""Isolated Linux CDP/ownership admission gate. Does not poll, complete, or mutate.
Real read-only identity is checked from an existing manually logged-in CDP tab.
No navigation or DCP request is initiated by this module.
"""
import fcntl
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

class AdmissionError(RuntimeError): pass
DCP_ORIGIN="https://www.dealercostparts.com"
EXACT_CART=DCP_ORIGIN+"/cart"

def verify_identity(expected, tabs, evaluate, preflight):
    """Use existing PR64 read-only guard with injected Linux CDP callbacks."""
    from polaris_dealer_session_identity_guard import verify_dealer_session
    if not isinstance(expected,str) or not expected.strip():
        return {"status":"DEALER_IDENTITY_CONFIG_MISSING","dealer_authenticated":False}
    def strict_tabs():
        found=[]
        for tab in tabs():
            url=tab.get("url","")
            p=urlsplit(url)
            if p.scheme=="https" and p.netloc=="www.dealercostparts.com" and p.path=="/cart" and not p.query and not p.fragment:
                found.append(tab)
        return found
    return verify_dealer_session(expected=expected,preflight=preflight,tabs=strict_tabs,evaluate=evaluate)

def safe_profile(state):
    root=Path(state)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or str(root) in ("/","/tmp","/app"):
        raise AdmissionError("PERSISTENT_VOLUME_UNVERIFIED")
    journal=root/"linux-producer-journal.json"
    if journal.exists():
        if journal.is_symlink() or not journal.is_file():
            raise AdmissionError("JOURNAL_INVALID")
        try:
            data=json.loads(journal.read_text(encoding="utf-8"))
        except (OSError,ValueError):
            raise AdmissionError("JOURNAL_CORRUPT")
        if data.get("state")!="CLEAN" or data.get("claim") is not None:
            raise AdmissionError("JOURNAL_QUARANTINED")
    else:
        raise AdmissionError("JOURNAL_NOT_INITIALIZED")
    lockfd=os.open(root/"linux-producer.lock",os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(lockfd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except (BlockingIOError,OSError):
        os.close(lockfd)
        raise AdmissionError("LOCAL_OWNER_LOCK_HELD")
    return lockfd

def persist_journal(root,record):
    """Atomic file update and directory fsync, no secret/token in journal."""
    allowed={"state","claim","owner_generation","updated_at","status"}
    if set(record)-allowed:
        raise AdmissionError("JOURNAL_FIELDS_DENIED")
    base=Path(root)
    path=base/"linux-producer-journal.json"
    temp=base/"linux-producer-journal.json.tmp"
    fd=os.open(temp,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        with os.fdopen(fd,"w") as f:
            json.dump(record,f,sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp,path)
        dfd=os.open(base,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(dfd)
        finally:os.close(dfd)
    finally:
        if temp.exists():temp.unlink()
    return True

def broker_admission(identity, journal_fd, signed_client):
    """No claim or completion. Signed-v2 acquire only after trusted identity+local lock."""
    if not identity.get("dealer_context_verified") or not identity.get("dealer_authenticated"):
        raise AdmissionError("DEALER_IDENTITY_NOT_VERIFIED")
    if journal_fd is None:raise AdmissionError("JOURNAL_LOCK_NOT_VERIFIED")
    if signed_client is None or not hasattr(signed_client,"acquire"):
        raise AdmissionError("SIGNED_BROKER_MISSING")
    # Caller must supply trusted independently verified broker endpoint/keys.
    receipt=signed_client.acquire()
    if receipt.get("status")!="ACQUIRED" or not receipt.get("owner_epoch"):
        raise AdmissionError("BROKER_OWNER_NOT_ACQUIRED")
    return {"status":"BROKER_ADMITTED_NO_CLAIMS","owner_generation":receipt.get("generation")}
