"""ADMIN-only Polaris Dealer Price through the normal-browser probe controller.

Opt-in ADMIN only. Never writes catalog/cache/global FOUND. Controlled probe-cart mutation is enabled and must be fully compensated before price is returned.
"""
from __future__ import annotations

import hmac
import hashlib
import logging
import os
import re
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit

from polaris_existing_browser_controller import ExistingBrowserProbeAdapter
from polaris_probe_cart_transaction import run_probe
from polaris_probe_errors import ProbeFailure,classify_failure

OEM_RE=re.compile(r"[A-Z0-9-]{4,40}")

def _denied(reason="DEALER_PRICE_UNVERIFIED"):
    return {"status":"DEALER_PRICE_UNVERIFIED","reason":reason,
            "admin_unit_price_usd":None,"dealer_price_usd":None,
            "production_found":False}

def _category(oem,resolver,now):
    evidence=resolver("Polaris",oem)
    if not isinstance(evidence,dict) or evidence.get("item_category")!="Parts":
        raise ProbeFailure("CATEGORY_UNVERIFIED")
    resolved=str(evidence.get("resolved_oem") or "")
    if OEM_RE.fullmatch(resolved) is None:
        raise ProbeFailure("CATEGORY_UNVERIFIED")
    for k in ("category_source_kind","category_source_ref","category_verified_at"):
        if not isinstance(evidence.get(k),str) or not evidence[k].strip():
            raise ProbeFailure("CATEGORY_UNVERIFIED")
    kind=evidence["category_source_kind"].strip()
    ttl_seconds={
        "validated_public_msrp": 720*3600,
        "validated_public_msrp_cache": 720*3600,
        "live_public_form_metadata": 24*3600,
    }.get(kind)
    if ttl_seconds is None:
        raise ProbeFailure("CATEGORY_UNVERIFIED")
    stamp=datetime.fromisoformat(evidence["category_verified_at"].replace("Z","+00:00"))
    age=(now-stamp).total_seconds() if stamp.tzinfo is not None else -1
    if stamp.tzinfo is None or not (0 <= age <= ttl_seconds):
        raise ProbeFailure("CATEGORY_STALE")
    return evidence

def _single_tab(tabs):
    found=[]
    for tab in tabs():
        if not isinstance(tab,dict) or tab.get("type")!="page":
            continue
        u=urlsplit(str(tab.get("url") or ""))
        title=str(tab.get("title") or "").lower()
        if (u.scheme,u.netloc)==("https","www.dealercostparts.com") and \
           "just a moment" not in title and "один момент" not in title:
            found.append(tab)
    if len(found)!=1:
        raise PermissionError("DCP_TAB_UNAVAILABLE")
    return found[0]

def _session_verifier(*,expected=None,preflight=None,tabs=None,evaluate=None):
    import oemixibot_dcp_price as dcp
    value=expected if expected is not None else os.getenv("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT")
    if not isinstance(value,str) or not value.strip():
        return False
    if preflight is None:
        from dp_sync_agent import _preflight_dcp
        preflight=_preflight_dcp
    tabs=tabs or dcp._tabs
    evaluate=evaluate or dcp._runtime_evaluate
    try:
        if preflight()!="READY":
            return False
        tab=_single_tab(tabs)
        result=evaluate(tab,r"""(()=>{
          const before=location.href;
          const names=[...document.querySelectorAll('#persistant-nav .displayname')]
            .map(n=>(n.textContent||'').trim()).filter(Boolean);
          const logout=[...document.querySelectorAll('a[href]')].some(a=>{
            try{const u=new URL(a.getAttribute('href'),location.href);
                return u.origin===location.origin && u.pathname==='/account/logoff';}
            catch(_){return false;}
          });
          return {before,after:location.href,names,logout};
        })()""")
        if not isinstance(result,dict) or result.get("before")!=result.get("after") or            result.get("logout") is not True:
            return False
        names=result.get("names")
        if not isinstance(names,list) or len(names)!=1 or not isinstance(names[0],str):
            return False
        return hmac.compare_digest(
            names[0].strip().casefold().encode(),
            value.strip().casefold().encode())
    except Exception:
        return False

