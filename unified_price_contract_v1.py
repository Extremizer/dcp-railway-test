"""Canonical Price Contract v1.

Isolated compatibility layer. It does not mutate DCP cache/broker/producer
schemas and performs no browser/network IO.

Dealer Price and MSRP are independent observations with independent
source/freshness/status. Consumers must not substitute one for the other.
"""
from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any, Callable

VERSION = "canonical-price-v1"

STATUS_VERIFIED = "VERIFIED"
STATUS_MISSING = "MISSING"
STATUS_INVALID = "INVALID"
STATUS_STALE = "STALE"
STATUS_SOURCE_UNVERIFIED = "SOURCE_UNVERIFIED"
STATUS_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
STATUS_FRESHNESS_UNSET = "FRESHNESS_UNSET"

IDENTITY_VERIFIED = "VERIFIED"
IDENTITY_INVALID = "INVALID"
IDENTITY_MISMATCH = "MISMATCH"

GATEWAY_OK = "OK"

_DEALER_SOURCE_PREFIXES = (
    "verified_local_dcp:",
    "verified_shared_gateway:",
    "owner_evidence:",
)
_MSRP_SOURCE_PREFIXES = (
    "verified_dcp_msrp:",
    "verified_public_msrp:",
    "owner_evidence:",
)


def _text(value: Any) -> str | None:
    if type(value) is not str:
        return None
    value = value.strip()
    return value or None


def _price(value: Any) -> float | None:
    if type(value) not in (int, float):
        return None
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        return None
    return value


def _parse_time(value: Any) -> datetime | None:
    raw = _text(value)
    if raw is None:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return None
    return dt.astimezone(timezone.utc)


def _fresh(
    verified_at: Any,
    *,
    max_age_hours: Any,
    now: datetime,
) -> tuple[bool, str]:
    checked = _parse_time(verified_at)
    if checked is None:
        return False, STATUS_INVALID
    if type(max_age_hours) not in (int, float):
        return False, STATUS_FRESHNESS_UNSET
    max_age = float(max_age_hours)
    if not math.isfinite(max_age) or max_age <= 0:
        return False, STATUS_FRESHNESS_UNSET
    age = (now - checked).total_seconds()
    if age < -60:
        return False, STATUS_INVALID
    if age > max_age * 3600:
        return False, STATUS_STALE
    return True, STATUS_VERIFIED


def _default_dealer_source(source: str) -> bool:
    s = source.lower()
    return s.startswith(_DEALER_SOURCE_PREFIXES) and not any(
        token in s for token in ("msrp", "retail", "rrp")
    )


def _default_msrp_source(source: str) -> bool:
    s = source.lower()
    return s.startswith(_MSRP_SOURCE_PREFIXES)


def _validate_observation(
    *,
    price: Any,
    source: Any,
    verified_at: Any,
    max_age_hours: Any,
    source_policy: Callable[[str], bool],
    identity_ok: bool,
    now: datetime,
) -> tuple[float | None, str | None, str | None, str]:
    value = _price(price)
    if value is None:
        return None, _text(source), _text(verified_at), STATUS_MISSING if price is None else STATUS_INVALID
    if not identity_ok:
        return None, _text(source), _text(verified_at), STATUS_IDENTITY_MISMATCH
    src = _text(source)
    if src is None or not source_policy(src):
        return None, src, _text(verified_at), STATUS_SOURCE_UNVERIFIED
    fresh, status = _fresh(verified_at, max_age_hours=max_age_hours, now=now)
    if not fresh:
        return None, src, _text(verified_at), status
    return value, src, _text(verified_at), STATUS_VERIFIED


