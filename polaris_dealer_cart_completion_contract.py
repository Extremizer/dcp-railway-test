"""Targeted Polaris Parts cart completion proof. Never accepts client-derived authority.

The existing signed protocol v2 endpoint, fenced claim, and server category
resolver remain authoritative. No additional queue or cache service.
"""
from __future__ import annotations
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation

SOURCE="verified_local_dcp:dealer_cart:authenticated_exact_line"

class CartProofError(ValueError):pass

def verify_polaris_cart_proof(payload,*,requested_oem,resolved_oem,category_evidence):
    if not isinstance(payload,dict):raise CartProofError("cart_proof_missing")
    if payload.get("manufacturer")!="Polaris" or payload.get("query_oem")!=requested_oem:
        raise CartProofError("cart_identity_mismatch")
    if not isinstance(category_evidence,dict) or category_evidence.get("item_category")!="Parts":
        raise CartProofError("category_not_parts")
    if (payload.get("current_oem")!=resolved_oem or payload.get("matched_sku")!=resolved_oem
        or payload.get("source")!=SOURCE or payload.get("currency")!="USD"):
        raise CartProofError("cart_sku_source_mismatch")
    proof=payload.get("dealer_cart_proof")
    if not isinstance(proof,dict):raise CartProofError("cart_proof_missing")
    if (proof.get("dealer_authenticated") is not True
        or proof.get("dealer_context_verified") is not True
        or proof.get("trusted_preflight") is not True
        or proof.get("cart_restored") is not True
        or proof.get("production_found") is not False
        or proof.get("dealer_price_usd") is not None
        or proof.get("status") not in ("ADMIN_PROBE_CART_PRICE_VERIFIED","ADMIN_DEALER_CART_PRICE_VERIFIED")
        or proof.get("requested_oem")!=requested_oem
        or proof.get("resolved_oem")!=resolved_oem
        or proof.get("matched_sku")!=resolved_oem
        or proof.get("currency")!="USD"
        or type(proof.get("quantity")) is not int or proof["quantity"]!=1):
        raise CartProofError("dealer_cart_proof_unverified")
    if not isinstance(proof.get("dealer_identity_proof_ref"),str) or not proof["dealer_identity_proof_ref"].strip():
        raise CartProofError("identity_evidence_missing")
    for key in ("category_source_kind","category_source_ref","category_verified_at"):
        if not category_evidence.get(key):
            raise CartProofError("category_evidence_missing")
        if proof.get(key)!=category_evidence[key]:
            raise CartProofError("category_evidence_mismatch")
    try:
        price=Decimal(str(payload["dealer_price_usd"]))
        unit=Decimal(str(proof["unit_price_usd"]))
        extended=Decimal(str(proof["subtotal_usd"]))
        if not (price.is_finite() and price>0 and unit==price and extended==unit):
            raise CartProofError("cart_price_mismatch")
    except (KeyError,InvalidOperation,TypeError):
        raise CartProofError("cart_price_unverified") from None
    return float(price)
