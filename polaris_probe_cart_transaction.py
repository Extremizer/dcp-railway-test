"""Isolated fail-closed probe transaction; adapter is injected, never opened here."""
import os,uuid,re
from datetime import datetime,timezone
from decimal import Decimal
from polaris_cart_snapshot import canonical,digest
from polaris_probe_cart_delta import prove_delta
from polaris_probe_cart_recovery import ProbeLock,ProbeBusy,Journal

def _result(status,price=None,mutated=False,restored=False):
    return dict(status=status,admin_unit_price_usd=price,cart_mutated=mutated,
        cart_restored=restored,production_found=False,dealer_price_usd=None)

def _safe(adapter):
    return adapter.verify() is True

def _price(adapter,oem,snapshot,line):
    value=Decimal(str(adapter.price(oem,snapshot)))
    if not value.is_finite() or value<=0 or value!=Decimal(line["unit_price"]):
        raise ValueError("PRICE_UNVERIFIED")
    return str(value)

def recover_probe(adapter,*,lock_path,journal_path):
    """Only recover a journal with proven exact delta. Never guess after uncertain add."""
    journal=Journal(journal_path)
    try:
        with ProbeLock(lock_path):
            rec=journal.read()
            if rec is None:return _result("NO_RECOVERY_NEEDED",restored=True)
            try:
                if not _safe(adapter):return _result("RECOVERY_REQUIRED")
                original=rec["initial_cart_snapshot"]
                delta=rec.get("delta")
                if not isinstance(delta,dict):return _result("RECOVERY_REQUIRED")
                current=adapter.snapshot()
                proof=prove_delta(original,current,rec["requested_oem"])
                if proof!=delta:return _result("RECOVERY_REQUIRED")
                journal.save({**rec,"cleanup_state":"STARTED","last_step":"RECOVERY_CLEANUP"})
                if not _safe(adapter):return _result("RECOVERY_REQUIRED")
                if proof["kind"]=="new":adapter.remove_line(proof["token"])
                else:adapter.decrement_one(proof["token"])
                if not _safe(adapter) or digest(adapter.snapshot())!=digest(original):
                    return _result("RECOVERY_REQUIRED")
                journal.save({**rec,"mutation_state":"RESTORED","cleanup_state":"RESTORED"})
                journal.clear()
                return _result("RESTORED",restored=True,mutated=True)
            except Exception:return _result("RECOVERY_REQUIRED")
    except ProbeBusy:return _result("BUSY")

def run_probe(oem,adapter,*,lock_path,journal_path,dry_run=False):
    journal=Journal(journal_path)
    probe_id=uuid.uuid4().hex
    try:
        with ProbeLock(lock_path,probe_id=probe_id):
            if journal.read() is not None:return _result("RECOVERY_REQUIRED")
            try:
                if not _safe(adapter):return _result("AUTH_REQUIRED")
                initial=adapter.snapshot()
                base=canonical(initial)
                oem=str(oem or '').strip().upper()
                if not re.fullmatch(r'[A-Z0-9-]{4,40}',oem):return _result('OEM_UNVERIFIED')
                matches=[x for x in base["lines"] if x["sku"]==oem]
                target=None
                if not matches:
                    target=adapter.find_exact_oem(oem)
                    if not isinstance(target,dict) or target.get('manufacturer')!='Polaris' or target.get('requested_oem')!=oem or target.get('resolved_oem')!=oem or target.get('item_category')!='Parts' or target.get('exact_match') is not True:
                        return _result('OEM_UNVERIFIED')
                if len(matches)>1:return _result("AMBIGUOUS_SKU")
                if dry_run:
                    if not _safe(adapter) or digest(adapter.snapshot())!=digest(initial):return _result("CART_CHANGED")
                    response=_result("DRY_RUN_READY",restored=True)
                    response.update(mode="EXISTING_CART" if matches else "ADD_NEW_LINE",requested_oem=oem,resolved_oem=oem)
                    return response
                if matches:
                    if not _safe(adapter):return _result("AUTH_REQUIRED")
                    price=_price(adapter,oem,initial,matches[0])
                    if not _safe(adapter) or digest(adapter.snapshot())!=digest(initial):
                        return _result("CART_CHANGED")
                    return _result("ADMIN_DEALER_CART_PRICE_VERIFIED",price,restored=True)
                rec={"probe_id":probe_id,"owner_pid":os.getpid(),
                    "started_at":datetime.now(timezone.utc).isoformat(),
                    "requested_oem":oem,"initial_cart_snapshot":base,
                    "mutation_state":"PREPARED","last_step":"PREPARED",
                    "cleanup_state":"PENDING","delta":None}
                journal.save(rec)
                delta=None;price=None;restored=False
                try:
                    if not _safe(adapter):raise ValueError("AUTH_REQUIRED")
                    rec.update(mutation_state="MUTATION_STARTED",last_step="ADD_ONE")
                    journal.save(rec)
                    adapter.add_one(oem)
                    if not _safe(adapter):raise ValueError("AUTH_REQUIRED")
                    after=adapter.snapshot()
                    delta=prove_delta(initial,after,oem)
                    rec.update(delta=delta,mutation_state="MUTATION_CONFIRMED",last_step="AFTER_ADD")
                    journal.save(rec)
                    if not _safe(adapter):raise ValueError("AUTH_REQUIRED")
                    line=next(x for x in canonical(after)["lines"] if x["token"]==delta["token"])
                    price=_price(adapter,oem,after,line)
                    rec.update(mutation_state="PRICE_CAPTURED",last_step="PRICE")
                    journal.save(rec)
                except Exception:
                    price=None
                finally:
                    rec.update(cleanup_state="STARTED",last_step="CLEANUP")
                    journal.save(rec)
                    try:
                        if delta is not None and _safe(adapter):
                            if delta["kind"]=="new":adapter.remove_line(delta["token"])
                            else:adapter.decrement_one(delta["token"])
                        restored=_safe(adapter) and digest(adapter.snapshot())==digest(initial)
                    except Exception:restored=False
                    rec.update(cleanup_state="RESTORED" if restored else "RECOVERY_REQUIRED",
                               mutation_state="RESTORED" if restored else "RECOVERY_REQUIRED")
                    journal.save(rec)
                if not restored:return _result("RESTORATION_FAILED",mutated=True)
                journal.clear()
                if price is None:return _result("PROBE_UNVERIFIED",mutated=True,restored=True)
                return _result("ADMIN_PROBE_CART_PRICE_VERIFIED",price,True,True)
            except Exception:return _result("PROBE_UNVERIFIED")
    except ProbeBusy:return _result("BUSY")
