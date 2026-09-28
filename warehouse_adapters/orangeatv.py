#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html
import re
from urllib.parse import quote, urljoin

from .base import WebsiteStockResult, extract_title, fetch_text

BASE_URL = "https://orangeatv.ru/"
SEARCH_URL = (
    BASE_URL
    + "index.php?route=product/search&search={oem}"
      "&description=true&sub_category=true"
)


class OrangeATVAdapter:
    adapter_type = "orangeatv"

    def lookup(self, oem: str) -> WebsiteStockResult:
        oem = str(oem or "").strip()
        if not oem:
            return WebsiteStockResult(status="not_found")

        search_url = SEARCH_URL.format(oem=quote(oem))
        try:
            _final, search_html = fetch_text(search_url)
        except Exception as exc:
            return WebsiteStockResult(
                status="check_failed",
                details={"error": type(exc).__name__, "message": str(exc)[:300]},
            )

        links = re.findall(
            r'href=["\']([^"\']*route=product/product[^"\']*)',
            search_html,
            re.I,
        )
        candidates: list[str] = []
        for link in links:
            url = urljoin(BASE_URL, html.unescape(link))
            if url not in candidates:
                candidates.append(url)

        if not candidates:
            return WebsiteStockResult(status="not_found", url=search_url)

        exact: list[tuple[str, str]] = []
        for url in candidates[:8]:
            try:
                final_url, page = fetch_text(url)
            except Exception:
                continue
            if re.search(rf"(?<!\d){re.escape(oem)}(?!\d)", page):
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
        match = re.search(
            r"Доступно\s*([0-9]+(?:[.,][0-9]+)?)\s*шт\.?",
            page,
            re.I,
        )
        if match:
            quantity = float(match.group(1).replace(",", "."))
            return WebsiteStockResult(
                status="in_stock" if quantity > 0 else "out_of_stock",
                quantity=quantity,
                url=final_url,
                title=title,
                details={"source": "visible_quantity"},
            )

        plain = re.sub(r"<[^>]+>", " ", page)
        if re.search(r"Нет\s+в\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="out_of_stock",
                quantity=0,
                url=final_url,
                title=title,
            )
        if re.search(r"В\s+наличии", plain, re.I):
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
