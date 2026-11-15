"""PREP-15: minimal in-memory RUB price handoff from existing website adapters.

WebsiteStockResult -> RFRouter presentation data. Never marks a website
scraped amount as independently confirmed. No DB schema change or writes.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any
import math

LIVE_HOLD = True
PREFLIGHT_VERIFIED = False
TELEGRAM_WRITE_ENABLED = False
ORDERS_WRITE_ENABLED = False
V2_WRITE_ENABLED = False
OUTBOX_WRITE_ENABLED = False

ALLOWED_ADAPTERS = frozenset({"orangeatv", "motoservice76", "vladextremelife"})


class RFPriceHandoffGap(RuntimeError):
    pass


@dataclass(frozen=True)
class RFPriceHandoff:
    adapter_type: str
    manufacturer: str
    oem: str
    warehouse_id: int
    price_rub: int | None
    price_status: str
    price_source: str | None
    product_url: str | None
    observed_at: str | None
    checkout_eligible: bool = False

    def for_router(self) -> dict[str, Any]:
        """Presentation-only payload; NEVER qualifies as verified RF price."""
        return asdict(self)


def handoff_website_rub_price(
    *,
    adapter_type: str,
    manufacturer: str,
    oem: str,
    warehouse_id: int,
    website_result: Any,
) -> RFPriceHandoff:
    """Convert an existing WebsiteStockResult without a DB or network call.

    The calling warehouse service must supply warehouse/identity from a
    separately verified source. This function performs no site lookup.
    """
    if (adapter_type not in ALLOWED_ADAPTERS
            # Manufacturer may be unverified for RF-only OEM; preserve "".
            or not isinstance(manufacturer, str)
            or not isinstance(oem, str) or not oem.strip()
            or type(warehouse_id) is not int or warehouse_id <= 0):
        raise RFPriceHandoffGap("source identity missing or invalid")
    status = getattr(website_result, "price_status", "UNKNOWN")
    value = getattr(website_result, "price_rub", None)
    source = getattr(website_result, "price_source", None)
    # Site-published RUB amounts are usable in preliminary RF quotes.
    # An exact OrangeATV stock-row amount is scoped to that site warehouse;
    # neither extraction is a reservation or permission to place an order.
    if status != "PARSED_UNVERIFIED":
        value, status, source = None, "UNKNOWN", None
    elif (type(value) is not int or value <= 0 or value > 100_000_000
          or source not in {"explicit_rub_price_element",
                            "orangeatv_stock_row_price",
                            "vladextremelife_stock_row_price"}):
        value, status, source = None, "UNKNOWN", None
    observed = getattr(website_result, "observed_at", None)
    if observed is not None:
        try:
            dt = datetime.fromisoformat(str(observed).replace("Z", "+00:00"))
            if dt.tzinfo is None:
                observed = None
        except ValueError:
            observed = None
    return RFPriceHandoff(
        adapter_type=adapter_type,
        manufacturer=manufacturer.strip(),
        oem=oem.strip(),
        warehouse_id=warehouse_id,
        price_rub=value,
        price_status=status,
        price_source=source,
        product_url=getattr(website_result, "url", None),
        observed_at=observed,
        checkout_eligible=False,
    )


def verified_price_for_checkout(*_args, **_kwargs):
    """No unverified scraped price may enter order confirmation."""
    raise RFPriceHandoffGap(
        "STOP: RUB price is not independently verified; "
        "stock, reserves and checkout remain on HOLD"
    )
