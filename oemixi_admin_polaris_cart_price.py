"""ADMIN-only, read-only Polaris dealer-cart display adapter.

No broker result completion, cache writes, MSRP fallback, producer or global FOUND.
Category must be resolved by the existing SERVER authority, never user text.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import re

from dcp_category_authority import resolve as server_category_resolve
from polaris_dealer_session_identity_guard import inspect_approved_dealer_cart

def admin_cart_price(oem, *, catalog_connection=None, category_resolver=None,
                     inspect=None, now=None):
    def denied():
        return {"status":"DEALER_PRICE_UNVERIFIED","dealer_price_usd":None,
                "production_found":False,"admin_unit_price_usd":None}
    if not isinstance(oem,str) or not re.fullmatch(r"[A-Z0-9-]{4,40}",oem):
        return denied()
    # Authorized catalog context only. Neither Telegram input nor the live page
    # is a category-authority source. No second authority algorithm.
    if category_resolver is None:
        if catalog_connection is None:
            return denied()
        category_resolver=lambda manufacturer,sku:server_category_resolve(catalog_connection,manufacturer,sku)
    try:
        evidence=category_resolver("Polaris",oem)
        if not isinstance(evidence,dict) or evidence.get("item_category")!="Parts":
            return denied()
        if evidence.get("resolved_oem")!=oem:
            return denied()  # no replacement-chain authority in this adapter
        if not all(isinstance(evidence.get(k),str) and evidence[k].strip() for k in
                   ("category_source_kind","category_source_ref","category_verified_at")):
            return denied()
        stamp=datetime.fromisoformat(evidence["category_verified_at"].replace("Z","+00:00"))
        current=now or datetime.now(timezone.utc)
        if stamp.tzinfo is None or current.tzinfo is None or not (0 <= (current-stamp).total_seconds() <= 86400):
            return denied()
        category={"manufacturer":"Polaris","oem":oem,**evidence}
        reader=inspect if inspect is not None else inspect_approved_dealer_cart
        found=reader(requested_oem=oem,category_evidence=category,observed_at=current.isoformat(),now=current)
        if not isinstance(found,dict) or found.get("status")!="VALIDATED_DEALER_CART_CANDIDATE" or found.get("production_found") is not False or found.get("dealer_price_usd") is not None:
            return denied()
        if (found.get("manufacturer")!="Polaris" or found.get("item_category")!="Parts" or
            found.get("requested_oem")!=oem or found.get("matched_sku")!=oem or
            found.get("current_oem")!=oem or found.get("currency")!="USD"):
            return denied()
        amount=Decimal(str(found.get("unit_price_usd")))
        if not amount.is_finite() or amount<=0 or amount.as_tuple().exponent < -2:
            return denied()
        return {"status":"ADMIN_DEALER_CART_PRICE_VERIFIED","manufacturer":"Polaris",
                "item_category":"Parts","oem":oem,"admin_unit_price_usd":str(amount),
                "dealer_price_usd":None,"production_found":False}
    except (Exception):
        return denied()

def format_admin_price(oem, result):
    if result.get("status")=="ADMIN_DEALER_CART_PRICE_VERIFIED":
        return ("🔎 Поиск по OEM\n\nПроизводитель: Polaris\n"
                f"OEM: {oem}\nКатегория: Parts\n\n"
                f"Дилерская цена: ${Decimal(result['admin_unit_price_usd']):.2f}")
    return f"🔎 Поиск по OEM\n\nOEM: {oem}\n\nДилерская цена недоступна."
