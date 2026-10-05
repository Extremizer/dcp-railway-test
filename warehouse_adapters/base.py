#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import html as html_lib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.request import Request, urlopen

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/150.0 Safari/537.36"
)


@dataclass
class WebsiteStockResult:
    status: str
    quantity: float | None = None
    price_rub: float | None = None
    url: str | None = None
    title: str | None = None
    observed_at: str = field(
        default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds")
    )
    details: dict[str, Any] = field(default_factory=dict)


def fetch_text(
    url: str,
    timeout: int = 25,
    retries: int = 2,
) -> tuple[str, str]:
    last_error: Exception | None = None
    for attempt in range(max(1, retries)):
        try:
            req = Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                },
            )
            with urlopen(req, timeout=timeout) as response:
                raw = response.read()
                charset = response.headers.get_content_charset()
                if charset:
                    encoding = charset
                elif b"charset=windows-1251" in raw[:5000].lower():
                    encoding = "cp1251"
                elif b"charset=cp1251" in raw[:5000].lower():
                    encoding = "cp1251"
                else:
                    encoding = "utf-8"
                return response.geturl(), raw.decode(encoding, errors="replace")
        except Exception as exc:
            last_error = exc
            if attempt + 1 < max(1, retries):
                time.sleep(1.5 * (attempt + 1))
    assert last_error is not None
    raise last_error


def html_to_text(value: str) -> str:
    value = re.sub(r"<script\b[^>]*>[\s\S]*?</script>", " ", value, flags=re.I)
    value = re.sub(r"<style\b[^>]*>[\s\S]*?</style>", " ", value, flags=re.I)
    value = re.sub(r"<[^>]+>", " ", value)
    value = html_lib.unescape(value)
    return re.sub(r"\s+", " ", value).strip()


def extract_title(value: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", value, re.I | re.S)
    if not match:
        return None
    return html_to_text(match.group(1)) or None
