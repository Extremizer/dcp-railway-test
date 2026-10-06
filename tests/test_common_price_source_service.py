import pytest
from common_price_source_service import (
    CACHE_FIRST, LIVE_FIRST, CACHE_ONLY,
    choose_initial_action, resolve_after_live, msrp_only_result,
)

FRESH={"fresh": True, "dealer_price_usd": 134.50}
STALE={"fresh": False, "dealer_price_usd": 134.50}

@pytest.mark.parametrize("mode,cache,action",[
    (CACHE_FIRST,FRESH,"CACHE"),
    (CACHE_FIRST,STALE,"LIVE"),
    (CACHE_FIRST,None,"LIVE"),
    (CACHE_ONLY,FRESH,"CACHE"),
    (CACHE_ONLY,STALE,"UNAVAILABLE"),
    (CACHE_ONLY,None,"UNAVAILABLE"),
    (LIVE_FIRST,FRESH,"LIVE"),
    (LIVE_FIRST,STALE,"LIVE"),
])
def test_initial_policy(mode,cache,action):
    assert choose_initial_action(mode,cache)["action"] == action

@pytest.mark.parametrize("mode,cache",[(CACHE_FIRST,None),(CACHE_FIRST,STALE),(LIVE_FIRST,None),(LIVE_FIRST,FRESH)])
def test_live_found(mode,cache):
    r=resolve_after_live(mode,"FOUND",120.25,cache)
    assert r["source"]=="LIVE" and r["dealer_price_usd"]==120.25

@pytest.mark.parametrize("status",["CLOUDFLARE","AUTH_REQUIRED","TECHNICAL_ERROR","TIMEOUT"])
def test_live_first_technical_failure_can_use_fresh_cache(status):
    r=resolve_after_live(LIVE_FIRST,status,None,FRESH)
    assert r["source"]=="CACHE" and r["dealer_price_usd"]==134.50

@pytest.mark.parametrize("status",["NOT_FOUND","NO_PRICE"])
def test_live_first_explicit_negative_never_reuses_old_dp(status):
    assert resolve_after_live(LIVE_FIRST,status,None,FRESH)["dealer_price_usd"] is None

@pytest.mark.parametrize("status",["CLOUDFLARE","AUTH_REQUIRED","TECHNICAL_ERROR","TIMEOUT","NOT_FOUND","NO_PRICE"])
def test_cache_first_failed_live_after_cache_miss_is_unavailable(status):
    assert resolve_after_live(CACHE_FIRST,status,None,STALE)["dealer_price_usd"] is None

def test_found_without_valid_dp_is_unavailable():
    assert resolve_after_live(CACHE_FIRST,"FOUND",None,None)["dealer_price_usd"] is None

def test_msrp_only_never_becomes_dp():
    r=msrp_only_result(189.99)
    assert r["msrp_usd"]==189.99
    assert r["dealer_price_usd"] is None
    assert r["price_available"] is False
