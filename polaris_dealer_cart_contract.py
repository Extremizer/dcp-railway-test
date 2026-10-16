"""Pure Stage-2 Polaris Parts structured dealer-cart candidate validator.

No DOM extraction, network, cache, browser, producer, or production FOUND.
A successful result is internal evidence ONLY.
"""
from __future__ import annotations
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any
import re

LOGICAL_SOURCE = "polaris_parts:authenticated_dealer_cart:exact_line_unit_price"
STATUS_OK = "VALIDATED_DEALER_CART_CANDIDATE"
STATUS_NO = "DEALER_PRICE_UNVERIFIED"
MAX_AGE_HOURS = Decimal("24")

def _money(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        d = Decimal(str(value))
        return d if d.is_finite() and d > 0 and d.as_tuple().exponent >= -2 else None
    except (InvalidOperation, ValueError):
        return None

def validate(candidate: dict[str, Any], *, now: datetime | None = None) -> dict[str, Any]:
    def reject(reason: str) -> dict[str, Any]:
        return {"status": STATUS_NO, "reason": reason, "dealer_price_usd": None}
    if not isinstance(candidate, dict):
        return reject("invalid_candidate")
    if candidate.get("dealer_authenticated") is not True or candidate.get("context") != "dealer_cart":
        return reject("dealer_context_unverified")
    if candidate.get("manufacturer") != "Polaris":
        return reject("manufacturer_unverified")
    if candidate.get("item_category") != "Parts" or candidate.get("category_server_verified") is not True:
        return reject("category_unverified")
    requested = candidate.get("requested_oem")
    matched = candidate.get("matched_sku")
    current = candidate.get("current_oem")
    if not all(isinstance(x, str) and re.fullmatch(r"[A-Za-z0-9-]{4,40}", x) for x in (requested,matched,current)):
        return reject("sku_evidence_missing")
    if requested != matched or current != requested:
        if candidate.get("replacement_chain_verified") is not True or not candidate.get("replacement_provenance"):
            return reject("replacement_unverified")
        if candidate.get("replacement_from") != requested or candidate.get("replacement_to") != current or matched != current:
            return reject("replacement_chain_mismatch")
    if candidate.get("cart_line_unique") is not True or candidate.get("cart_line_stable") is not True:
        return reject("cart_line_ambiguous")
    line_id = candidate.get("cart_line_id")
    if not isinstance(line_id, str) or not line_id.strip():
        return reject("cart_line_id_missing")
    qty = candidate.get("quantity")
    if type(qty) is not int or qty <= 0:
        return reject("quantity_invalid")
    if candidate.get("currency") != "USD":
        return reject("currency_unverified")
    if candidate.get("source") != LOGICAL_SOURCE:
        return reject("source_unverified")
    if not isinstance(candidate.get("source_provenance"), str) or not candidate["source_provenance"].strip():
        return reject("source_provenance_missing")
    if candidate.get("price_kind") != "DEALER_CART_UNIT":
        return reject("price_kind_unverified")
    unit = _money(candidate.get("unit_price_usd"))
    if unit is None:
        return reject("unit_price_invalid")
    if candidate.get("unit_price_separately_identified") is not True:
        return reject("unit_price_ambiguous")
    subtotal = candidate.get("subtotal_usd")
    if subtotal is not None:
        subtotal_value = _money(subtotal)
        if subtotal_value is None or subtotal_value != unit * qty:
            return reject("subtotal_inconsistent")
    try:
        stamp = datetime.fromisoformat(str(candidate.get("observed_at") or "").replace("Z", "+00:00"))
        current_time = now if now is not None else datetime.now(timezone.utc)
        if stamp.tzinfo is None or current_time.tzinfo is None:
            return reject("timestamp_unverified")
        age = Decimal(str((current_time-stamp).total_seconds())) / Decimal("3600")
        if age < 0 or age > MAX_AGE_HOURS:
            return reject("timestamp_stale_or_future")
    except (TypeError, ValueError, OverflowError):
        return reject("timestamp_unverified")
    return {
        "status": STATUS_OK,
        "source": LOGICAL_SOURCE,
        "manufacturer": "Polaris",
        "item_category": "Parts",
        "requested_oem": requested,
        "matched_sku": matched,
        "current_oem": current,
        "cart_line_id": line_id,
        "quantity": qty,
        "unit_price_usd": str(unit),
        "currency": "USD",
        "observed_at": stamp.isoformat(),
        "dealer_price_usd": None,
        "production_found": False,
    }
