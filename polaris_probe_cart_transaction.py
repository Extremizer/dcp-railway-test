"""Isolated fail-closed probe transaction; adapter is injected, never opened here."""
import os,uuid,re
from datetime import datetime,timezone
from decimal import Decimal
from polaris_cart_snapshot import canonical,digest
from polaris_probe_cart_delta import prove_delta
from polaris_probe_cart_recovery import ProbeLock,ProbeBusy,Journal,RecoveryOwnership
from polaris_probe_errors import classify_failure

def _result(status,price=None,mutated=False,restored=False,failure_code=None):
    result=dict(status=status,admin_unit_price_usd=price,cart_mutated=mutated,
        cart_restored=restored,production_found=False,dealer_price_usd=None)
    if failure_code:
        result["failure_code"]=failure_code
    return result

def _adapter_failure(adapter,default="AUTH_REQUIRED"):
    code=getattr(adapter,"last_failure_code",None)
    return code or default

def _failed(exc,*,mutated=False,restored=False):
    code=classify_failure(exc)
    return _result(code,mutated=mutated,restored=restored,failure_code=code)

def _safe(adapter):
    return adapter.verify() is True

def _price(adapter,oem,snapshot,line):
    value=Decimal(str(adapter.price(oem,snapshot)))
    if not value.is_finite() or value<=0 or value!=Decimal(line["unit_price"]):
        raise ValueError("PRICE_UNVERIFIED")
    return str(value)

def _valid_target(target,requested):
    if not isinstance(target,dict):
        return False
    resolved=str(target.get("resolved_oem") or "")
    if re.fullmatch(r"[A-Z0-9-]{4,40}",resolved) is None:
        return False
    if (target.get("manufacturer")!="Polaris"
        or target.get("requested_oem")!=requested
        or target.get("item_category")!="Parts"
        or target.get("exact_match") is not True):
        return False
    if resolved!=requested and target.get("replacement_proven") is not True:
        return False
    return True

def recover_probe(adapter,*,lock_path,journal_path):
    """Only recover a journal with proven exact delta. Never guess after uncertain add."""
    journal=Journal(journal_path)
    try:
        with RecoveryOwnership(lock_path,journal_path):
            rec=journal.read()
            if rec is None:return _result("NO_RECOVERY_NEEDED",restored=True)
            try:
                if not _safe(adapter):return _result("RECOVERY_REQUIRED")
                original=rec["initial_cart_snapshot"]
                delta=rec.get("delta")
                if not isinstance(delta,dict):return _result("RECOVERY_REQUIRED")
                resolved=rec.get("resolved_oem") or rec["requested_oem"]
                current=adapter.snapshot()
                proof=prove_delta(original,current,resolved)
                if proof!=delta:return _result("RECOVERY_REQUIRED")
                journal.save({**rec,"cleanup_state":"STARTED","last_step":"RECOVERY_CLEANUP"})
                if not _safe(adapter):return _result("RECOVERY_REQUIRED")
                if proof["kind"]=="new":adapter.remove_line(proof["token"])
                else:adapter.decrement_one(proof["token"])
                if not _safe(adapter) or digest(adapter.snapshot())!=digest(original):
                    return _result("RECOVERY_REQUIRED")
                journal.save({**rec,"mutation_state":"RESTORED","cleanup_state":"RESTORED"})
                journal.clear()
                import pathlib
                pathlib.Path(lock_path).unlink()
                return _result("RESTORED",restored=True,mutated=True)
            except Exception:return _result("RECOVERY_REQUIRED")
    except ProbeBusy:return _result("RECOVERY_REQUIRED")

