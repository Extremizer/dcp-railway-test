"""Baseline regression specification for Extremizer Telegram DP enrichment."""
from common_price_source_service import (
    CACHE_ONLY, LIVE_FIRST, choose_initial_action, resolve_after_live,
)

TG_TECHNICAL={"CLOUDFLARE","AUTH_REQUIRED","TECHNICAL_ERROR"}
FRESH={"fresh":True,"dealer_price_usd":134.50}
STALE={"fresh":False,"dealer_price_usd":134.50}


def test_parts_normal_mode_is_live_first_even_with_fresh_cache():
    assert choose_initial_action(LIVE_FIRST,FRESH)["action"]=="LIVE"


def test_parts_live_found_wins():
    r=resolve_after_live(LIVE_FIRST,"FOUND",130.00,FRESH,TG_TECHNICAL)
    assert r["source"]=="LIVE" and r["dealer_price_usd"]==130.00


def test_parts_three_baseline_technical_statuses_use_fresh_cache():
    for status in TG_TECHNICAL:
        r=resolve_after_live(LIVE_FIRST,status,None,FRESH,TG_TECHNICAL)
        assert r["source"]=="CACHE" and r["dealer_price_usd"]==134.50


def test_parts_timeout_is_not_a_baseline_cache_fallback():
    r=resolve_after_live(LIVE_FIRST,"TIMEOUT",None,FRESH,TG_TECHNICAL)
    assert r["dealer_price_usd"] is None


def test_parts_explicit_negative_never_reuses_cache():
    for status in ("NOT_FOUND","NO_PRICE"):
        r=resolve_after_live(LIVE_FIRST,status,None,FRESH,TG_TECHNICAL)
        assert r["dealer_price_usd"] is None


def test_parts_technical_failure_with_stale_cache_is_unavailable():
    r=resolve_after_live(LIVE_FIRST,"TECHNICAL_ERROR",None,STALE,TG_TECHNICAL)
    assert r["dealer_price_usd"] is None


def test_parts_cache_only_fresh_cache():
    assert choose_initial_action(CACHE_ONLY,FRESH)["action"]=="CACHE"


def test_parts_cache_only_stale_or_missing_is_unavailable():
    assert choose_initial_action(CACHE_ONLY,STALE)["action"]=="UNAVAILABLE"
    assert choose_initial_action(CACHE_ONLY,None)["action"]=="UNAVAILABLE"


def test_non_parts_baseline_is_cache_only():
    assert choose_initial_action(CACHE_ONLY,FRESH)["action"]=="CACHE"
    assert choose_initial_action(CACHE_ONLY,STALE)["action"]=="UNAVAILABLE"