def _real_adapter(category_evidence,*,tabs=None,evaluate=None,preflight=None,expected=None):
    import oemixibot_dcp_price as dcp
    tabs=tabs or dcp._tabs
    evaluate=evaluate or dcp._runtime_evaluate
    requested_holder={"oem":None}
    def get_tab():
        return _single_tab(tabs)
    def verify():
        return _session_verifier(expected=expected,preflight=preflight,tabs=tabs,evaluate=evaluate)
    def authority(oem):
        requested_holder["oem"]=oem
        return {"manufacturer":"Polaris","item_category":"Parts",
                "requested_oem":oem,"resolved_oem":category_evidence.get("resolved_oem"),
                "exact_match":True}
    return ExistingBrowserProbeAdapter(
        get_tab=get_tab,evaluate=evaluate,verify_session=verify,
        resolve_authority=authority,allow_mutation=True,
        timeout_seconds=30.0,poll_seconds=0.2)

def admin_browser_price(oem,*,category_resolver,adapter=None,now=None,
                        lock_path=None,journal_path=None):
    if not isinstance(oem,str) or OEM_RE.fullmatch(oem) is None:
        return _denied("OEM_UNVERIFIED")
    current=now or datetime.now(timezone.utc)
    try:
        evidence=_category(oem,category_resolver,current)
        runtime=adapter or _real_adapter(evidence)
        if lock_path is None or journal_path is None:
            root=Path(tempfile.gettempdir())/"extremizer-admin-polaris"
            root.mkdir(parents=True,exist_ok=True)
            lock_path=lock_path or root/"probe.lock"
            journal_path=journal_path or root/"probe.json"
        authority_target={"manufacturer":"Polaris","requested_oem":oem,
            "resolved_oem":evidence["resolved_oem"],"item_category":"Parts",
            "exact_match":True,"replacement_proven":evidence["resolved_oem"]!=oem}
        result=run_probe(oem,runtime,lock_path=lock_path,journal_path=journal_path,
                         authoritative_target=authority_target)
        status=result.get("status")
        if status not in {"ADMIN_DEALER_CART_PRICE_VERIFIED","ADMIN_PROBE_CART_PRICE_VERIFIED"}:
            return _denied(result.get("failure_code") or status or "DEALER_PRICE_UNVERIFIED")
        if result.get("production_found") is not False or result.get("dealer_price_usd") is not None:
            return _denied("UNSAFE_ADMIN_RESULT")
        mutated=result.get("cart_mutated")
        restored=result.get("cart_restored")
        if status=="ADMIN_DEALER_CART_PRICE_VERIFIED":
            if mutated is not False or restored is not True:
                return _denied("UNSAFE_ADMIN_RESULT")
        else:
            if mutated is not True or restored is not True:
                return _denied("UNSAFE_ADMIN_RESULT")
        return {"status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
                "manufacturer":"Polaris","item_category":"Parts","oem":oem,
                "requested_oem":oem,"resolved_oem":result.get("resolved_oem") or evidence["resolved_oem"],
                "admin_unit_price_usd":result.get("admin_unit_price_usd"),
                "dealer_price_usd":None,"production_found":False,
                "source":"polaris:authenticated_dealer_cart:normal_browser"}
    except Exception as exc:
        code=classify_failure(exc)
        logging.exception("OEMixi ADMIN browser price failed code=%s",code)
        return _denied(code)


def _dealer_identity_ref(expected=None):
    value=expected if expected is not None else os.getenv("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT")
    if not isinstance(value,str) or not value.strip():
        raise ProbeFailure("DEALER_IDENTITY_UNVERIFIED")
    normalized=value.strip().casefold().encode("utf-8")
    return "sha256:"+hashlib.sha256(b"extremizer-dcp-dealer-identity-v1\0"+normalized).hexdigest()

