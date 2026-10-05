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

    @staticmethod
    def _exact_article(page: str, oem: str) -> bool:
        plain = html_to_text(page)
        return bool(
            re.search(
                rf"Артикул:\s*{re.escape(oem)}(?:\s|$)",
                plain,
                re.I,
            )
        )

    @staticmethod
    def _parse_price_rub(page: str) -> float | None:
        """Extract the product's own RUB price from an exact product page.

        Prefer structured/schema markup, then price-labelled HTML blocks.
        Do not scrape arbitrary numbers from the whole page: recommendations,
        cart totals and article numbers may also be present.
        """
        patterns = (
            # <meta itemprop="price" content="12345.00"> and attribute-order variants
            r'itemprop=["\']price["\'][^>]*(?:content|value)=["\']\s*([0-9][0-9\s]*(?:[.,][0-9]{1,2})?)',
            r'(?:content|value)=["\']\s*([0-9][0-9\s]*(?:[.,][0-9]{1,2})?)["\'][^>]*itemprop=["\']price["\']',
            # Common product-price classes/ids on YaSite-style pages.
            r'<[^>]+(?:class|id)=["\'][^"\']*(?:product[-_ ]?price|price[-_ ]?(?:value|current|new)|(?:^|[-_ ])price(?:[-_ ]|$))[^"\']*["\'][^>]*>[\s\S]{0,240}?([0-9][0-9\s]*(?:[.,][0-9]{1,2})?)\s*(?:₽|р\.?|руб\.?)',
        )
        for pattern in patterns:
            match = re.search(pattern, page, re.I)
            if not match:
                continue
            raw = re.sub(r"[\s\xa0]", "", match.group(1)).replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                continue
            if 0 < value < 10_000_000:
                return value

        # Last safe fallback: explicit "Цена: N руб" text on the exact product page.
        plain = html_to_text(page)
        match = re.search(
            r"(?:Цена|Стоимость)\s*:?\s*([0-9][0-9\s\xa0]*(?:[.,][0-9]{1,2})?)\s*(?:₽|р\.?|руб\.?)",
            plain,
            re.I,
        )
        if match:
            raw = re.sub(r"[\s\xa0]", "", match.group(1)).replace(",", ".")
            try:
                value = float(raw)
            except ValueError:
                value = 0.0
            if 0 < value < 10_000_000:
                return value
        return None

    @staticmethod
    def _parse_exact_page(final_url: str, page: str) -> WebsiteStockResult:
        title = extract_title(page)
        price_rub = Motoservice76Adapter._parse_price_rub(page)

        # Quantity belongs to the product's own presence-info block.
        presence_match = re.search(
            r'<p\b[^>]*class=["\'][^"\']*presence-info[^"\']*["\'][^>]*>'
            r'([\s\S]*?)</p>',
            page,
            re.I,
        )
        presence_text = (
            html_to_text(presence_match.group(1))
            if presence_match
            else ""
        )

        qty_match = re.search(
            r"Есть\s+в\s+наличии\s*,?\s*"
            r"([0-9]+(?:[.,][0-9]+)?)\s*шт\.?",
            presence_text,
            re.I,
        )
        # Compatibility/safety fallback: an explicit numeric stock phrase
        # anywhere on the exact product page is strong enough evidence.
        # We intentionally do NOT use global "Под заказ"/"Нет в наличии"
        # text for zero because those phrases can appear in navigation.
        if not qty_match:
            qty_match = re.search(
                r"Есть\s+в\s+наличии\s*,?\s*"
                r"([0-9]+(?:[.,][0-9]+)?)\s*шт\.?",
                html_to_text(page),
                re.I,
            )
        if qty_match:
            quantity = float(qty_match.group(1).replace(",", "."))
            return WebsiteStockResult(
                status="in_stock" if quantity > 0 else "out_of_stock",
                quantity=quantity,
                price_rub=price_rub,
                url=final_url,
                title=title,
                details={
                    "source": "visible_quantity",
                    "availability": "InStock" if quantity > 0 else "OutOfStock",
                },
            )

        # Prefer the structured Offer availability over global page text.
        availability_match = re.search(
            r'itemprop=["\']availability["\'][^>]*'
            r'href=["\'][^"\']*/(InStock|OutOfStock)["\']',
            page,
            re.I,
        )
        availability = availability_match.group(1) if availability_match else None

        if availability and availability.casefold() == "outofstock":
            return WebsiteStockResult(
                status="out_of_stock",
                quantity=0,
                price_rub=price_rub,
                url=final_url,
                title=title,
                details={
                    "source": "schema_availability",
                    "availability": "OutOfStock",
                    "presence_text": presence_text or None,
                },
            )

        if availability and availability.casefold() == "instock":
            return WebsiteStockResult(
                status="quantity_unknown",
                quantity=None,
                price_rub=price_rub,
                url=final_url,
                title=title,
                details={
                    "source": "schema_availability",
                    "availability": "InStock",
                    "presence_text": presence_text or None,
                },
            )

        # Product-specific visible fallback only. Do not use generic
        # navigation text such as "Запчасти под заказ".
        if presence_text:
            if re.search(r"Нет\s+в\s+наличии", presence_text, re.I):
                return WebsiteStockResult(
                    status="out_of_stock",
                    quantity=0,
                    price_rub=price_rub,
                    url=final_url,
                    title=title,
                    details={
                        "source": "presence_text",
                        "presence_text": presence_text,
                    },
                )
            if re.search(r"Есть\s+в\s+наличии|В\s+наличии", presence_text, re.I):
                return WebsiteStockResult(
                    status="quantity_unknown",
                    quantity=None,
                    price_rub=price_rub,
                    url=final_url,
                    title=title,
                    details={
                        "source": "presence_text",
                        "presence_text": presence_text,
                    },
                )

        return WebsiteStockResult(
            status="quantity_unknown",
            quantity=None,
            price_rub=price_rub,
            url=final_url,
            title=title,
            details={
                "source": "product_page_no_quantity",
                "presence_text": presence_text or None,
            },
        )

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
            return WebsiteStockResult(
                status="not_found",
                url=search_url,
                details={"candidate_count": 0},
            )

        exact: list[tuple[str, str]] = []
        page_errors: list[dict] = []
        checked = 0
        for url in candidates[:12]:
            checked += 1
            try:
                final_url, page = fetch_text(url, timeout=15, retries=2)
            except Exception as exc:
                page_errors.append(
                    {
                        "url": url,
                        "error": type(exc).__name__,
                        "message": str(exc)[:200],
                    }
                )
                continue
            if self._exact_article(page, oem):
                exact.append((final_url, page))

        if not exact:
            if checked and len(page_errors) == checked:
                return WebsiteStockResult(
                    status="check_failed",
                    url=search_url,
                    details={
                        "candidate_count": len(candidates),
                        "page_errors": page_errors[:8],
                    },
                )
            return WebsiteStockResult(
                status="not_found",
                url=search_url,
                details={
                    "candidate_count": len(candidates),
                    "page_errors": page_errors[:8],
                },
            )

        if len(exact) > 1:
            return WebsiteStockResult(
                status="ambiguous",
                url=search_url,
                details={
                    "candidates": [url for url, _ in exact],
                    "candidate_count": len(candidates),
                },
            )

        final_url, page = exact[0]
        result = self._parse_exact_page(final_url, page)
        result.details["candidate_count"] = len(candidates)
        if page_errors:
            result.details["page_errors"] = page_errors[:8]
        return result
