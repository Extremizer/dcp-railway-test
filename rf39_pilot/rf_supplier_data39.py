"""RF39 website-first: direct RF price/stock observations from three existing site adapters.

Published website prices/quantities are valid PRELIMINARY RF evidence (not API
or feed dependent). They are not reservations. Pre-order recheck makes a new
site lookup; this module never writes carts, orders, SQLite or Telegram.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
from urllib.parse import urlsplit

from .warehouse_adapters.registry import get_adapter
from .rf_price_handoff import handoff_website_rub_price
from .rf_production_ports38 import IndependentRFOfferPort

LIVE_HOLD = True
PREFLIGHT_VERIFIED = False
REAL_DB_ATTACHED = False
ORDERS_WRITE_ENABLED = False

ADAPTERS = {
    "orangeatv": "orangeatv",
    "motoservice76": "motoservice76",
    "vladextremelife": "vladextremelife",
}
SITE_HOSTS = {
    "orangeatv": frozenset({"orangeatv.ru", "www.orangeatv.ru"}),
    "motoservice76": frozenset({
        "xn--76-dlclqvoatief.xn--p1ai", "www.xn--76-dlclqvoatief.xn--p1ai"}),
    "vladextremelife": frozenset({
        "vladextremelife.ru", "www.vladextremelife.ru"}),
}
SITE_STATES = frozenset({"in_stock", "out_of_stock", "quantity_unknown"})


class RF39Stop(RuntimeError):
    pass


def _valid_site_url(adapter_type: str, url: str | None) -> bool:
    if not isinstance(url, str):
        return False
    try:
        p = urlsplit(url)
        return (p.scheme == "https"
                and p.hostname in SITE_HOSTS[adapter_type]
                and p.port in (None, 443)
                and not p.username and not p.password)
    except (ValueError, KeyError):
        return False


def _fresh_now(when: str | None) -> bool:
    try:
        t = datetime.fromisoformat(str(when).replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - t).total_seconds()
        return t.tzinfo is not None and -10 <= age <= 300
    except (ValueError, TypeError):
        return False


def _site_quantity(value) -> int | None:
    if type(value) in (float, int) and 0 <= value <= 1_000_000:
        if int(value) == value:
            return int(value)
    return None


def _check_identity(*, adapter_type: str, manufacturer: str, oem: str,
                    warehouse_id: int, supplier_internal_warehouse: str,
                    verified_warehouse_map: Mapping[str, int]) -> None:
    if adapter_type not in ADAPTERS:
        raise RF39Stop("supplier adapter unsupported")
    if (type(warehouse_id) is not int or warehouse_id <= 0
        or not isinstance(verified_warehouse_map, Mapping)
        or type(verified_warehouse_map.get(adapter_type)) is not int
        or verified_warehouse_map[adapter_type] != warehouse_id):
        raise RF39Stop("approved warehouse mapping absent")
    # RF-only OEM may have no authoritative brand. Do not invent one.
    # Supplier adapter + exact OEM + selected warehouse remain mandatory.
    if (not isinstance(manufacturer, str)
        or not all(isinstance(x, str) and x.strip()
                   for x in (oem, supplier_internal_warehouse))):
        raise RF39Stop("OEM and site warehouse are required")


def preview_lookup(*, adapter_type: str, manufacturer: str, oem: str,
                   warehouse_id: int, supplier_internal_warehouse: str,
                   verified_warehouse_map: Mapping[str, int]):
    """One existing HTML lookup; presentation/cart selection only, no order write."""
    _check_identity(adapter_type=adapter_type, manufacturer=manufacturer,
                    oem=oem, warehouse_id=warehouse_id,
                    supplier_internal_warehouse=supplier_internal_warehouse,
                    verified_warehouse_map=verified_warehouse_map)
    adapter = get_adapter(ADAPTERS[adapter_type])
    if adapter is None:
        raise RF39Stop("existing website adapter missing")

    # OrangeATV owner rule: primary green stock and main product price ONLY.
    # Partner stock rows are never a source for Procenka or cart.
    if adapter_type == "orangeatv":
        if supplier_internal_warehouse != "\u041e\u0441\u043d\u043e\u0432\u043d\u043e\u0439 \u0441\u043a\u043b\u0430\u0434":
            raise RF39Stop("OrangeATV partner warehouses forbidden")
        result = adapter.lookup(oem)
    else:
        result = adapter.lookup(oem)

    if result.status in SITE_STATES:
        if not _valid_site_url(adapter_type, result.url):
            raise RF39Stop("product URL is not on the approved supplier site")
        if not _fresh_now(result.observed_at):
            raise RF39Stop("website observation is missing or stale")
    site_quantity = _site_quantity(result.quantity) if result.status in SITE_STATES else None
    preview = handoff_website_rub_price(
        adapter_type=adapter_type, manufacturer=manufacturer, oem=oem,
        warehouse_id=warehouse_id, website_result=result)
    info = result.details if isinstance(result.details, dict) else {}
    # Exact primary block; NEVER trust stock-base_list partner rows.
    scoped = (info.get("source") == "orangeatv_primary_stock"
              if adapter_type == "orangeatv" else True)
    price_ok = (preview.price_status == "PARSED_UNVERIFIED"
                and preview.price_rub is not None
                and _fresh_now(preview.observed_at) and scoped)
    stock_ok = (result.status == "in_stock"
                and site_quantity is not None and site_quantity > 0 and scoped)
    stock_marked_without_qty = (
        adapter_type == "vladextremelife"
        and result.status == "quantity_unknown"
        and info.get("site_availability_confirmed") is True
        and info.get("internal_warehouse_label_verified") is True
        and isinstance(info.get("selected_internal_warehouse"), str)
        and bool(info["selected_internal_warehouse"].strip())
    )
    can_quote = bool(price_ok and (stock_ok or stock_marked_without_qty))
    if result.status in ("ambiguous", "check_failed"):
        state = "SITE_UNCERTAIN"
    elif result.status == "not_found":
        state = "SITE_NOT_FOUND"
    elif can_quote:
        state = "SITE_OFFER_OBSERVED"
    else:
        state = "SITE_PARTIAL_OR_UNAVAILABLE"

    return {
        "adapter": adapter_type, "warehouse_id": warehouse_id,
        "supplier_internal_warehouse": supplier_internal_warehouse,
        "warehouse_identity_source": "approved_mapping",
        "site_warehouse_row_matched": scoped if result.status in SITE_STATES else False,
        "manufacturer": manufacturer, "oem": oem,
        "status": result.status,
        "site_quantity_observed": site_quantity,
        "site_in_stock_without_quantity": stock_marked_without_qty,
        "site_internal_row_selected": (
            info.get("selected_internal_warehouse")
            if stock_marked_without_qty else None
        ),
        "site_product_url": result.url if _valid_site_url(adapter_type, result.url) else None,
        "site_observed_at": result.observed_at,
        "price_rub_preliminary": preview.price_rub if price_ok else None,
        "price_status": preview.price_status if price_ok else "UNKNOWN",
        "price_provenance": preview.price_source if price_ok else None,
        "site_evidence_status": state,
        "can_add_to_cart_preview": can_quote,
        "reservation_guaranteed": False,
        "checkout_allowed": False,
    }


def recheck_before_order(*, previous_offer: Mapping, adapter_type: str,
                         manufacturer: str, oem: str, quantity: int,
                         warehouse_id: int, supplier_internal_warehouse: str,
                         verified_warehouse_map: Mapping[str, int]):
    """Re-fetch website before order confirmation; never submits an order.

    The caller must hold the previous server-side preview, not client-edited
    markup. A changed price or product source requires a new user quotation.
    """
    _check_identity(adapter_type=adapter_type, manufacturer=manufacturer,
                    oem=oem, warehouse_id=warehouse_id,
                    supplier_internal_warehouse=supplier_internal_warehouse,
                    verified_warehouse_map=verified_warehouse_map)
    if type(quantity) is not int or not 1 <= quantity <= 99:
        raise RF39Stop("invalid requested quantity")
    identity = {"adapter": adapter_type, "warehouse_id": warehouse_id,
                "manufacturer": manufacturer, "oem": oem,
                "supplier_internal_warehouse": supplier_internal_warehouse}
    if (not isinstance(previous_offer, Mapping)
        or any(previous_offer.get(k) != v for k, v in identity.items())):
        raise RF39Stop("previous website offer has different identity")
    latest = preview_lookup(
        adapter_type=adapter_type, manufacturer=manufacturer, oem=oem,
        warehouse_id=warehouse_id,
        supplier_internal_warehouse=supplier_internal_warehouse,
        verified_warehouse_map=verified_warehouse_map)
    if (not previous_offer.get("site_product_url")
        or previous_offer.get("site_product_url") != latest["site_product_url"]):
        outcome = "SOURCE_CHANGED"
    elif not latest["can_add_to_cart_preview"]:
        outcome = "PRICE_OR_STOCK_UNAVAILABLE"
    elif previous_offer.get("site_internal_row_selected") != latest.get("site_internal_row_selected"):
        outcome = "SUPPLIER_INTERNAL_ROW_CHANGED"
    elif previous_offer.get("price_rub_preliminary") != latest["price_rub_preliminary"]:
        outcome = "PRICE_CHANGED_REQUOTE"
    elif latest["site_quantity_observed"] is None:
        # Site explicitly says in stock, but publishes no count. Exactly ONE
        # preliminary cart unit may be selected; never claim one unit reserved.
        outcome = ("SITE_RECHECK_MATCHED"
                   if quantity == 1 and latest.get("site_in_stock_without_quantity") is True
                   else "QUANTITY_UNVERIFIED")
    elif latest["site_quantity_observed"] < quantity:
        outcome = "INSUFFICIENT_SITE_STOCK"
    else:
        outcome = "SITE_RECHECK_MATCHED"
    return {
        "recheck_status": outcome,
        "site_recheck_pass": outcome == "SITE_RECHECK_MATCHED",
        "current_offer": latest,
        "requested_quantity": quantity,
        "reservation_guaranteed": False,
        "checkout_allowed": False,
        "orders_written": False,
    }


# Compatibility guard only for the older RF38 durable checkout boundary.
# Website-first preview and recheck above DO NOT call this older port.
def rf38_verified_price_reader(**_kwargs):
    raise RF39Stop("real order write remains on HOLD")


def rf38_verified_stock_reader(**_kwargs):
    raise RF39Stop("real order write remains on HOLD")


def connect_rf38_fail_closed(*, approved_price_sources: frozenset[str],
                            approved_stock_sources: frozenset[str]):
    return IndependentRFOfferPort(
        price_reader=rf38_verified_price_reader,
        stock_reader=rf38_verified_stock_reader,
        approved_price_sources=approved_price_sources,
        approved_stock_sources=approved_stock_sources)
