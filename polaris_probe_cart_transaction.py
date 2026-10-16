"""Isolated probe-cart transaction, driven ONLY by an injected fake/authorized adapter.

Adapter protocol: snapshot(), verify(), add_one(oem), remove_line(token),
price(oem,snapshot). No live adapter is provided here.
"""
import os,uuid
from datetime import datetime,timezone
from polaris_cart_snapshot import canonical,digest,exact_new_line
from polaris_probe_cart_recovery import ProbeLock,ProbeBusy,Journal

def run_probe(oem, adapter, *, lock_path, journal_path):
    def fail(reason,restored=False,mutated=False):
        return {"status":reason,"admin_unit_price_usd":None,"cart_mutated":mutated,
                "cart_restored":restored,"production_found":False,"dealer_price_usd":None}
    journal=Journal(journal_path)
    try:
        with ProbeLock(lock_path):
            if journal.read() is not None:
                return fail("RECOVERY_REQUIRED")
            try:
                if adapter.verify() is not True:
                    return fail("AUTH_REQUIRED")
                initial=adapter.snapshot()
                base=canonical(initial)
                matches=[x for x in base["lines"] if x["sku"]==oem]
                if len(matches)>1:return fail("AMBIGUOUS_SKU")
                if matches:
                    price=adapter.price(oem,initial)
                    if digest(adapter.snapshot())!=digest(initial):
                        return fail("CART_CHANGED")
                    if str(price)!=str(matches[0]["unit_price"]):
                        return fail("PRICE_UNVERIFIED")
                    return {"status":"ADMIN_DEALER_CART_PRICE_VERIFIED","admin_unit_price_usd":str(price),
                            "cart_mutated":False,"cart_restored":True,
                            "production_found":False,"dealer_price_usd":None}
                record={"probe_id":uuid.uuid4().hex,"requested_oem":oem,
                        "started_at":datetime.now(timezone.utc).isoformat(),"owner_pid":os.getpid(),
                        "initial_cart_snapshot":base,"mutation_state":"PREPARED",
                        "affected_line_token":None,"qty_before":0,"qty_after":1,
                        "last_step":"PREPARED","cleanup_state":"PENDING"}
                journal.save(record)
                added=None; price=None; restored=False
                try:
                    record["mutation_state"]="MUTATION_STARTED";record["last_step"]="ADD_ONE";journal.save(record)
                    adapter.add_one(oem)
                    after=adapter.snapshot()
                    added=exact_new_line(initial,after,oem)
                    record["affected_line_token"]=added["token"]
                    record["mutation_state"]="MUTATION_CONFIRMED"
                    record["last_step"]="AFTER_ADD";journal.save(record)
                    price=adapter.price(oem,after)
                    if str(price)!=added["unit_price"]:raise ValueError("PRICE_UNVERIFIED")
                    record["mutation_state"]="PRICE_CAPTURED";record["last_step"]="PRICE";journal.save(record)
                except Exception:
                    pass
                finally:
                    record["cleanup_state"]="STARTED";record["last_step"]="CLEANUP";journal.save(record)
                    try:
                        # When ADD outcome is uncertain, never guess the token:
                        # leave durable recovery marker and block future probes.
                        if added is not None:
                            adapter.remove_line(added["token"])
                        restored=digest(adapter.snapshot())==digest(initial)
                    except Exception:
                        restored=False
                    record["cleanup_state"]="RESTORED" if restored else "RECOVERY_REQUIRED"
                    record["mutation_state"]=record["cleanup_state"]
                    journal.save(record)
                if not restored:return fail("RESTORATION_FAILED",mutated=True)
                journal.clear()
                if price is None or added is None:return fail("PROBE_UNVERIFIED",restored=True,mutated=True)
                return {"status":"ADMIN_PROBE_CART_PRICE_VERIFIED",
                        "admin_unit_price_usd":str(price),"cart_mutated":True,
                        "cart_restored":True,"production_found":False,"dealer_price_usd":None}
            except Exception:
                return fail("PROBE_UNVERIFIED")
    except ProbeBusy:
        return fail("BUSY")
