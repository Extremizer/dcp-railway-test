"""Fail-closed OEMixi manufacturer enrollment via authoritative dealer-cart proof.

No local DCP browser, no MSRP validation, no auto manufacturer selection.
Use an injected, production-authorized SharedDealerPriceAdapter only.
"""
from __future__ import annotations
import math
import re
import dealercostparts_manufacturer_finder_v6_6 as finder
import oem_identity_service

TRUSTED_SOURCE = "verified_local_dcp:dealer_cart:authenticated_exact_line"
MANUFACTURERS = (
    "Arctic Cat", "BRP", "Can-Am", "CFMoto", "Honda", "Indian",
    "Kawasaki", "KTM", "Polaris", "Sea-Doo", "Ski-Doo", "Suzuki", "Yamaha",
)


def manufacturer_choices():
    return tuple(m for m in MANUFACTURERS if finder.manufacturer_alias(m))


def validate_selected_manufacturer(oem, selected, price_adapter, *, category_bootstrap=None, writer=None):
    """Return validation verdict; persist identity iff matching trusted dealer proof.

    A successful price response does not imply identity unless its provenance is
    an authenticated exact dealer-cart line. Stale cache, MSRP and unauthorised
    broker responses are never sufficient.
    """
    try:
        normalized = finder.normalize_oem(oem)
    except (ValueError, TypeError):
        return {"status": "INVALID_OEM"}
    canonical = finder.manufacturer_alias(selected)
    if not normalized or not re.fullmatch(r"[A-Z0-9-]{4,40}", normalized):
        return {"status": "INVALID_OEM"}
    if canonical not in manufacturer_choices():
        return {"status": "INVALID_MANUFACTURER"}
    if price_adapter is None:
        return {"status": "VALIDATION_UNAVAILABLE"}
    if category_bootstrap is None:
        return {"status": "CATEGORY_BOOTSTRAP_UNAVAILABLE"}
    try:
        authority = category_bootstrap(canonical, normalized)
    except Exception:
        return {"status": "CATEGORY_BOOTSTRAP_UNAVAILABLE"}
    if (not isinstance(authority, dict)
            or authority.get("status") != "CATEGORY_VERIFIED"
            or authority.get("manufacturer") != canonical
            or authority.get("oem") != normalized):
        return {"status": "CATEGORY_UNVERIFIED"}
    existing = oem_identity_service.lookup(normalized)
    if existing and existing.get("manufacturer") != canonical:
        return {"status": "IDENTITY_CONFLICT"}
    try:
        proof = price_adapter.get_dealer_price_dict(canonical, normalized)
    except Exception:
        return {"status": "VALIDATION_UNAVAILABLE"}
    if not isinstance(proof, dict) or proof.get("status") != "FOUND":
        return {"status": "VALIDATION_FAILED"}
    if str(proof.get("source") or "") != TRUSTED_SOURCE:
        return {"status": "UNTRUSTED_PRICE_SOURCE"}
    if proof.get("server_cache_committed") is not True:
        return {"status": "UNCOMMITTED_DEALER_PRICE"}
    value = proof.get("dealer_price_usd")
    try:
        price = float(value)
    except (TypeError, ValueError):
        return {"status": "INVALID_DEALER_PRICE"}
    if not math.isfinite(price) or price <= 0:
        return {"status": "INVALID_DEALER_PRICE"}
    try:
        current = finder.normalize_oem(proof.get("current_oem") or normalized)
    except (ValueError, TypeError):
        return {"status": "INVALID_CURRENT_OEM"}
    if not current or not re.fullmatch(r"[A-Z0-9-]{4,40}", current):
        return {"status": "INVALID_CURRENT_OEM"}
    if current != normalized:
        known_current = oem_identity_service.lookup(current)
        if known_current and known_current.get("manufacturer") != canonical:
            return {"status": "IDENTITY_CONFLICT"}
        # Do not persist a fresh alias without independent alias authority.
        return {"status": "ALIAS_AUTHORITY_REQUIRED"}
    save = writer or oem_identity_service.remember
    try:
        if not save(normalized, canonical, source="dealer_cart_exact_line_confirmed"):
            return {"status": "PERSIST_FAILED"}
    except Exception:
        return {"status": "IDENTITY_CONFLICT"}
    return {"status": "VERIFIED", "oem": normalized,
            "manufacturer": canonical, "dealer_price_usd": price,
            "source": TRUSTED_SOURCE}
