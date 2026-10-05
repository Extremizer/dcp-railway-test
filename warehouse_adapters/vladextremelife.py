#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import json
import re
import time
from urllib.parse import urlencode, urljoin

from .base import WebsiteStockResult, extract_title, fetch_text, html_to_text

SITEMAP_URL = "https://vladextremelife.ru/sitemap-iblock-138.xml"
BASE_URL = "https://vladextremelife.ru"
SEARCHBOOSTER_API = (
    "https://api.searchbooster.net/api/v2/"
    "941bbeef-3399-4ece-ae07-78d70f9c03cf/completions"
)


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

    def _sitemap_candidate_urls(self, oem: str) -> list[str]:
        sitemap = self._sitemap()
        urls = re.findall(r"<loc>(.*?)</loc>", sitemap, re.I | re.S)
        exact_token = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(oem)}(?![A-Za-z0-9])",
            re.I,
        )
        return [url.strip() for url in urls if exact_token.search(url)][:30]

    # Backwards-compatible alias used by existing diagnostics/tests.
    def _candidate_urls(self, oem: str) -> list[str]:
        return self._sitemap_candidate_urls(oem)

    def _search_api_exact(self, oem: str) -> list[dict]:
        query = urlencode(
            {
                "query": oem,
                "locale": "ru",
                "client": "vladextremelife.ru",
            }
        )
        _url, payload = fetch_text(
            f"{SEARCHBOOSTER_API}?{query}",
            timeout=20,
            retries=2,
        )
        data = json.loads(payload)
        offers = data.get("offers") or []
        wanted = oem.casefold()
        return [
            offer
            for offer in offers
            if str(offer.get("offerCode") or "").strip().casefold() == wanted
        ][:12]

    def _search_candidate_urls(self, oem: str) -> list[str]:
        exact_urls: list[str] = []
        for offer in self._search_api_exact(oem):
            href = str(offer.get("url") or "").strip()
            if not href:
                continue
            full_url = urljoin(BASE_URL + "/", href)
            if full_url not in exact_urls:
                exact_urls.append(full_url)
        return exact_urls[:12]

    @staticmethod
    def _page_has_exact_oem(page: str, oem: str) -> bool:
        plain = html_to_text(page)
        return bool(
            re.search(
                rf"Артикул:\s*{re.escape(oem)}(?:\s|$)",
                plain,
                re.I,
            )
        )

    def _open_exact_candidates(
        self,
        candidates: list[str],
        oem: str,
    ) -> tuple[list[tuple[str, str]], list[dict]]:
        exact: list[tuple[str, str]] = []
        errors: list[dict] = []

        for url in candidates:
            try:
                final_url, page = fetch_text(url, timeout=30, retries=2)
            except Exception as exc:
                errors.append(
                    {
                        "url": url,
                        "error": type(exc).__name__,
                        "message": str(exc)[:200],
                    }
                )
                continue

            if self._page_has_exact_oem(page, oem):
                exact.append((final_url, page))

        return exact, errors

    @staticmethod
    def _parse_exact_page(
        final_url: str,
        page: str,
        *,
        lookup_source: str,
    ) -> WebsiteStockResult:
        title = extract_title(page)

        # IMPORTANT: Vlad Extreme Life product pages show stock for many
        # physical warehouses. For public "склад КРС" we are allowed to use
        # ONLY these two Krasnoyarsk locations and must ignore every other
        # warehouse, even when CATALOG_QUANTITY contains their network total.
        target_prefixes = (
            "склад1 красноярск высотная",
            "склад3 красноярск удаленный",
        )

        store_matches = list(
            re.finditer(
                r'store_title[^>]*>([^<]+)</div>',
                page,
                re.I,
            )
        )

        local_rows: list[dict] = []
        unknown_local = False

        for index, match in enumerate(store_matches):
            store_name = html_to_text(match.group(1)).strip()
            normalized_name = re.sub(r"\s+", " ", store_name).strip().casefold()
            if not any(normalized_name.startswith(prefix) for prefix in target_prefixes):
                continue

            block_end = (
                store_matches[index + 1].start()
                if index + 1 < len(store_matches)
                else min(len(page), match.end() + 5000)
            )
            block = page[match.end():block_end]

            store_id_match = re.search(
                r'data-store=["\']([0-9]+)["\']',
                block,
                re.I,
            )
            qty_match = re.search(
                r'data-max=["\']([0-9]+(?:[.,][0-9]+)?)["\']',
                block,
                re.I,
            )
            price_match = re.search(
                r'itemprop=["\']price["\'][^>]*content=["\']'
                r'([0-9][0-9\s]*(?:[.,][0-9]+)?)\s*руб',
                block,
                re.I,
            )
            local_price = (
                float(price_match.group(1).replace(" ", "").replace(",", "."))
                if price_match
                else None
            )

            if qty_match:
                quantity = float(qty_match.group(1).replace(",", "."))
                local_rows.append(
                    {
                        "name": store_name,
                        "store_id": (
                            store_id_match.group(1)
                            if store_id_match
                            else None
                        ),
                        "quantity": quantity,
                        "price_rub": local_price,
                    }
                )
            else:
                block_plain = html_to_text(block)
                if re.search(r"В\s+наличии", block_plain, re.I):
                    unknown_local = True
                    local_rows.append(
                        {
                            "name": store_name,
                            "store_id": (
                                store_id_match.group(1)
                                if store_id_match
                                else None
                            ),
                            "quantity": None,
                            "price_rub": local_price,
                        }
                    )
                else:
                    # A target warehouse block exists but carries no positive
                    # availability/quantity marker: treat that location as 0.
                    local_rows.append(
                        {
                            "name": store_name,
                            "store_id": (
                                store_id_match.group(1)
                                if store_id_match
                                else None
                            ),
                            "quantity": 0.0,
                            "price_rub": local_price,
                        }
                    )

        if local_rows and unknown_local:
            return WebsiteStockResult(
                status="quantity_unknown",
                quantity=None,
                url=final_url,
                title=title,
                details={
                    "source": "krs_local_stores",
                    "lookup_source": lookup_source,
                    "included_stores": local_rows,
                    "rule": "Склад1 Красноярск Высотная + Склад3 Красноярск Удаленный",
                },
            )

        if local_rows:
            total = sum(
                float(row["quantity"] or 0)
                for row in local_rows
            )
            positive_prices = {
                float(row["price_rub"])
                for row in local_rows
                if float(row.get("quantity") or 0) > 0
                and row.get("price_rub") is not None
            }
            local_price_rub = (
                next(iter(positive_prices))
                if len(positive_prices) == 1
                else None
            )
            return WebsiteStockResult(
                status="in_stock" if total > 0 else "out_of_stock",
                quantity=total,
                price_rub=local_price_rub,
                url=final_url,
                title=title,
                details={
                    "source": "krs_local_stores",
                    "lookup_source": lookup_source,
                    "included_stores": local_rows,
                    "rule": "Склад1 Красноярск Высотная + Склад3 Красноярск Удаленный",
                },
            )

        # If the page clearly contains warehouse blocks but neither permitted
        # Krasnoyarsk warehouse appears, both permitted local stocks are zero.
        if store_matches:
            return WebsiteStockResult(
                status="out_of_stock",
                quantity=0.0,
                url=final_url,
                title=title,
                details={
                    "source": "krs_local_stores",
                    "lookup_source": lookup_source,
                    "included_stores": [],
                    "rule": "Склад1 Красноярск Высотная + Склад3 Красноярск Удаленный",
                },
            )

        # No warehouse structure at all: do not turn a template/transport
        # change into a false zero.
        return WebsiteStockResult(
            status="quantity_unknown",
            quantity=None,
            url=final_url,
            title=title,
            details={
                "source": "krs_local_stores_unavailable",
                "lookup_source": lookup_source,
                "rule": "Склад1 Красноярск Высотная + Склад3 Красноярск Удаленный",
            },
        )

    def lookup(self, oem: str) -> WebsiteStockResult:
        oem = str(oem or "").strip()
        if not oem:
            return WebsiteStockResult(status="not_found")

        sitemap_candidates: list[str] = []
        sitemap_error: dict | None = None
        try:
            sitemap_candidates = self._sitemap_candidate_urls(oem)
        except Exception as exc:
            sitemap_error = {
                "error": type(exc).__name__,
                "message": str(exc)[:300],
            }

        exact, sitemap_page_errors = self._open_exact_candidates(
            sitemap_candidates,
            oem,
        )
        if len(exact) == 1:
            final_url, page = exact[0]
            return self._parse_exact_page(
                final_url,
                page,
                lookup_source="sitemap",
            )
        if len(exact) > 1:
            return WebsiteStockResult(
                status="ambiguous",
                details={
                    "lookup_source": "sitemap",
                    "candidates": [url for url, _ in exact],
                },
            )

        # Sitemap can contain stale/404 URLs or omit the OEM from a slug.
        # Fall back to the site's live SearchBooster index and still verify
        # the exact article on the actual product page before trusting stock.
        search_candidates: list[str] = []
        search_error: dict | None = None
        try:
            search_candidates = self._search_candidate_urls(oem)
        except Exception as exc:
            search_error = {
                "error": type(exc).__name__,
                "message": str(exc)[:300],
            }

        exact, search_page_errors = self._open_exact_candidates(
            search_candidates,
            oem,
        )
        if len(exact) == 1:
            final_url, page = exact[0]
            result = self._parse_exact_page(
                final_url,
                page,
                lookup_source="searchbooster",
            )
            result.details["sitemap_candidate_count"] = len(sitemap_candidates)
            if sitemap_page_errors:
                result.details["sitemap_page_errors"] = sitemap_page_errors[:5]
            return result

        if len(exact) > 1:
            return WebsiteStockResult(
                status="ambiguous",
                details={
                    "lookup_source": "searchbooster",
                    "candidates": [url for url, _ in exact],
                },
            )

        # If both discovery mechanisms themselves failed, do not disguise an
        # infrastructure outage as a genuine "not found".
        if sitemap_error and search_error:
            return WebsiteStockResult(
                status="check_failed",
                details={
                    "sitemap_error": sitemap_error,
                    "search_error": search_error,
                },
            )

        # If discovery found candidates but every candidate page failed for
        # transport/server reasons and the live search did not establish that
        # the OEM is absent, report a failed check rather than a false zero.
        all_page_errors = sitemap_page_errors + search_page_errors
        if (sitemap_candidates or search_candidates) and all_page_errors:
            if len(all_page_errors) >= len(sitemap_candidates) + len(search_candidates):
                return WebsiteStockResult(
                    status="check_failed",
                    details={
                        "candidate_count": len(sitemap_candidates)
                        + len(search_candidates),
                        "page_errors": all_page_errors[:8],
                        "sitemap_error": sitemap_error,
                        "search_error": search_error,
                    },
                )

        return WebsiteStockResult(
            status="not_found",
            details={
                "sitemap_candidate_count": len(sitemap_candidates),
                "search_candidate_count": len(search_candidates),
                "sitemap_error": sitemap_error,
                "search_error": search_error,
            },
        )