def run_probe(oem,adapter,*,lock_path,journal_path,dry_run=False,authoritative_target=None):
    journal=Journal(journal_path)
    probe_id=uuid.uuid4().hex
    try:
        with ProbeLock(lock_path,probe_id=probe_id,journal_path=journal_path) as ownership:
            if journal.read() is not None:return _result("RECOVERY_REQUIRED")
            try:
                if not _safe(adapter):
                    code=_adapter_failure(adapter)
                    return _result(code,failure_code=code)
                initial=adapter.snapshot()
                base=canonical(initial)
                requested=str(oem or "").strip().upper()
                if not re.fullmatch(r"[A-Z0-9-]{4,40}",requested):
                    return _result("OEM_UNVERIFIED")

                requested_matches=[x for x in base["lines"] if x["sku"]==requested]
                if len(requested_matches)>1:return _result("AMBIGUOUS_SKU")

                resolved=requested
                resolved_matches=requested_matches
                target=None
                if not requested_matches:
                    # Authoritative alias already in the initial cart: do not
                    # perform part-search/navigation. The caller must provide
                    # independently verified server Category Authority evidence.
                    trusted_alias = (_valid_target(authoritative_target, requested)
                                     and authoritative_target["resolved_oem"] != requested)
                    if trusted_alias:
                        alias = authoritative_target["resolved_oem"]
                        alias_lines = [x for x in base["lines"] if x["sku"] == alias]
                        if len(alias_lines) > 1:
                            return _result("AMBIGUOUS_SKU")
                        if alias_lines:
                            if alias_lines[0]["qty"] != 1:
                                return _result("AMBIGUOUS_SKU")
                            resolved = alias
                            resolved_matches = alias_lines
                    if not resolved_matches:
                        target=adapter.find_exact_oem(requested)
                        if not _valid_target(target,requested):
                            return _result("OEM_UNVERIFIED")
                        # A verified server-side replacement must not be
                        # contradicted by browser search evidence.
                        if trusted_alias and target["resolved_oem"] != authoritative_target["resolved_oem"]:
                            return _result("OEM_UNVERIFIED")
                        resolved=target["resolved_oem"]
                        resolved_matches=[x for x in base["lines"] if x["sku"]==resolved]
                        if len(resolved_matches)>1:return _result("AMBIGUOUS_SKU")

                existing=resolved_matches
                if dry_run:
                    if not _safe(adapter) or digest(adapter.snapshot())!=digest(initial):
                        return _result("CART_CHANGED")
                    response=_result("DRY_RUN_READY",restored=True)
                    response.update(
                        mode="EXISTING_CART" if existing else "ADD_NEW_LINE",
                        requested_oem=requested,
                        resolved_oem=resolved,
                    )
                    return response

                if existing:
                    if not _safe(adapter):
                        code=_adapter_failure(adapter)
                        return _result(code,failure_code=code)
                    price=_price(adapter,resolved,initial,existing[0])
                    if not _safe(adapter) or digest(adapter.snapshot())!=digest(initial):
                        return _result("CART_CHANGED")
                    response=_result("ADMIN_DEALER_CART_PRICE_VERIFIED",price,restored=True)
                    response.update(requested_oem=requested,resolved_oem=resolved)
                    return response

                rec={"probe_id":probe_id,"owner_pid":os.getpid(),
                    "started_at":datetime.now(timezone.utc).isoformat(),
                    "owner_instance_id":ownership.owner_instance_id,
                    "requested_oem":requested,"resolved_oem":resolved,
                    "initial_cart_snapshot":base,
                    "mutation_state":"PREPARED","last_step":"PREPARED",
                    "cleanup_state":"PENDING","delta":None}
                journal.save(rec)
                delta=None;price=None;restored=False;probe_error=None
                try:
                    if not _safe(adapter):
                        raise RuntimeError(_adapter_failure(adapter))
                    rec.update(mutation_state="MUTATION_STARTED",last_step="ADD_ONE")
                    journal.save(rec)
                    adapter.add_one(requested)
                    if not _safe(adapter):
                        raise RuntimeError(_adapter_failure(adapter))
                    after=adapter.snapshot()
                    delta=prove_delta(initial,after,resolved)
                    rec.update(delta=delta,mutation_state="MUTATION_CONFIRMED",last_step="AFTER_ADD")
                    journal.save(rec)
                    if not _safe(adapter):
                        raise RuntimeError(_adapter_failure(adapter))
                    line=next(x for x in canonical(after)["lines"] if x["token"]==delta["token"])
                    price=_price(adapter,resolved,after,line)
                    rec.update(mutation_state="PRICE_CAPTURED",last_step="PRICE")
                    journal.save(rec)
                except Exception as exc:
                    price=None
                    probe_error=exc
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
                if not restored:return _result("RESTORATION_FAILED",mutated=True,failure_code="RESTORATION_FAILED")
                journal.clear()
                if price is None:
                    return _failed(probe_error or RuntimeError("PROBE_INTERNAL_ERROR"),
                                   mutated=True,restored=True)
                response=_result("ADMIN_PROBE_CART_PRICE_VERIFIED",price,True,True)
                response.update(requested_oem=requested,resolved_oem=resolved)
                return response
            except Exception as exc:
                return _failed(exc)
    except ProbeBusy:return _result("BUSY")
