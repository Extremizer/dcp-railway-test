"""Fail-closed dealer identity gate for existing read-only Polaris cart wiring.

Only the established dp_sync_agent._preflight_dcp() supplies READY.
The displayname is used for confidential equality checking, never returned/logged.
No producer, cart mutation, completion, cache, or ADMIN integration.
"""
from __future__ import annotations
import hmac
import os
from urllib.parse import urlsplit

import oemixibot_dcp_price as dcp

IDENTITY_READ_ONLY_JS = r"""(() => {
 const before=location.href;
 const nodes=[...document.querySelectorAll('#persistant-nav .displayname')];
 const names=nodes.map(n => (n.textContent||'').trim());
 const logout=[...document.querySelectorAll('a[href]')].some(a => {
  try { const u=new URL(a.getAttribute('href'),location.href);
        return u.origin===location.origin && u.pathname==='/account/logoff'; }
  catch (_) { return false; }
 });
 const after=location.href;
 return {before,after,names,logout};
})()"""

def verify_dealer_session(*, expected=None, preflight=None, tabs=None, evaluate=None):
    """Return ONLY trusted boolean flags and non-sensitive status.

    Injected callables are for isolated testing; operational default uses existing
    preflight, CDP tab discovery, and read-only Runtime.evaluate.
    """
    def fail(status):
        return {"status":status,"trusted_preflight":False,
                "dealer_authenticated":False,"dealer_context_verified":False}
    value=expected if expected is not None else os.getenv("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT")
    if not isinstance(value,str) or not value.strip():
        return fail("DEALER_IDENTITY_CONFIG_MISSING")
    try:
        if preflight is None:
            from dp_sync_agent import _preflight_dcp
            preflight=_preflight_dcp
        if preflight()!="READY":
            return fail("DEALER_IDENTITY_PREFLIGHT_REJECTED")
        get_tabs=tabs if tabs is not None else dcp._tabs
        run=evaluate if evaluate is not None else dcp._runtime_evaluate
        matches=[]
        for tab in get_tabs():
            url=str(tab.get("url") or "")
            p=urlsplit(url)
            if (dcp._is_dcp_page(tab) and p.scheme=="https"
                and p.netloc=="www.dealercostparts.com"
                and p.path.rstrip("/")=="/cart"
                and not p.query and not p.fragment):
                matches.append(tab)
        if len(matches)!=1:
            return fail("DEALER_IDENTITY_CART_CONTEXT_UNVERIFIED")
        result=run(matches[0],IDENTITY_READ_ONLY_JS)
        if not isinstance(result,dict) or result.get("logout") is not True:
            return fail("DEALER_IDENTITY_AUTH_UNVERIFIED")
        if result.get("before")!="https://www.dealercostparts.com/cart" or result.get("after")!=result.get("before"):
            return fail("DEALER_IDENTITY_CART_CONTEXT_UNVERIFIED")
        names=result.get("names")
        if not isinstance(names,list) or len(names)!=1 or not isinstance(names[0],str) or not names[0].strip():
            return fail("DEALER_IDENTITY_MARKER_MISSING")
        actual=names[0].strip().casefold().encode("utf-8")
        approved=value.strip().casefold().encode("utf-8")
        if not hmac.compare_digest(actual,approved):
            return fail("DEALER_IDENTITY_MISMATCH")
        return {"status":"DEALER_IDENTITY_VERIFIED","trusted_preflight":True,
                "dealer_authenticated":True,"dealer_context_verified":True}
    except Exception:
        return fail("DEALER_IDENTITY_UNVERIFIED")

def inspect_approved_dealer_cart(*, requested_oem, category_evidence, expected=None,
                                 preflight=None,tabs=None,evaluate=None,
                                 observed_at=None,now=None):
    """Compose identity proof with accepted read-only cart wiring only."""
    from polaris_cart_existing_session_wiring import inspect_existing_cart
    identity=verify_dealer_session(expected=expected,preflight=preflight,tabs=tabs,evaluate=evaluate)
    if identity["dealer_context_verified"] is not True:
        return {"status":"DEALER_PRICE_UNVERIFIED","reason":identity["status"],
                "dealer_price_usd":None,"production_found":False}
    result=inspect_existing_cart(requested_oem=requested_oem,category_evidence=category_evidence,
        session_evidence=identity,observed_at=observed_at,now=now,tabs=tabs,evaluate=evaluate)
    return {**result,"dealer_price_usd":None,"production_found":False}
