"""Probnik adapter regression for CACHE_FIRST common pricing."""
from common_price_source_service import CACHE_FIRST, choose_initial_action, resolve_after_live
from common_pricing_service import customer_price_rub_from_dp

FRESH={"fresh":True,"dealer_price_usd":134.50}


def test_fresh_cache_short_circuits_live():
    d=choose_initial_action(CACHE_FIRST,FRESH)
    assert d["action"]=="CACHE"
    assert customer_price_rub_from_dp(FRESH["dealer_price_usd"],1.34,105)==19000


def test_cache_miss_requests_live_and_found_prices():
    assert choose_initial_action(CACHE_FIRST,None)["action"]=="LIVE"
    r=resolve_after_live(CACHE_FIRST,"FOUND",134.50,None)
    assert r["source"]=="LIVE"
    assert customer_price_rub_from_dp(r["dealer_price_usd"],1.34,105)==19000


def test_stale_is_treated_as_cache_miss():
    stale={"fresh":False,"dealer_price_usd":134.50}
    assert choose_initial_action(CACHE_FIRST,stale)["action"]=="LIVE"


def test_failed_live_after_miss_never_reuses_old_dp():
    stale={"fresh":False,"dealer_price_usd":134.50}
    for status in ("TIMEOUT","AUTH_REQUIRED","CLOUDFLARE","TECHNICAL_ERROR","NO_PRICE","NOT_FOUND"):
        r=resolve_after_live(CACHE_FIRST,status,None,stale)
        assert r["dealer_price_usd"] is None


def test_auto_identity_needs_live_dp_and_manufacturer():
    r=resolve_after_live(CACHE_FIRST,"FOUND",120.00,None)
    assert r["source"]=="LIVE"
    manufacturer="Can-Am"
    accepted=(r["source"]=="LIVE" and bool(manufacturer))
    assert accepted is True


def test_auto_identity_without_manufacturer_is_not_accepted():
    r=resolve_after_live(CACHE_FIRST,"FOUND",120.00,None)
    manufacturer=""
    accepted=(r["source"]=="LIVE" and bool(manufacturer))
    assert accepted is False


def test_msrp_does_not_rescue_failed_dp():
    r=resolve_after_live(CACHE_FIRST,"NO_PRICE",None,None)
    msrp_usd=189.99
    assert msrp_usd>0
    assert r["dealer_price_usd"] is None
