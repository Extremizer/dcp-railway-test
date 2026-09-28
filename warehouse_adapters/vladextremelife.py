#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
import time

from .base import WebsiteStockResult, extract_title, fetch_text, html_to_text

SITEMAP_URL = "https://vladextremelife.ru/sitemap-iblock-138.xml"


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

        qty_match = re.search(
            r"CATALOG_QUANTITY\s*:\s*['\"]?([0-9]+(?:[.,][0-9]+)?)",
            page,
            re.I,
        )
        if not qty_match:
            qty_match = re.search(
                r"QTY_MAX\s*:\s*['\"]?([0-9]+(?:[.,][0-9]+)?)",
                page,
                re.I,
            )

        if qty_match:
            quantity = float(qty_match.group(1).replace(",", "."))
            store_max = [
                float(value.replace(",", "."))
                for value in re.findall(
                    r'data-max=["\']([0-9]+(?:[.,][0-9]+)?)["\']',
                    page,
                    re.I,
                )
            ]
            return WebsiteStockResult(
                status="in_stock" if quantity > 0 else "out_of_stock",
                quantity=quantity,
                url=final_url,
                title=title,
                details={
                    "source": "catalog_quantity",
                    "store_max_values": store_max,
                },
            )

        plain = html_to_text(page)
        if re.search(r"В\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="quantity_unknown",
                quantity=None,
                url=final_url,
                title=title,
            )
        if re.search(r"Нет\s+в\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="out_of_stock",
                quantity=0,
                url=final_url,
                title=title,
            )

        return WebsiteStockResult(
            status="quantity_unknown",
            quantity=None,
            url=final_url,
            title=title,
        )
