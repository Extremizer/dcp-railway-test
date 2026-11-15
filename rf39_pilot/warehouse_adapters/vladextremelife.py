#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup
from bs4.element import Tag

from .base import WebsiteStockResult, extract_title, fetch_text, html_to_text

SITEMAP_URL = "https://vladextremelife.ru/sitemap-iblock-138.xml"


# The exact labels and site-level data-store IDs are tied to Krasnoyarsk,
# not similarly named Moscow / St Petersburg / remote "SkladV" branches.
_KRASNOYARSK_STORES = {
    "\u0421\u043a\u043b\u0430\u04341 \u041a\u0440\u0430\u0441\u043d\u043e\u044f\u0440\u0441\u043a \u0412\u044b\u0441\u043e\u0442\u043d\u0430\u044f": "2",
    "\u0421\u043a\u043b\u0430\u04343 \u041a\u0440\u0430\u0441\u043d\u043e\u044f\u0440\u0441\u043a \u0423\u0434\u0430\u043b\u0435\u043d\u043d\u044b\u0439": "1",
}


def _available_site_rows(page_html: str) -> list[tuple[int, int, str, str]]:
    """Read current price and frontend data-max from SAME exact Krasnoyarsk row.

    Never click plus / cart. A missing, ambiguous or changed row is not a
    confirmed stock quantity. Product OEM itself is matched in lookup().
    """
    soup = BeautifulSoup(page_html, "html.parser")
    rows: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()
    for title in soup.select("div.store_title"):
        label = " ".join(title.get_text(" ", strip=True).split())
        normalized = label.replace("\u0451", "\u0435").replace("\u0401", "\u0415")
        store_id = _KRASNOYARSK_STORES.get(normalized)
        if store_id is None:
            continue
        if normalized in seen:
            return []  # Same site warehouse twice: no authoritative selection.
        seen.add(normalized)

        # Only elements between THIS title and the NEXT store title are eligible.
        siblings: list[Tag] = []
        for node in title.next_siblings:
            if not isinstance(node, Tag):
                continue
            if "store_title" in node.get("class", []):
                break
            siblings.append(node)

        def within(selector: str) -> list[Tag]:
            return [el for root in siblings for el in root.select(selector)]

        available = within(".bxr-instock-wrap")
        prices = within("meta[itemprop='price']")
        plus = within("input.bxr-quantity-button-plus")
        fields = within("input.bxr-quantity-text[name='quantity']")
        buttons = within("button.product_basket_add")
        item_fields = within("input.bxr-basket-item-id[name='item']")
        if not all(len(items) == 1 for items in (
            available, prices, plus, fields, buttons, item_fields
        )):
            continue
        if not re.fullmatch(
            r"\u0412\s+\u043d\u0430\u043b\u0438\u0447\u0438\u0438",
            available[0].get_text(" ", strip=True), re.I
        ):
            continue

        price_raw = str(prices[0].get("content") or "")
        price_match = re.fullmatch(
            r"\s*(\d[\d\s\u00a0\u202f]*)\s*\u0440\u0443\u0431\.?\s*",
            price_raw, re.I
        )
        maximum_raw = str(plus[0].get("data-max") or "")
        item_id = str(plus[0].get("data-item") or "")
        if not price_match or not re.fullmatch(r"[1-9]\d{0,5}", maximum_raw):
            continue
        price = int(re.sub(r"\s+", "", price_match.group(1)))
        maximum = int(maximum_raw)
        if not (0 < price <= 100_000_000 and 0 < maximum <= 1_000_000):
            continue
        if not re.fullmatch(r"[1-9]\d*", item_id):
            continue

        button = buttons[0]
        if (
            plus[0].get("data-store") != store_id
            or fields[0].get("data-store") != store_id
            or button.get("data-store") != store_id
            or plus[0].get("data-item") != item_id
            or fields[0].get("data-item") != item_id
            or button.get("data-item") != item_id
            or item_fields[0].get("value") != item_id
            or plus[0].get("data-ratio") != "1"
            or button.get("data-ratio") != "1"
            or button.get("data-currency") != "RUB"
            or button.get("data-value") != str(price)
        ):
            continue
        rows.append((price, maximum, label, store_id))
    return rows


class VladExtremeLifeAdapter:
    adapter_type = "vladextremelife"

    def __init__(self):
        self._sitemap_text: str | None = None
        self._sitemap_loaded_at = 0.0

    def _sitemap(self) -> str:
        if (
            self._sitemap_text is None
            or time.time() - self._sitemap_loaded_at > 3600
        ):
            _url, text = fetch_text(SITEMAP_URL, timeout=45, retries=2)
            self._sitemap_text = text
            self._sitemap_loaded_at = time.time()
        return self._sitemap_text

    def _candidate_urls(self, oem: str) -> list[str]:
        sitemap = self._sitemap()
        urls = re.findall(r"<loc>(.*?)</loc>", sitemap, re.I | re.S)
        exact_token = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(oem)}(?![A-Za-z0-9])",
            re.I,
        )
        return [url.strip() for url in urls if exact_token.search(url)][:30]

    def lookup(self, oem: str) -> WebsiteStockResult:
        oem = str(oem or "").strip()
        if not oem:
            return WebsiteStockResult(status="not_found")

        try:
            candidates = self._candidate_urls(oem)
        except Exception as exc:
            return WebsiteStockResult(
                status="check_failed",
                details={"error": type(exc).__name__, "message": str(exc)[:300]},
            )

        if not candidates:
            return WebsiteStockResult(status="not_found")

        exact: list[tuple[str, str]] = []
        for url in candidates:
            try:
                final_url, page = fetch_text(url, timeout=30, retries=2)
            except Exception:
                continue

            plain = html_to_text(page)
            if re.search(
                rf"Артикул:\s*{re.escape(oem)}(?:\s|$)",
                plain,
                re.I,
            ):
                exact.append((final_url, page))

        if not exact:
            return WebsiteStockResult(
                status="not_found",
                details={"candidate_count": len(candidates)},
            )
        if len(exact) > 1:
            return WebsiteStockResult(
                status="ambiguous",
                details={"candidates": [url for url, _ in exact]},
            )

        final_url, page = exact[0]
        title = extract_title(page)

        rows = _available_site_rows(page)
        if rows:
            # Vendor's internal stock rows are NOT EXTREMIZER warehouses.
            # Select one priced, explicitly in-stock row; do not add quantities.
            price, quantity, internal_label, store_id = min(
                rows, key=lambda row: (row[0], row[2]))
            return WebsiteStockResult(
                status="in_stock",
                quantity=float(quantity),
                url=final_url,
                title=title,
                price_rub=price,
                price_status="PARSED_UNVERIFIED",
                price_source="vladextremelife_stock_row_price",
                details={
                    "site_availability_confirmed": True,
                    "internal_warehouse_label_verified": True,
                    "selected_internal_warehouse": internal_label,
                    "selected_site_data_store": store_id,
                    "quantity_source": "same_row_plus_data_max",
                    "available_price_rows": len(rows),
                },
            )

        # No in-stock priced offer from internal Склад1 or Склад3.
        # Do not fall back to aggregate stock or prices from forbidden rows.
        return WebsiteStockResult(
            status="quantity_unknown",
            quantity=None,
            url=final_url,
            title=title,
            details={"allowed_internal_warehouses": [1, 3], "eligible_offers": 0},
        )
