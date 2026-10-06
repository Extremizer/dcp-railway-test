"""Pure source-selection policy for dealer price (DP).

This module decides WHAT action/result is allowed. It does not access SQLite,
DCP, HTTP, Telegram, files, or environment.
"""
from __future__ import annotations

CACHE_FIRST = "cache_first"
LIVE_FIRST = "live_first"
CACHE_ONLY = "cache_only"

TECHNICAL_LIVE_FAILURES = frozenset(
    {"CLOUDFLARE", "AUTH_REQUIRED", "TECHNICAL_ERROR", "TIMEOUT"}
)
EXPLICIT_LIVE_NEGATIVES = frozenset({"NOT_FOUND", "NO_PRICE"})


def _fresh_positive(cache: dict | None) -> bool:
    if not cache or not bool(cache.get("fresh")):
        return False
    value = cache.get("dealer_price_usd")
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def choose_initial_action(mode: str, cache: dict | None) -> dict:
    """Choose CACHE, LIVE, or UNAVAILABLE before any live attempt."""
    if mode not in {CACHE_FIRST, LIVE_FIRST, CACHE_ONLY}:
        raise ValueError(f"unknown price-source mode: {mode}")
    fresh = _fresh_positive(cache)
    if mode == CACHE_ONLY:
        return {"action": "CACHE" if fresh else "UNAVAILABLE", "reason": "fresh_cache" if fresh else "cache_miss"}
    if mode == CACHE_FIRST and fresh:
        return {"action": "CACHE", "reason": "fresh_cache"}
    return {"action": "LIVE", "reason": "live_first" if mode == LIVE_FIRST else "cache_miss"}


def resolve_after_live(
    mode: str,
    live_status: str | None,
    live_dp_usd,
    cache: dict | None,
    technical_fallback_statuses=None,
) -> dict:
    """Resolve live result without ever substituting MSRP for DP.

    LIVE_FIRST preserves Extremizer Telegram baseline: fresh cache is fallback
    only for technical/session live failures, never explicit NOT_FOUND/NO_PRICE.
    CACHE_FIRST preserves Probnik baseline: this function is reached only after
    a cache miss/stale cache, so a failed live lookup is unavailable.
    """
    if mode not in {CACHE_FIRST, LIVE_FIRST}:
        raise ValueError("live resolution is valid only for live-capable modes")
    status = str(live_status or "").upper()
    if (
        status == "FOUND"
        and isinstance(live_dp_usd, (int, float))
        and not isinstance(live_dp_usd, bool)
        and live_dp_usd > 0
    ):
        return {"source": "LIVE", "dealer_price_usd": float(live_dp_usd), "status": "FOUND"}

    fallback_statuses = (
        TECHNICAL_LIVE_FAILURES
        if technical_fallback_statuses is None
        else frozenset(str(item).upper() for item in technical_fallback_statuses)
    )
    if mode == LIVE_FIRST and status in fallback_statuses and _fresh_positive(cache):
        return {
            "source": "CACHE",
            "dealer_price_usd": float(cache["dealer_price_usd"]),
            "status": "CACHE_FALLBACK",
            "live_status": status,
        }

    return {"source": None, "dealer_price_usd": None, "status": status or "UNAVAILABLE"}


def msrp_only_result(msrp_usd) -> dict:
    """MSRP may be retained for RRP/reference, but is never a DP source."""
    valid = isinstance(msrp_usd, (int, float)) and not isinstance(msrp_usd, bool) and msrp_usd > 0
    return {
        "dealer_price_usd": None,
        "price_available": False,
        "msrp_usd": float(msrp_usd) if valid else None,
    }
