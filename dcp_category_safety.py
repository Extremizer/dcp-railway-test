"""Stage 2 fail-closed DCP evidence gate, used by the existing price lookup.

No routing heuristics, manufacturer iteration, cache writer or cart automation.
Route authority must come from independently verified manufacturer/category evidence.
"""
from dataclasses import dataclass
from typing import Any
import math

@dataclass(frozen=True)
class Decision:
    status: str
    manufacturer: str | None = None
    category: str | None = None
    requested_oem: str | None = None
    matched_sku: str | None = None
    current_oem: str | None = None
    msrp_usd: float | None = None
    dealer_usd: float | None = None
    mutation: bool = False

def _sku(value: Any) -> str:
    return "".join(c for c in str(value or "").upper() if c.isalnum())

def _price(value: Any) -> float | None:
    try:
        n = float(value)
        return n if math.isfinite(n) and n > 0 else None
    except (TypeError, ValueError):
        return None

def evaluate(evidence: dict[str, Any]) -> Decision:
    m = str(evidence.get("manufacturer") or "").strip()
    requested = _sku(evidence.get("requested_oem") or evidence.get("requested_sku"))
    category = str(evidence.get("category") or "").strip()
    matched = _sku(evidence.get("matched_sku") or evidence.get("cart_sku"))
    current = _sku(evidence.get("current_oem"))
    base = dict(manufacturer=m or None, category=category or None,
                requested_oem=requested or None, matched_sku=matched or None,
                current_oem=current or None)
    def stop(status: str) -> Decision:
        return Decision(status=status, **base)
    if (not m or m.upper() in {"UNKNOWN", "__AUTO__", "AUTO"}
            or not evidence.get("manufacturer_verified", False)):
        return stop("UNKNOWN_MANUFACTURER")
    if not requested:
        return stop("NO_EXACT_SKU")
    if not category or not evidence.get("category_verified", False):
        return stop("CATEGORY_UNVERIFIED")
    provider = str(evidence.get("provider_status") or "").upper()
    if provider in {"CLOUDFLARE", "AUTH_REQUIRED"}:
        return stop(provider)
    http = evidence.get("http_status")
    if http == 429:
        return stop("HTTP_429")
    if type(http) is int and http >= 500:
        return stop("HTTP_5XX")
    if evidence.get("cart_mutation_requested"):
        if not evidence.get("owner_go", False):
            return stop("OWNER_GO_REQUIRED")
        if not evidence.get("baseline_verified", False):
            return stop("CART_BASELINE_UNVERIFIED")
        if not evidence.get("stable_line_identity", False):
            return stop("CART_BASELINE_UNVERIFIED")
        if not evidence.get("cart_lock_acquired", False):
            return stop("CART_CONFLICT")
        # Stage C2 is disabled even with all permissions satisfied.
        return stop("CART_STAGE_C2_DISABLED")
    if evidence.get("sku_ambiguous") or len(evidence.get("results") or []) > 1 and not matched:
        return stop("SKU_AMBIGUOUS")
    if evidence.get("replacement_chain_verified") is False and (
        evidence.get("replacement_requested") or current and current != requested):
        return stop("REPLACEMENT_UNVERIFIED")
    if not matched:
        return stop("NO_EXACT_SKU")
    if matched != requested and not evidence.get("replacement_chain_verified", False):
        return stop("NO_EXACT_SKU")
    if current and current != matched and not evidence.get("replacement_chain_verified", False):
        return stop("REPLACEMENT_UNVERIFIED")
    kind = str(evidence.get("price_kind") or "").upper()
    if kind not in {"DEALER", "MSRP"}:
        return stop("PRICE_KIND_UNKNOWN" if evidence.get("price_usd") is not None else "DEALER_PRICE_UNVERIFIED")
    amount = _price(evidence.get("price_usd"))
    if amount is None:
        return stop("DEALER_PRICE_UNVERIFIED")
    if kind == "DEALER":
        if not evidence.get("dealer_source_verified", False) or not evidence.get("line_verified", False):
            return stop("DEALER_PRICE_UNVERIFIED")
        return Decision(status="FOUND", dealer_usd=amount, **base)
    if not evidence.get("msrp_source_verified", False):
        return stop("PRICE_KIND_UNKNOWN")
    return Decision(status="MSRP_ONLY", msrp_usd=amount, **base)

def allow_cart_mutation(*, owner_go=False, baseline_verified=False,
                        stable_line_identity=False, cart_lock_acquired=False) -> bool:
    """Stage C2 is explicitly inactive; never mutate from this Stage 2 gate."""
    return False
