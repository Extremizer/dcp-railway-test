"""End-to-end regression for PriceSourceService -> PricingService.

No interface, DB, DCP, HTTP, or environment dependencies.
"""
from common_price_source_service import (
    CACHE_FIRST, LIVE_FIRST, CACHE_ONLY,
    choose_initial_action, resolve_after_live, msrp_only_result,
)
from common_pricing_service import (
    customer_price_rub_from_dp, rrp_rub_from_msrp, msrp_offer,
)

FRESH_417={"fresh": True, "dealer_price_usd": 134.50}
STALE_417={"fresh": False, "dealer_price_usd": 134.50}


def quote_from_dp(dp, coefficient, rate, msrp=None):
    customer=customer_price_rub_from_dp(dp, coefficient, rate)
    rrp=rrp_rub_from_msrp(msrp, rate) if msrp is not None else None
    offer=msrp_offer(customer, rrp)
    return customer, rrp, offer


def test_control_417224332_cache_first():
    first=choose_initial_action(CACHE_FIRST,FRESH_417)
    assert first["action"]=="CACHE"
    customer,rrp,offer=quote_from_dp(134.50,1.34,105,189.99)
    assert customer==19000
    assert rrp==19949
    assert offer=={"show_msrp":True,"benefit_pct":4.8}


def test_control_417224332_cache_only_web():
    assert choose_initial_action(CACHE_ONLY,FRESH_417)["action"]=="CACHE"
    assert quote_from_dp(134.50,1.34,105,189.99)[0]==19000


def test_live_first_found_beats_cache():
    assert choose_initial_action(LIVE_FIRST,FRESH_417)["action"]=="LIVE"
    resolved=resolve_after_live(LIVE_FIRST,"FOUND",130.00,FRESH_417)
    assert resolved["source"]=="LIVE"
    assert quote_from_dp(resolved["dealer_price_usd"],1.34,105)[0]==18300


def test_live_first_technical_failure_falls_back_to_fresh_cache():
    resolved=resolve_after_live(LIVE_FIRST,"TECHNICAL_ERROR",None,FRESH_417)
    assert resolved["source"]=="CACHE"
    assert quote_from_dp(resolved["dealer_price_usd"],1.34,105)[0]==19000


def test_live_first_explicit_no_price_does_not_reuse_cache():
    resolved=resolve_after_live(LIVE_FIRST,"NO_PRICE",None,FRESH_417)
    assert resolved["dealer_price_usd"] is None


def test_cache_first_stale_then_live_found():
    assert choose_initial_action(CACHE_FIRST,STALE_417)["action"]=="LIVE"
    resolved=resolve_after_live(CACHE_FIRST,"FOUND",134.50,STALE_417)
    assert quote_from_dp(resolved["dealer_price_usd"],1.34,105)[0]==19000


def test_cache_first_stale_then_live_failure_is_unavailable():
    resolved=resolve_after_live(CACHE_FIRST,"TIMEOUT",None,STALE_417)
    assert resolved["dealer_price_usd"] is None


def test_cache_only_stale_is_unavailable():
    assert choose_initial_action(CACHE_ONLY,STALE_417)["action"]=="UNAVAILABLE"


def test_cache_miss_live_failure_is_unavailable():
    assert choose_initial_action(CACHE_FIRST,None)["action"]=="LIVE"
    assert resolve_after_live(CACHE_FIRST,"AUTH_REQUIRED",None,None)["dealer_price_usd"] is None


def test_msrp_only_never_produces_customer_price():
    source=msrp_only_result(189.99)
    assert source["dealer_price_usd"] is None
    assert source["price_available"] is False
    assert rrp_rub_from_msrp(source["msrp_usd"],105)==19949


def test_rrp_hidden_when_not_above_customer_price():
    customer,rrp,offer=quote_from_dp(150,1.34,105,189.99)
    assert customer==21200
    assert rrp==19949
    assert offer["show_msrp"] is False and offer["benefit_pct"] is None


def test_exact_100_rub_boundary_not_overrounded():
    assert customer_price_rub_from_dp(100,1,1)==100


def test_just_over_100_rub_boundary_rounds_up():
    assert customer_price_rub_from_dp(100.01,1,1)==200
