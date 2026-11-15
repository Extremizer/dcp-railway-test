#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Existing OrangeATV HTML adapter; RF39 adds optional exact site warehouse selection."""
from __future__ import annotations

import html
import re
from urllib.parse import quote, urljoin

from .rub_price import with_rub_price
from .base import WebsiteStockResult, extract_title, fetch_text, html_to_text

BASE_URL = "https://orangeatv.ru/"
SEARCH_URL = (
    BASE_URL
    + "index.php?route=product/search&search={oem}"
      "&description=true&sub_category=true"
)

def _exact_product_oem(page: str, oem: str) -> bool:
    # A substring elsewhere on a page (recommendations, navigation) is not proof.
    sku = r'["\']sku["\']\s*:\s*["\']' + re.escape(oem) + r'["\']'
    if re.search(sku, page, re.I):
        return True
    code_label = "\u041a\u043e\u0434 \u0442\u043e\u0432\u0430\u0440\u0430"
    if re.search(
        r"""<b>\s*""" + re.escape(code_label)
        + r"""\s*:\s*</b>\s*<span[^>]*>\s*""" + re.escape(oem)
        + r"""\s*</span>""", page, re.I
    ):
        return True
    plain = html_to_text(page)
    return bool(re.search(
        r"(?:Артикул|Модель|OEM)\s*[:№]\s*" + re.escape(oem)
        + r"(?![A-Za-z0-9._/-])", plain, re.I))


def _selected_stock_row(page: str, warehouse: str):
    """Read only an EXACT named OrangeATV stock-base_list row, not total quantity."""
    starts = list(re.finditer(
        r'<div\b[^>]*class\s*=\s*["\'][^"\']*'
        r'(?<![A-Za-z0-9_-])stock-base_list(?![A-Za-z0-9_-])'
        r'[^"\']*["\'][^>]*>', page, re.I))
    found = []
    for i, item in enumerate(starts):
        end = min(starts[i + 1].start() if i + 1 < len(starts)
                  else len(page), item.end() + 2500)
        block = page[item.end():end]
        fields = {}
        for kind in ("name", "stock", "price"):
            m = re.search(
                r'<div\b[^>]*class\s*=\s*["\'][^"\']*stock-base_list--'
                + kind + r'[^"\']*["\'][^>]*>([\s\S]{0,200}?)</div>',
                block, re.I)
            if m:
                fields[kind] = html_to_text(m.group(1))
        if fields.get("name", "").strip().casefold() == warehouse.strip().casefold():
            found.append(fields)
    if len(found) != 1:
        return None
    item = found[0]
    quantity_match = re.fullmatch(r"\s*(\d+)\s*шт\.?\s*", item.get("stock", ""), re.I)
    raw_price = item.get("price", "")
    price_match = re.fullmatch(
        r"\s*([0-9][0-9\s\u00a0\u202f]{0,15})\s*(?:₽|р\.|руб\.?|RUB)\s*",
        raw_price, re.I)
    if quantity_match is None or price_match is None:
        return None
    price_digits = re.sub(r"\s", "", price_match.group(1))
    if not price_digits.isdigit():
        return None
    qty, price = int(quantity_match.group(1)), int(price_digits)
    if not 0 <= qty <= 1_000_000 or not 0 < price <= 100_000_000:
        return None
    return qty, price


def _primary_orange_price(primary: str) -> int | None:
    """Only one main product priceBig span; partner price rows excluded."""
    candidates = re.findall(
        r"""<div\b[^>]*class\s*=\s*["'][^"']*\bpriceBig\b[^"']*["'][^>]*>\s*<span\b[^>]*>\s*([0-9][0-9\s\u00a0\u202f]{0,15})\s*(?:\u0440\.|\u20bd|\u0440\u0443\u0431\.)\s*</span>""",
        primary, re.I,
    )
    if len(candidates) != 1:
        return None
    digits = re.sub(r"\s+", "", candidates[0])
    if not digits.isdigit():
        return None
    price = int(digits)
    return price if 0 < price <= 100_000_000 else None


