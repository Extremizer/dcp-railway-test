"""Consumer adapters for Canonical Price Contract v1.

No pricing formulas live here. Adapters expose only already-verified raw fields
in shapes compatible with existing consumers.
"""
from __future__ import annotations

from unified_price_contract_v1 import (
    STATUS_VERIFIED,
    dealer_legacy_offer,
)


def oemixi_offer_from_contract(contract: dict) -> dict:
    legacy = dealer_legacy_offer(contract)
    return {
        "status": legacy.get("status"),
        "manufacturer": contract.get("manufacturer"),
        "requested_oem": contract.get("requested_oem"),
        "oem": contract.get("matched_oem") or contract.get("requested_oem"),
        "item_category": contract.get("item_category"),
        "dl_usd": (
            contract.get("dealer_price_usd")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "price_source": (
            contract.get("dealer_price_source")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "dealer_verified_at": (
            contract.get("dealer_verified_at")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_usd": (
            contract.get("msrp_usd")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_source": (
            contract.get("msrp_source")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_verified_at": (
            contract.get("msrp_verified_at")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "price_contract_version": contract.get("version"),
    }


def procenka_raw_price_inputs(contract: dict) -> dict:
    """Fields existing Procenka code can consume without recalculation here."""
    return {
        "manufacturer": contract.get("manufacturer"),
        "query_oem": contract.get("requested_oem"),
        "oem": contract.get("matched_oem") or contract.get("requested_oem"),
        "item_category": contract.get("item_category"),
        "_dealer_price_usd": (
            contract.get("dealer_price_usd")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "_dealer_price_source": (
            contract.get("dealer_price_source")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "_dealer_price_checked_at": (
            contract.get("dealer_verified_at")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "_dealer_price_status": (
            "FOUND"
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else contract.get("dealer_price_status")
        ),
        # Existing client card/pricing logic treats price as MSRP USD input.
        "price": (
            contract.get("msrp_usd")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "_msrp_source": (
            contract.get("msrp_source")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "_msrp_checked_at": (
            contract.get("msrp_verified_at")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "price_contract_version": contract.get("version"),
    }


def probnik_raw_price_inputs(contract: dict) -> dict:
    """Raw verified USD values only; existing Probnik code computes RUB/RRP."""
    return {
        "manufacturer": contract.get("manufacturer"),
        "oem": contract.get("requested_oem"),
        "current_oem": contract.get("matched_oem") or contract.get("requested_oem"),
        "item_category": contract.get("item_category"),
        "dp_usd": (
            contract.get("dealer_price_usd")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_usd": (
            contract.get("msrp_usd")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "dealer_price_source": (
            contract.get("dealer_price_source")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "dealer_verified_at": (
            contract.get("dealer_verified_at")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_source": (
            contract.get("msrp_source")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_verified_at": (
            contract.get("msrp_verified_at")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "price_contract_version": contract.get("version"),
    }


__all__ = [
    "oemixi_offer_from_contract",
    "procenka_raw_price_inputs",
    "probnik_raw_price_inputs",
]
