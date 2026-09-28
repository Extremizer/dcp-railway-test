#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import re
from urllib.parse import quote, urljoin

from .base import WebsiteStockResult, extract_title, fetch_text, html_to_text

BASE_URL = "https://xn--76-dlclqvoatief.xn--p1ai/"
SEARCH_URL = BASE_URL + "poisk_produktov/?search_text={oem}"


class Motoservice76Adapter:
    adapter_type = "motoservice76"

    def lookup(self, oem: str) -> WebsiteStockResult:
        oem = str(oem or "").strip()
        if not oem:
            return WebsiteStockResult(status="not_found")

        search_url = SEARCH_URL.format(oem=quote(oem))
        try:
            _final, search_html = fetch_text(search_url, timeout=12, retries=2)
        except Exception as exc:
            return WebsiteStockResult(
                status="check_failed",
                details={"error": type(exc).__name__, "message": str(exc)[:300]},
            )

        product_links = re.findall(
            r'href=["\']([^"\']*/products/[0-9]+/?)["\']',
            search_html,
            re.I,
        )
        candidates: list[str] = []
        for link in product_links:
            url = urljoin(BASE_URL, link)
            if url not in candidates:
                candidates.append(url)

        if not candidates:
            return WebsiteStockResult(status="not_found", url=search_url)

        exact: list[tuple[str, str]] = []
        for url in candidates[:12]:
            try:
                final_url, page = fetch_text(url, timeout=15, retries=2)
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
            return WebsiteStockResult(status="not_found", url=search_url)
        if len(exact) > 1:
            return WebsiteStockResult(
                status="ambiguous",
                url=search_url,
                details={"candidates": [url for url, _ in exact]},
            )

        final_url, page = exact[0]
        title = extract_title(page)
        plain = html_to_text(page)

        qty_match = re.search(
            r"Есть\s+в\s+наличии\s*,?\s*([0-9]+(?:[.,][0-9]+)?)\s*шт\.?",
            plain,
            re.I,
        )
        if qty_match:
            quantity = float(qty_match.group(1).replace(",", "."))
            return WebsiteStockResult(
                status="in_stock" if quantity > 0 else "out_of_stock",
                quantity=quantity,
                url=final_url,
                title=title,
                details={"source": "visible_quantity"},
            )

        if re.search(r"Под\s+заказ|Нет\s+в\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="out_of_stock",
                quantity=0,
                url=final_url,
                title=title,
            )

        if re.search(r"Есть\s+в\s+наличии|В\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="quantity_unknown",
                quantity=None,
                url=final_url,
                title=title,
            )

        return WebsiteStockResult(
            status="quantity_unknown",
            quantity=None,
            url=final_url,
            title=title,
        )