def producer_browser_price(oem,*,category_evidence,adapter=None,now=None,
                           lock_path=None,journal_path=None,expected=None,
                           dealer_identity_proof_ref=None):
    """Return a signed-v2 completion candidate from the existing Polaris probe.

    This does not write cache/FOUND itself. Server completion remains the only writer.
    """
    if not isinstance(oem,str) or OEM_RE.fullmatch(oem) is None:
        return {"status":"TECHNICAL_ERROR","error_code":"OEM_UNVERIFIED"}
    evidence=category_evidence if isinstance(category_evidence,dict) else {}
    required={"manufacturer":"Polaris","oem":oem,"item_category":"Parts"}
    if any(evidence.get(k)!=v for k,v in required.items()):
        return {"status":"TECHNICAL_ERROR","error_code":"CATEGORY_UNVERIFIED"}
    if any(not isinstance(evidence.get(k),str) or not evidence[k].strip()
           for k in ("category_source_kind","category_source_ref","category_verified_at")):
        return {"status":"TECHNICAL_ERROR","error_code":"CATEGORY_UNVERIFIED"}
    current=now or datetime.now(timezone.utc)
    try:
        stamp=datetime.fromisoformat(evidence["category_verified_at"].replace("Z","+00:00"))
        if stamp.tzinfo is None:
            raise ProbeFailure("CATEGORY_STALE")
        age=(current-stamp).total_seconds()
        if age<0 or age>720*3600:
            raise ProbeFailure("CATEGORY_STALE")
        runtime=adapter or _real_adapter(evidence,expected=expected)
        if lock_path is None or journal_path is None:
            root=Path(tempfile.gettempdir())/"extremizer-producer-polaris"
            root.mkdir(parents=True,exist_ok=True)
            lock_path=lock_path or root/"probe.lock"
            journal_path=journal_path or root/"probe.json"
        result=run_probe(oem,runtime,lock_path=lock_path,journal_path=journal_path)
        status=result.get("status")
        if status not in {"ADMIN_DEALER_CART_PRICE_VERIFIED","ADMIN_PROBE_CART_PRICE_VERIFIED"}:
            return {"status":"TECHNICAL_ERROR","error_code":result.get("failure_code") or status or "DEALER_PRICE_UNVERIFIED"}
        if result.get("production_found") is not False or result.get("dealer_price_usd") is not None:
            return {"status":"TECHNICAL_ERROR","error_code":"UNSAFE_ADMIN_RESULT"}
        mutated=result.get("cart_mutated")
        restored=result.get("cart_restored")
        if status=="ADMIN_DEALER_CART_PRICE_VERIFIED":
            if mutated is not False or restored is not True:
                return {"status":"TECHNICAL_ERROR","error_code":"CART_RESTORATION_UNVERIFIED"}
        elif mutated is not True or restored is not True:
            return {"status":"TECHNICAL_ERROR","error_code":"CART_RESTORATION_UNVERIFIED"}
        resolved=str(result.get("resolved_oem") or "")
        if OEM_RE.fullmatch(resolved) is None:
            return {"status":"TECHNICAL_ERROR","error_code":"OEM_UNVERIFIED"}
        price=float(result.get("admin_unit_price_usd"))
        if not (price>0):
            return {"status":"TECHNICAL_ERROR","error_code":"DEALER_PRICE_UNVERIFIED"}
        identity_ref=dealer_identity_proof_ref or _dealer_identity_ref(expected)
        from polaris_dealer_cart_completion_contract import SOURCE
        proof={
            "status":status,
            "dealer_authenticated":True,
            "dealer_context_verified":True,
            "trusted_preflight":True,
            "cart_restored":True,
            "production_found":False,
            "dealer_price_usd":None,
            "requested_oem":oem,
            "resolved_oem":resolved,
            "matched_sku":resolved,
            "currency":"USD",
            "quantity":1,
            "unit_price_usd":price,
            "subtotal_usd":price,
            "dealer_identity_proof_ref":identity_ref,
            "category_source_kind":evidence["category_source_kind"],
            "category_source_ref":evidence["category_source_ref"],
            "category_verified_at":evidence["category_verified_at"],
        }
        return {
            "status":"FOUND","manufacturer":"Polaris","query_oem":oem,
            "matched_sku":resolved,"current_oem":resolved,
            "dealer_price_usd":price,"currency":"USD","source":SOURCE,
            "name":None,"dealer_cart_proof":proof,
        }
    except Exception as exc:
        return {"status":"TECHNICAL_ERROR","error_code":classify_failure(exc)}
