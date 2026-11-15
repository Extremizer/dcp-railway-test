"""Conservative RUB price parser for PREP-14 only (offline HTML input).

Only identifies one CURRENT product price in a price-labelled element with
explicit RUB/rouble currency. Unverifiable/ambiguous results return UNKNOWN.
A parsed price does NOT mean site or merchant price is live-verified.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
import html
import re

@dataclass(frozen=True)
class RubPriceParse:
    price_rub: int | None
    status: str
    source: str | None = None


def _amount(value: str) -> int | None:
    compact = re.sub(r"[\s\u00a0\u202f]", "", html.unescape(value))
    if not re.fullmatch(r"\d{1,9}(?:[.,]\d{1,2})?", compact):
        return None
    try:
        price = Decimal(compact.replace(",", "."))
    except InvalidOperation:
        return None
    if price <= 0 or price > 100000000:
        return None
    if price != price.to_integral_value():
        return None
    return int(price)


def parse_rub_price(page_html: str) -> RubPriceParse:
    if not isinstance(page_html, str) or len(page_html) > 3000000:
        return RubPriceParse(None, "UNKNOWN")
    candidates: list[int] = []
    # Match an explicitly current-price-marked HTML element; ignore old,
    # crossed-out, struck-through and recommended price labels.
    tags = re.findall(
        r"<(span|div|p|strong|b)\b([^>]*)>([^<]{1,120})</\1\s*>",
        page_html, flags=re.I | re.S,
    )
    for _tag, attrs, raw in tags:
        if not re.search(r"(?:class|itemprop)\s*=\s*['\"][^'\"]*(?:price|стоимост|цена)", attrs, re.I):
            continue
        if re.search(r"(?:old|previous|cross|strike|compare|original|discount-old|старая|было)", attrs, re.I):
            continue
        label = html.unescape(raw).strip()
        if not re.search(r"(?:₽|\bруб(?:\.|лей|ля)?\b|\bр\.|\bRUB\b)", label, re.I):
            continue
        clean = re.sub(r"(?:₽|\bруб(?:\.|лей|ля)?\b|\bр\.|\bRUB\b)", "", label, flags=re.I).strip()
        price = _amount(clean)
        if price is not None:
            candidates.append(price)
    if len(candidates) != 1:
        return RubPriceParse(None, "UNKNOWN")
    return RubPriceParse(candidates[0], "PARSED_UNVERIFIED", "explicit_rub_price_element")


def with_rub_price(stock_result, product_html: str):
    parsed = parse_rub_price(product_html)
    return replace(stock_result, price_rub=parsed.price_rub,
                   price_status=parsed.status, price_source=parsed.source)
