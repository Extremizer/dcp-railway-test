#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import time

from bs4 import BeautifulSoup
from bs4.element import Tag

from .base import WebsiteStockResult, extract_title, fetch_text, html_to_text

SITEMAP_URL = "https://vladextremelife.ru/sitemap-iblock-138.xml"


def _available_site_rows(page_html: str) -> list[tuple[int, str]]:
    """Pair each site's in-stock marker with its own RUB price, not a page-wide price."""
    soup = BeautifulSoup(page_html, "html.parser")
    rows: list[tuple[int, str]] = []
    for title in soup.select("div.store_title"):
        label = title.get_text(" ", strip=True)
        # OWNER rule: only Vladextremelife's internal Склад1 and Склад3.
        # A cheaper Склад6, Склад2 or unnumbered "Склад" is never eligible.
        if not re.match(r"^Склад\s*[13](?=\s|$)", label, re.I):
            continue
        available = False
        price = None
        for element in title.next_elements:
            if not isinstance(element, Tag):
                continue
            if element is not title and "store_title" in element.get("class", []):
                break
            if "bxr-instock-wrap" in element.get("class", []):
                available = bool(re.fullmatch(r"В\s+наличии", element.get_text(" ", strip=True), re.I))
            if element.name == "meta" and element.get("itemprop") == "price":
                match = re.fullmatch(
                    r"\s*(\d[\d\s\u00a0\u202f]*)\s*руб\.?\s*",
                    str(element.get("content") or ""), re.I,
                )
                if match:
                    amount = int(re.sub(r"\s+", "", match.group(1)))
                    if 0 < amount <= 100_000_000:
                        price = amount
                break
        if available and price is not None and label:
            rows.append((price, label))
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
            price, internal_label = min(rows, key=lambda row: (row[0], row[1]))
            return WebsiteStockResult(
                status="quantity_unknown",
                quantity=None,
                url=final_url,
                title=title,
                price_rub=price,
                price_status="PARSED_UNVERIFIED",
                price_source="vladextremelife_stock_row_price",
                details={
                    "site_availability_confirmed": True,
                    "internal_warehouse_label_verified": True,
                    "selected_internal_warehouse": internal_label,
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