def build_contract(
    *,
    manufacturer: Any,
    requested_oem: Any,
    matched_oem: Any,
    item_category: Any,
    dealer_price_usd: Any = None,
    dealer_price_source: Any = None,
    dealer_verified_at: Any = None,
    msrp_usd: Any = None,
    msrp_source: Any = None,
    msrp_verified_at: Any = None,
    dealer_max_age_hours: Any = None,
    msrp_max_age_hours: Any = None,
    gateway_status: Any = GATEWAY_OK,
    expected_manufacturer: Any = None,
    expected_requested_oem: Any = None,
    dealer_source_policy: Callable[[str], bool] = _default_dealer_source,
    msrp_source_policy: Callable[[str], bool] = _default_msrp_source,
    now: datetime | None = None,
) -> dict:
    """Validate and return one immutable-value-style canonical dict.

    Freshness policy is explicit per price. Missing policy means the price is
    present as evidence but not usable by pricing consumers.
    """
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    mfg = _text(manufacturer)
    req = _text(requested_oem)
    matched = _text(matched_oem)
    category = _text(item_category)

    identity_status = IDENTITY_VERIFIED
    if not mfg or not req or not matched:
        identity_status = IDENTITY_INVALID
    if (
        identity_status == IDENTITY_VERIFIED
        and _text(expected_manufacturer) is not None
        and mfg != _text(expected_manufacturer)
    ):
        identity_status = IDENTITY_MISMATCH
    if (
        identity_status == IDENTITY_VERIFIED
        and _text(expected_requested_oem) is not None
        and req != _text(expected_requested_oem)
    ):
        identity_status = IDENTITY_MISMATCH

    identity_ok = identity_status == IDENTITY_VERIFIED

    dp, dp_source, dp_at, dp_status = _validate_observation(
        price=dealer_price_usd,
        source=dealer_price_source,
        verified_at=dealer_verified_at,
        max_age_hours=dealer_max_age_hours,
        source_policy=dealer_source_policy,
        identity_ok=identity_ok,
        now=now,
    )
    msrp, msrp_src, msrp_at, msrp_status = _validate_observation(
        price=msrp_usd,
        source=msrp_source,
        verified_at=msrp_verified_at,
        max_age_hours=msrp_max_age_hours,
        source_policy=msrp_source_policy,
        identity_ok=identity_ok,
        now=now,
    )

    return {
        "version": VERSION,
        "manufacturer": mfg,
        "requested_oem": req,
        "matched_oem": matched,
        "item_category": category,
        "dealer_price_usd": dp,
        "dealer_price_source": dp_source,
        "dealer_verified_at": dp_at,
        "msrp_usd": msrp,
        "msrp_source": msrp_src,
        "msrp_verified_at": msrp_at,
        "identity_status": identity_status,
        "dealer_price_status": dp_status,
        "msrp_status": msrp_status,
        "gateway_status": str(gateway_status or "UNKNOWN").upper(),
    }


def from_gateway_and_msrp(
    *,
    manufacturer: str,
    requested_oem: str,
    item_category: str | None,
    dealer_result: dict | None,
    msrp_result: dict | None,
    dealer_max_age_hours: Any,
    msrp_max_age_hours: Any,
    now: datetime | None = None,
) -> dict:
    """Combine independent legacy/shared observations without DB mutation."""
    dealer = dealer_result if isinstance(dealer_result, dict) else {}
    msrp = msrp_result if isinstance(msrp_result, dict) else {}

    matched_dealer = _text(dealer.get("current_oem") or dealer.get("matched_oem"))
    matched_msrp = _text(msrp.get("current_oem") or msrp.get("matched_oem"))
    matched = matched_dealer or matched_msrp or requested_oem

    # If both observations claim different matched SKUs, neither price can be
    # safely treated as a single contract identity.
    if matched_dealer and matched_msrp and matched_dealer != matched_msrp:
        matched = None

    gateway_status = dealer.get("status") or msrp.get("gateway_status") or "UNKNOWN"

    return build_contract(
        manufacturer=dealer.get("manufacturer") or msrp.get("manufacturer") or manufacturer,
        requested_oem=requested_oem,
        matched_oem=matched,
        item_category=item_category or dealer.get("item_category") or msrp.get("item_category"),
        dealer_price_usd=dealer.get("dealer_price_usd"),
        dealer_price_source=dealer.get("source") or dealer.get("dealer_price_source"),
        dealer_verified_at=dealer.get("last_verified_at") or dealer.get("dealer_verified_at"),
        msrp_usd=msrp.get("msrp_usd"),
        msrp_source=msrp.get("source") or msrp.get("msrp_source"),
        msrp_verified_at=msrp.get("last_verified_at") or msrp.get("msrp_verified_at"),
        dealer_max_age_hours=dealer_max_age_hours,
        msrp_max_age_hours=msrp_max_age_hours,
        gateway_status=gateway_status,
        expected_manufacturer=manufacturer,
        expected_requested_oem=requested_oem,
        now=now,
    )


class CanonicalPriceGatewayAdapter:
    """Compose existing Dealer gateway + independent read-only MSRP source.

    dealer_gateway must remain the existing shared gateway path. msrp_reader is
    read-only and must not enqueue/live-search. Neither side falls back to the
    other price type.
    """
    def __init__(
        self,
        dealer_gateway,
        msrp_reader,
        *,
        dealer_max_age_hours,
        msrp_max_age_hours,
    ):
        if not callable(dealer_gateway):
            raise ValueError("dealer_gateway_required")
        if not callable(msrp_reader):
            raise ValueError("msrp_reader_required")
        for name, value in (
            ("dealer_max_age_hours", dealer_max_age_hours),
            ("msrp_max_age_hours", msrp_max_age_hours),
        ):
            if type(value) not in (int, float) or not math.isfinite(float(value)) or float(value) <= 0:
                raise ValueError(f"explicit_valid_{name}_required")
        self.dealer_gateway = dealer_gateway
        self.msrp_reader = msrp_reader
        self.dealer_max_age_hours = float(dealer_max_age_hours)
        self.msrp_max_age_hours = float(msrp_max_age_hours)

    def resolve(self, manufacturer, requested_oem, *, item_category=None):
        try:
            dealer = self.dealer_gateway(
                manufacturer,
                requested_oem,
                max_age_hours=self.dealer_max_age_hours,
            )
        except Exception:
            dealer = {
                "status": "TECHNICAL_ERROR",
                "error_code": "shared_gateway_unavailable",
            }
        if not isinstance(dealer, dict):
            dealer = {
                "status": "TECHNICAL_ERROR",
                "error_code": "invalid_gateway_response",
            }

        try:
            msrp = self.msrp_reader(
                manufacturer,
                requested_oem,
                max_age_hours=self.msrp_max_age_hours,
            )
        except Exception:
            msrp = {
                "gateway_status": "TECHNICAL_ERROR",
                "error_code": "msrp_reader_unavailable",
            }
        if not isinstance(msrp, dict):
            msrp = {
                "gateway_status": "TECHNICAL_ERROR",
                "error_code": "invalid_msrp_response",
            }

        return from_gateway_and_msrp(
            manufacturer=manufacturer,
            requested_oem=requested_oem,
            item_category=item_category,
            dealer_result=dealer,
            msrp_result=msrp,
            dealer_max_age_hours=self.dealer_max_age_hours,
            msrp_max_age_hours=self.msrp_max_age_hours,
        )


