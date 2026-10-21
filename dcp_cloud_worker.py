"""Cloud DCP producer state machine. No Windows agent, no fallback, no automatic admission.

A single owner is acquired while broker admission is CLOSED; separate Release
Control owns opening admission. In-flight uncertainty is durable and blocks
subsequent claims. Polaris stays HOLD until the independent cloud cart runtime
and proof contract are accepted.
"""
from __future__ import annotations
import json
import os
import math
from pathlib import Path

class CloudWorkerBlocked(RuntimeError): pass

ALLOWED_FAILURES={"NOT_FOUND","NO_PRICE","CLOUDFLARE","AUTH_REQUIRED","TECHNICAL_ERROR"}

def normalize_result(claim, raw):
    """Only server-verifiable dealer SEARCH evidence can become FOUND."""
    from dp_result_completion import _identity, _dealer_source, CompletionError
    manufacturer,query=_identity(claim["manufacturer"],claim["oem"])
    if manufacturer=="Polaris":
        return {"status":"TECHNICAL_ERROR","error_code":"polaris_cloud_cart_not_accepted"}
    if not isinstance(raw,dict):
        return {"status":"TECHNICAL_ERROR","error_code":"browser_response_invalid"}
    status=str(raw.get("status") or "TECHNICAL_ERROR").upper()
    if status!="FOUND":
        return {"status":status if status in ALLOWED_FAILURES else "TECHNICAL_ERROR",
                "error_code":status[:80]}
    try:
        m,oem=_identity(raw.get("manufacturer"),raw.get("query_oem"))
        _,current=_identity(m,raw.get("current_oem") or oem)
        _,matched=_identity(m,raw.get("matched_sku"))
        value=raw.get("dealer_price_usd")
        if (m!=manufacturer or oem!=query or matched!=query or
                type(value) not in (float,int) or not math.isfinite(value)
                or value<=0 or raw.get("currency")!="USD"
                or raw.get("authorized") is not True):
            raise ValueError("unverified_identity_or_price")
        _dealer_source(raw.get("source"),manufacturer,query)
        return {"status":"FOUND","manufacturer":m,"query_oem":oem,
                "matched_sku":matched,"current_oem":current,
                "currency":"USD","dealer_price_usd":value,
                "source":raw["source"],"name":str(raw.get("name") or "")[:300]}
    except (KeyError,ValueError,CompletionError,TypeError):
        return {"status":"TECHNICAL_ERROR","error_code":"dealer_evidence_rejected"}

class CloudWorker:
    def __init__(self, *, client, browser, lookup, notifier, journal_dir, enabled=False):
        self.client=client
        self.browser=browser
        self.lookup=lookup
        self.notifier=notifier
        self.enabled=bool(enabled)
        self.journal=Path(journal_dir)/"dcp-cloud-uncertain.json"
        self.blocked=False
        self.human_gate=False
        self.state="DISABLED" if not self.enabled else "STARTING"

    def _write_uncertain(self,phase,claim=None):
        data={"phase":phase}
        if claim:
            data["request_id"]=claim.get("request_id")
            data["generation"]=claim.get("claim_generation")
        tmp=self.journal.with_suffix(".tmp")
        fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o600)
        try:
            with os.fdopen(fd,"w") as f:
                json.dump(data,f,separators=(",",":"))
                f.flush()
                os.fsync(f.fileno())
        except Exception:
            try:os.unlink(tmp)
            except OSError:pass
            raise
        os.replace(tmp,self.journal)
        directory=os.open(self.journal.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)

    def _clear_uncertain(self):
        self.journal.unlink()
        directory=os.open(self.journal.parent,os.O_RDONLY)
        try:os.fsync(directory)
        finally:os.close(directory)

    def acknowledge_manual_recovery(self):
        if self.journal.exists() or self.blocked:
            return False
        if self.browser.status()!="READY":
            return False
        self.human_gate=False
        return True

    def tick(self):
        if not self.enabled:
            self.state="DISABLED"
            return self.state
        if self.journal.exists() or self.blocked:
            self.blocked=True
            self.state="RECONCILIATION_REQUIRED"
            return self.state
        state=self.browser.status()
        if state!="READY":
            self.human_gate=True
            self.state=state
            if state in ("AUTH_REQUIRED","CLOUDFLARE"):
                try:self.notifier.notify(state)
                except Exception:pass  # Telegram outage is not permission to search
            # Hold the existing owner lease during human recovery, never claim.
            if self.client.owner_epoch:
                try:self.client.renew()
                except Exception:self.blocked=True;self.state="RECONCILIATION_REQUIRED"
            return self.state
        if self.human_gate:
            self.state="HUMAN_CONFIRMATION_REQUIRED"
            return self.state
        try:
            if not self.client.owner_epoch:
                self.client.acquire()  # Requires separate CLOSED admission on server.
                self.state="OWNER_ACQUIRED_ADMISSION_CLOSED"
                return self.state
            self.client.renew()
            self._write_uncertain("claim_request_uncertain")
            claim=self.client.next()
            if not claim:
                self._clear_uncertain()
                self.state="IDLE"
                return self.state
            self._write_uncertain("lookup_inflight",claim)
            manufacturer=claim.get("manufacturer")
            if manufacturer=="Polaris":
                raw={"status":"TECHNICAL_ERROR"}
            else:
                raw=self.lookup(manufacturer,claim["oem"],category_evidence=claim)
            result=normalize_result(claim,raw)
            self._write_uncertain("result_delivery_uncertain",claim)
            self.client.complete(claim,result)
            self._clear_uncertain()
            self.state="COMPLETED" if result["status"]=="FOUND" else result["status"]
            if self.state in ("AUTH_REQUIRED","CLOUDFLARE"):
                self.human_gate=True
            return self.state
        except Exception:
            self.blocked=True
            self.state="RECONCILIATION_REQUIRED"
            return self.state
