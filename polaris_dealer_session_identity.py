"""Dealer account identity proof layered on the EXISTING DCP preflight.

The configured identity is confidential: never log, return, or serialize it.
This module never navigates, mutates the cart, or grants production FOUND.
"""
from __future__ import annotations
import hmac
import os
from urllib.parse import urlsplit

import oemixibot_dcp_price as dcp

IDENTITY_EXPRESSION = r"""(()=> {
 const before=location.href;
 const names=[...document.querySelectorAll('#persistant-nav .displayname')]
   .map(e=>(e.textContent||'').trim());
 const logoff=[...document.querySelectorAll('a[href]')]
   .some(a=>{try{return new URL(a.getAttribute('href'),location.href).pathname==='/account/logoff'}catch(e){return false}});
 return {url_before:before,url_after:location.href,names,logoff};
})()"""

def verify_dealer_identity(*, preflight, expected_account=None, tabs=None, evaluate=None):
    """Fail-closed dealer identity, not a replacement for DCP preflight.

    preflight: callable invoking existing dp_sync_agent._preflight_dcp.
    expected_account: protected config, defaults to environment.
    """
    def reject():
        return {"trusted_preflight":False,"dealer_authenticated":False,
                "dealer_context_verified":False}
    configured=expected_account if expected_account is not None else os.getenv("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT")
    if not isinstance(configured,str) or not configured.strip() or not callable(preflight):
        return reject()
    try:
        if preflight()!="READY":
            return reject()
        tab_reader=tabs if tabs is not None else dcp._tabs
        evaluator=evaluate if evaluate is not None else dcp._runtime_evaluate
        eligible=[]
        for tab in tab_reader():
            p=urlsplit(str(tab.get("url") or ""))
            if (dcp._is_dcp_page(tab) and p.scheme=="https"
                and p.netloc=="www.dealercostparts.com" and
                p.path.rstrip("/")=="/cart" and not p.query and not p.fragment):
                eligible.append(tab)
        if len(eligible)!=1:
            return reject()
        probe=evaluator(eligible[0], IDENTITY_EXPRESSION)
        if not isinstance(probe,dict) or probe.get("logoff") is not True:
            return reject()
        if probe.get("url_before") != probe.get("url_after"):
            return reject()
        if probe.get("url_before") != "https://www.dealercostparts.com/cart":
            return reject()
        names=probe.get("names")
        if not isinstance(names,list) or len(names)!=1 or not isinstance(names[0],str):
            return reject()
        actual=names[0].strip().casefold()
        expected=configured.strip().casefold()
        if not actual or not hmac.compare_digest(actual.encode(),expected.encode()):
            return reject()
        return {"trusted_preflight":True,"dealer_authenticated":True,
                "dealer_context_verified":True}
    except Exception:
        return reject()

def inspect_verified_dealer_cart(*, requested_oem, category_evidence,
                                preflight, expected_account=None, tabs=None,
                                evaluate=None, observed_at=None, now=None):
    """One optional read-only composed entry point. No producer hook."""
    from polaris_cart_existing_session_wiring import inspect_existing_cart
    identity=verify_dealer_identity(preflight=preflight,expected_account=expected_account,
                                    tabs=tabs,evaluate=evaluate)
    if identity["dealer_context_verified"] is not True:
        return {"status":"DEALER_PRICE_UNVERIFIED","reason":"dealer_identity_unverified",
                "dealer_price_usd":None}
    return inspect_existing_cart(requested_oem=requested_oem,category_evidence=category_evidence,
                                 session_evidence=identity,observed_at=observed_at,now=now,
                                 tabs=tabs,evaluate=evaluate)
