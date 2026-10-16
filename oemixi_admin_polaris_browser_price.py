"""ADMIN-only Polaris Dealer Price through the normal-browser probe controller.

Opt-in only. Never writes catalog/cache/global FOUND. Mutation is disabled here.
"""
from __future__ import annotations

import hmac
import logging
import os
import re
import tempfile
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlsplit

from polaris_existing_browser_controller import ExistingBrowserProbeAdapter
from polaris_probe_cart_transaction import run_probe

OEM_RE=re.compile(r"[A-Z0-9-]{4,40}")

def _denied(reason="DEALER_PRICE_UNVERIFIED"):
    return {"status":"DEALER_PRICE_UNVERIFIED","reason":reason,
            "admin_unit_price_usd":None,"dealer_price_usd":None,
            "production_found":False}

def _category(oem,resolver,now):
    evidence=resolver("Polaris",oem)
    if not isinstance(evidence,dict) or evidence.get("item_category")!="Parts":
        raise ValueError("CATEGORY_UNVERIFIED")
    resolved=str(evidence.get("resolved_oem") or "")
    if OEM_RE.fullmatch(resolved) is None:
        raise ValueError("CATEGORY_UNVERIFIED")
    for k in ("category_source_kind","category_source_ref","category_verified_at"):
        if not isinstance(evidence.get(k),str) or not evidence[k].strip():
            raise ValueError("CATEGORY_UNVERIFIED")
    stamp=datetime.fromisoformat(evidence["category_verified_at"].replace("Z","+00:00"))
    if stamp.tzinfo is None or not (0 <= (now-stamp).total_seconds() <= 86400):
        raise ValueError("CATEGORY_STALE")
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
                "requested_oem":oem,"resolved_oem":category_evidence["resolved_oem"],
                "exact_match":True}
    return ExistingBrowserProbeAdapter(
        get_tab=get_tab,evaluate=evaluate,verify_session=verify,
        resolve_authority=authority,allow_mutation=False,
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
        result=run_probe(oem,runtime,lock_path=lock_path,journal_path=journal_path)
        if result.get("status")!="ADMIN_DEALER_CART_PRICE_VERIFIED":
            return _denied(result.get("status") or "DEALER_PRICE_UNVERIFIED")
        if result.get("cart_mutated") is not False or result.get("production_found") is not False:
            return _denied("UNSAFE_ADMIN_RESULT")
        if result.get("dealer_price_usd") is not None:
            return _denied("UNSAFE_ADMIN_RESULT")
        return {"status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
                "manufacturer":"Polaris","item_category":"Parts","oem":oem,
                "requested_oem":oem,"resolved_oem":result.get("resolved_oem") or evidence["resolved_oem"],
                "admin_unit_price_usd":result.get("admin_unit_price_usd"),
                "dealer_price_usd":None,"production_found":False,
                "source":"polaris:authenticated_dealer_cart:normal_browser"}
    except Exception as exc:
        logging.exception("OEMixi ADMIN browser price failed type=%s",type(exc).__name__)
        return _denied(type(exc).__name__)