class OrangeATVAdapter:
    adapter_type = "orangeatv"

    def lookup(self, oem: str, supplier_internal_warehouse: str | None = None) -> WebsiteStockResult:
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
            search_html, re.I)
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
            if _exact_product_oem(page, oem):
                exact.append((final_url, page))

        if not exact:
            return WebsiteStockResult(status="not_found", url=search_url)
        if len(exact) > 1:
            return WebsiteStockResult(
                status="ambiguous", url=search_url,
                details={"candidates": [url for url, _ in exact]})

        final_url, page = exact[0]
        title = extract_title(page)
        if supplier_internal_warehouse is not None:
            row = _selected_stock_row(page, supplier_internal_warehouse)
            if row is None:
                return WebsiteStockResult(
                    status="quantity_unknown", url=final_url, title=title,
                    details={"source": "stock_row_unavailable",
                             "oem_match": "product_sku",
                             "internal_warehouse_verified": False})
            qty, rub = row
            return WebsiteStockResult(
                status="in_stock" if qty > 0 else "out_of_stock",
                quantity=float(qty), url=final_url, title=title,
                details={"source": "site_stock_row",
                         "oem_match": "product_sku",
                         "internal_warehouse_verified": True,
                         "supplier_internal_warehouse": supplier_internal_warehouse},
                price_rub=rub, price_status="PARSED_UNVERIFIED",
                price_source="orangeatv_stock_row_price")

        # Primary green availability, not "stock-base_list" partner rows.
        # Limit extraction to the main product area above the partner section.
        section = re.search(r"\u041d\u0430\u043b\u0438\u0447\u0438\u0435\s+\u043d\u0430\s+\u0441\u043a\u043b\u0430\u0434\u0430\u0445", page, re.I)
        primary = page[:section.start()] if section else page
        # If the section cannot be separated safely, deny instead of reading
        # a partner price or stock from the same HTML document.
        if re.search(r'class\s*=\s*["\x27][^"\x27]*\bstock-base_list\b', primary, re.I):
            return WebsiteStockResult(status="quantity_unknown", url=final_url, title=title)
        # Only the green PRIMARY stock box counts, not stock-base partner rows.
        green = re.search(
            r"""<div\b[^>]*class\s*=\s*["'][^"']*\balert-success-alt\b[^"']*["'][^>]*>([\s\S]{0,1200}?)</div>""",
            primary, re.I,
        )
        green_text = html_to_text(green.group(1)) if green else ""
        main_stock = re.search(r"\u0414\u043e\u0441\u0442\u0443\u043f\u043d\u043e\s*(\d+)\s*\u0448\u0442\.?", green_text, re.I)
        is_green_main = bool(re.search(r"\u0415\u0441\u0442\u044c\s+\u0432\s+\u043d\u0430\u043b\u0438\u0447\u0438\u0438", green_text, re.I))
        if is_green_main and main_stock:
            quantity = int(main_stock.group(1))
            if not 0 <= quantity <= 1000000:
                return WebsiteStockResult(status="quantity_unknown", url=final_url, title=title)
            main_price = _primary_orange_price(primary)
            return WebsiteStockResult(
                status="in_stock" if quantity > 0 else "out_of_stock",
                quantity=float(quantity), url=final_url, title=title,
                details={"source": "orangeatv_primary_stock",
                         "oem_match": "product_sku_or_product_code",
                         "partner_stock_excluded": True},
                price_rub=main_price,
                price_status="PARSED_UNVERIFIED" if main_price is not None else "UNKNOWN",
                price_source="explicit_rub_price_element" if main_price is not None else None)

        # Missing primary green count cannot be inferred from partner stock.
        return WebsiteStockResult(status="quantity_unknown", url=final_url, title=title)
