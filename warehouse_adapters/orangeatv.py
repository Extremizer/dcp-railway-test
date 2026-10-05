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

        anchors = re.findall(
            r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',
            search_html,
            re.I | re.S,
        )
        candidates: list[str] = []
        for link, anchor_html in anchors:
            raw_link = html.unescape(link)
            anchor_text = re.sub(r"<[^>]+>", " ", anchor_html)
            anchor_text = html.unescape(anchor_text)
            is_legacy_product = "route=product/product" in raw_link
            has_oem_in_link = re.search(
                rf"(?<!\d){re.escape(oem)}(?!\d)",
                raw_link,
                re.I,
            )
            has_oem_in_text = re.search(
                rf"(?<!\d){re.escape(oem)}(?!\d)",
                anchor_text,
                re.I,
            )
            if not (is_legacy_product or has_oem_in_link or has_oem_in_text):
                continue
            if "route=product/search" in raw_link or "/search/" in raw_link:
                continue
            url = urljoin(BASE_URL, raw_link)
            if url not in candidates:
                candidates.append(url)

        if not candidates:
            return WebsiteStockResult(status="not_found", url=search_url)

        exact: list[tuple[str, str]] = []
        for url in candidates[:12]:
            try:
                final_url, page = fetch_text(url)
            except Exception:
                continue

            code_match = re.search(
                r"Код\s+товара\s*:?\s*</?[^>]*>?"
                r"\s*(?:<[^>]+>\s*)*"
                rf"{re.escape(oem)}"
                r"(?:\s*</[^>]+>)*",
                page,
                re.I,
            )
            if not code_match:
                code_match = re.search(
                    rf"Код\s+товара\s*:?.{{0,120}}"
                    rf"(?<!\d){re.escape(oem)}(?!\d)",
                    page,
                    re.I | re.S,
                )

            if code_match:
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

        price_rub = None
        price_match = re.search(
            r'"priceCurrency"\s*:\s*"RUB"[\s\S]{0,300}?'
            r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)"?',
            page,
            re.I,
        )
        if not price_match:
            price_match = re.search(
                r'"price"\s*:\s*"?(\d+(?:[.,]\d+)?)"?'
                r'[\s\S]{0,300}?"priceCurrency"\s*:\s*"RUB"',
                page,
                re.I,
            )
        if price_match:
            price_rub = float(price_match.group(1).replace(",", "."))

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
                price_rub=price_rub,
                url=final_url,
                title=title,
                details={"source": "visible_quantity"},
            )

        plain = re.sub(r"<[^>]+>", " ", page)
        if re.search(r"Нет\s+в\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="out_of_stock",
                quantity=0,
                price_rub=price_rub,
                url=final_url,
                title=title,
            )
        if re.search(r"В\s+наличии", plain, re.I):
            return WebsiteStockResult(
                status="quantity_unknown",
                quantity=None,
                price_rub=price_rub,
                url=final_url,
                title=title,
            )

        return WebsiteStockResult(
            status="quantity_unknown",
            quantity=None,
            price_rub=price_rub,
            url=final_url,
            title=title,
        )