def dealer_legacy_offer(contract: dict) -> dict:
    """Compatibility adapter for OEMixi/catalog style dealer offer."""
    if contract.get("dealer_price_status") != STATUS_VERIFIED:
        gateway_status = str(contract.get("gateway_status") or "").upper()
        safe_status = (
            gateway_status
            if gateway_status not in ("", "OK", "FOUND")
            else "NO_PRICE"
        )
        return {
            "status": safe_status,
            "manufacturer": contract.get("manufacturer"),
            "requested_oem": contract.get("requested_oem"),
            "current_oem": contract.get("matched_oem"),
            "dealer_price_usd": None,
            "msrp_usd": contract.get("msrp_usd") if contract.get("msrp_status") == STATUS_VERIFIED else None,
            "price_contract_version": contract.get("version"),
        }
    return {
        "status": "FOUND",
        "manufacturer": contract.get("manufacturer"),
        "query_oem": contract.get("requested_oem"),
        "requested_oem": contract.get("requested_oem"),
        "current_oem": contract.get("matched_oem"),
        "dealer_price_usd": contract.get("dealer_price_usd"),
        "source": contract.get("dealer_price_source"),
        "last_verified_at": contract.get("dealer_verified_at"),
        "currency": "USD",
        "msrp_usd": contract.get("msrp_usd") if contract.get("msrp_status") == STATUS_VERIFIED else None,
        "msrp_source": contract.get("msrp_source") if contract.get("msrp_status") == STATUS_VERIFIED else None,
        "msrp_verified_at": contract.get("msrp_verified_at") if contract.get("msrp_status") == STATUS_VERIFIED else None,
        "price_contract_version": contract.get("version"),
    }


def client_price_inputs(contract: dict) -> dict:
    """Safe raw inputs for existing Procenka/Probnik pricing functions.

    This adapter deliberately does not calculate RUB, RRP or benefit.
    """
    return {
        "dealer_price_usd": (
            contract.get("dealer_price_usd")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_usd": (
            contract.get("msrp_usd")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "manufacturer": contract.get("manufacturer"),
        "requested_oem": contract.get("requested_oem"),
        "matched_oem": contract.get("matched_oem"),
        "item_category": contract.get("item_category"),
        "price_contract_version": contract.get("version"),
    }


def admin_price_view(contract: dict) -> dict:
    """Read-only values for Stage A administrative card."""
    return {
        "manufacturer": contract.get("manufacturer"),
        "requested_oem": contract.get("requested_oem"),
        "matched_oem": contract.get("matched_oem"),
        "item_category": contract.get("item_category"),
        "dealer_price_usd": (
            contract.get("dealer_price_usd")
            if contract.get("dealer_price_status") == STATUS_VERIFIED
            else None
        ),
        "dealer_price_source": contract.get("dealer_price_source"),
        "dealer_verified_at": contract.get("dealer_verified_at"),
        "msrp_usd": (
            contract.get("msrp_usd")
            if contract.get("msrp_status") == STATUS_VERIFIED
            else None
        ),
        "msrp_source": contract.get("msrp_source"),
        "msrp_verified_at": contract.get("msrp_verified_at"),
        "identity_status": contract.get("identity_status"),
        "dealer_price_status": contract.get("dealer_price_status"),
        "msrp_status": contract.get("msrp_status"),
        "gateway_status": contract.get("gateway_status"),
    }


__all__ = [
    "VERSION",
    "STATUS_VERIFIED",
    "STATUS_MISSING",
    "STATUS_INVALID",
    "STATUS_STALE",
    "STATUS_SOURCE_UNVERIFIED",
    "STATUS_IDENTITY_MISMATCH",
    "STATUS_FRESHNESS_UNSET",
    "IDENTITY_VERIFIED",
    "IDENTITY_INVALID",
    "IDENTITY_MISMATCH",
    "build_contract",
    "from_gateway_and_msrp",
    "CanonicalPriceGatewayAdapter",
    "dealer_legacy_offer",
    "client_price_inputs",
    "admin_price_view",
]
