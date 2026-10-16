"""Opt-in read-only wiring for the existing DCP CDP/preflight boundary.

No producer entry point is changed. No navigation, click, fetch, cart mutation,
cache write, or production FOUND. SIGN OUT alone is not dealer authentication.
"""
from __future__ import annotations
from datetime import datetime, timezone
from urllib.parse import urlsplit

import oemixibot_dcp_price as existing_dcp
from polaris_dealer_cart_session_adapter import read_existing_cart

CART_URL = "https://www.dealercostparts.com/cart"
READ_ONLY_DOCUMENT_EXPRESSION = r"""(()=> {
  const before=location.href;
  const html=document.documentElement?.outerHTML||'';
  const after=location.href;
  return {url_before:before,url_after:after,html,
          context_changed:before!==after};
})()"""

def inspect_existing_cart(*, requested_oem, category_evidence, session_evidence,
                          observed_at=None, now=None, tabs=None, evaluate=None):
    """Read only an already-open exact cart tab using existing CDP primitives.

    trusted_preflight and dealer_authenticated must be supplied by existing
    trusted upstream context; this function NEVER upgrades generic SIGN OUT.
    """
    def reject(reason):
        return {"status":"DEALER_PRICE_UNVERIFIED","reason":reason,"dealer_price_usd":None}
    if not isinstance(session_evidence,dict) or not (
        session_evidence.get("trusted_preflight") is True
        and session_evidence.get("dealer_authenticated") is True
        and session_evidence.get("dealer_context_verified") is True
    ):
        return reject("dealer_preflight_not_proven")
    if session_evidence.get("cloudflare") or session_evidence.get("challenged"):
        return reject("CLOUDFLARE")
    if not isinstance(category_evidence,dict) or not (
        category_evidence.get("manufacturer")=="Polaris"
        and category_evidence.get("oem")==requested_oem
        and category_evidence.get("item_category")=="Parts"
        and all(category_evidence.get(k) for k in
            ("category_source_kind","category_source_ref","category_verified_at"))
    ):
        return reject("CATEGORY_UNVERIFIED")
    tabs_reader=tabs if tabs is not None else existing_dcp._tabs
    evaluator=evaluate if evaluate is not None else existing_dcp._runtime_evaluate
    try:
        entries=tabs_reader()
        eligible=[]
        for tab in entries:
            if not existing_dcp._is_dcp_page(tab):
                continue
            p=urlsplit(str(tab.get("url") or ""))
            if (p.scheme=="https" and p.netloc=="www.dealercostparts.com"
                and p.path.rstrip("/")=="/cart" and not p.query and not p.fragment):
                eligible.append(tab)
        if len(eligible)!=1:
            return reject("cart_tab_missing_or_ambiguous")
        tab=eligible[0]
        def reader():
            document=evaluator(tab,READ_ONLY_DOCUMENT_EXPRESSION)
            if not isinstance(document,dict):
                return None
            # The trusted dealer proof is external, not inferred from HTML.
            return {**document,"trusted_dealer_session":True}
        observed_at=observed_at or datetime.now(timezone.utc).isoformat()
        result=read_existing_cart(reader,requested_oem=requested_oem,
            category_evidence=category_evidence,
            session_evidence={"trusted_preflight":True,"dealer_authenticated":True},
            observed_at=observed_at,
            source_provenance="existing_dcp_authenticated_cart:read_only_snapshot",
            now=now)
        if result.get("status")!="VALIDATED_DEALER_CART_CANDIDATE":
            return result
        return {**result,"dealer_price_usd":None,"production_found":False}
    except Exception:
        return reject("read_only_session_unavailable")
