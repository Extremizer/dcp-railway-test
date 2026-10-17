"""Trusted Polaris read-only cart result for EXISTING signed-v2 dp_sync_agent.

No browser mutation. Never returns FOUND from MSRP, search or client context.
If requested SKU is not already the unique exact dealer-cart line, fail closed.
"""
from __future__ import annotations
import secrets
from decimal import Decimal,InvalidOperation
from polaris_dealer_session_identity_guard import verify_dealer_session,inspect_approved_dealer_cart
from polaris_dealer_cart_completion_contract import SOURCE

def inspect_claim(item,*,identity=None,inspect=None):
    def stop(code="DEALER_PRICE_UNVERIFIED"):
        return {"status":"NO_PRICE","error_code":code}
    try:
        if (not isinstance(item,dict) or item.get("manufacturer")!="Polaris"
            or item.get("item_category")!="Parts"):
            return stop("CATEGORY_UNVERIFIED")
        requested=item.get("oem");resolved=item.get("resolved_oem")
        if (not isinstance(requested,str) or not isinstance(resolved,str) or
            not requested or not resolved or not all(item.get(k) for k in
             ("category_source_kind","category_source_ref","category_verified_at"))):
            return stop("CATEGORY_UNVERIFIED")
        check=identity if identity is not None else verify_dealer_session
        reader=inspect if inspect is not None else inspect_approved_dealer_cart
        before=check()
        if not isinstance(before,dict) or before.get("status")!="DEALER_IDENTITY_VERIFIED" or before.get("dealer_context_verified") is not True:
            return stop("DEALER_IDENTITY_UNVERIFIED")
        cat={"manufacturer":"Polaris","oem":resolved,"item_category":"Parts",
             **{k:item[k] for k in ("category_source_kind","category_source_ref","category_verified_at")}}
        candidate=reader(requested_oem=resolved,category_evidence=cat)
        if (not isinstance(candidate,dict) or
            candidate.get("status")!="VALIDATED_DEALER_CART_CANDIDATE" or
            candidate.get("manufacturer")!="Polaris" or
            candidate.get("item_category")!="Parts" or
            candidate.get("requested_oem")!=resolved or
            candidate.get("matched_sku")!=resolved or
            candidate.get("current_oem")!=resolved or
            type(candidate.get("quantity")) is not int or candidate["quantity"]!=1 or
            candidate.get("currency")!="USD" or candidate.get("production_found") is not False or
            candidate.get("dealer_price_usd") is not None):
            return stop("CART_UNVERIFIED")
        price=Decimal(str(candidate["unit_price_usd"]))
        if not price.is_finite() or price<=0 or price.as_tuple().exponent < -2:
            return stop("PRICE_UNVERIFIED")
        after=check()
        if not isinstance(after,dict) or after.get("status")!="DEALER_IDENTITY_VERIFIED" or after.get("dealer_context_verified") is not True:
            return stop("DEALER_SESSION_CHANGED")
        proof={**{k:item[k] for k in ("category_source_kind","category_source_ref","category_verified_at")},
          "status":"ADMIN_DEALER_CART_PRICE_VERIFIED","requested_oem":requested,
          "resolved_oem":resolved,"matched_sku":resolved,"quantity":1,
          "currency":"USD","unit_price_usd":str(price),"subtotal_usd":str(price),
          "dealer_authenticated":True,"trusted_preflight":True,"dealer_context_verified":True,
          "dealer_identity_proof_ref":"verified-session:"+secrets.token_hex(12),
          "cart_restored":True,"dealer_price_usd":None,"production_found":False}
        return {"status":"FOUND","manufacturer":"Polaris","query_oem":requested,
                "current_oem":resolved,"matched_sku":resolved,"dealer_price_usd":float(price),
                "currency":"USD","source":SOURCE,"dealer_cart_proof":proof}
    except Exception:
        return stop("CART_UNVERIFIED")
