"""Strict evidence validator for selected-brand DCP SEARCH, isolated and offline.

No browser, DB, network, Telegram or DCP access. Does not authenticate the
producer or independently establish that the dealer price field is approved.
Only an authenticated supplier row with an EXPLICIT Dealer Price label may
become a candidate; server-side signed-v2 provenance remains a separate gate.
Polaris always uses the dealer CART contract instead.
"""
from datetime import datetime, timezone
from urllib.parse import urlsplit, parse_qs
import math
import re

import dealercostparts_manufacturer_finder_v6_6 as finder

HOST = "www.dealercostparts.com"
PRICE_SELECTOR = ".c2 .dbl"


def selected_pair(manufacturer, oem):
    try:
        brand = finder.manufacturer_alias(str(manufacturer or "").strip())
        part = finder.normalize_oem(str(oem or "")).upper()
    except (ValueError, TypeError):
        raise ValueError("manufacturer_and_oem_required") from None
    if not brand or brand == "Polaris" or str(manufacturer or "").strip().upper() in (
        "AUTO", "__AUTO__", "UNKNOWN"
    ):
        raise ValueError("non_polaris_selected_manufacturer_required")
    catalog = next((c for c in finder.CATALOGS
                    if c.kind == "parts" and c.manufacturer == brand), None)
    if catalog is None or not re.fullmatch(r"[A-Z0-9-]{4,40}", part):
        raise ValueError("exact_parts_catalog_required")
    return brand, part, catalog.slug


def verify_observation(manufacturer, oem, observation):
    """Fail closed on missing account, exact SKU, dealer label or fresh proof."""
    try:
        brand, exact, slug = selected_pair(manufacturer, oem)
    except ValueError:
        return {"status": "UNKNOWN_MANUFACTURER", "dealer_price_usd": None}
    def unavailable(status):
        return {"status": status, "manufacturer": brand, "query_oem": exact,
                "dealer_price_usd": None}
    if not isinstance(observation, dict):
        return unavailable("TECHNICAL_ERROR")
    if observation.get("challenged") is True:
        return unavailable("CLOUDFLARE")
    if observation.get("authenticated") is not True:
        return unavailable("AUTH_REQUIRED")
    if type(observation.get("http_status")) is not int or observation["http_status"] != 200:
        return unavailable("TECHNICAL_ERROR")
    url = urlsplit(str(observation.get("document_url") or ""))
    if (url.scheme != "https" or url.netloc != HOST
        or url.path != "/oemparts/partsearch/" + slug
        or parse_qs(url.query) != {"partsearch": [exact]}
        or url.fragment):
        return unavailable("TECHNICAL_ERROR")
    if observation.get("found") is not True:
        return unavailable("NOT_FOUND")
    if (observation.get("manufacturer") != brand
        or observation.get("query_oem") != exact
        or observation.get("matched_sku") != exact
        or observation.get("current_oem") != exact
        or type(observation.get("row_count")) is not int
        or observation["row_count"] != 1):
        return unavailable("TECHNICAL_ERROR")
    price = observation.get("dealer_price_usd")
    label = " ".join(str(observation.get("price_label") or "").lower().split())
    if (label != "dealer price"
        or observation.get("price_field_selector") != PRICE_SELECTOR
        or observation.get("currency") != "USD"
        or type(price) not in (int, float) or not math.isfinite(price) or price <= 0):
        return unavailable("DEALER_PRICE_UNVERIFIED")
    try:
        stamp = datetime.fromisoformat(str(observation.get("verified_at") or "").replace("Z", "+00:00"))
        if stamp.tzinfo is None or not -60 <= (datetime.now(timezone.utc) - stamp).total_seconds() <= 300:
            return unavailable("TECHNICAL_ERROR")
    except (TypeError, ValueError, OverflowError):
        return unavailable("TECHNICAL_ERROR")
    return {"status": "FOUND", "manufacturer": brand, "query_oem": exact,
            "matched_sku": exact, "current_oem": exact,
            "dealer_price_usd": float(price), "currency": "USD",
            "source": f"server_html:#add_{exact} .c2 .dbl",
            "name": str(observation.get("name") or "")[:300] or None}
