"""WEB adapter regression: WEB remains CACHE_ONLY while pricing math is COMMON CORE."""
from common_price_source_service import CACHE_ONLY, choose_initial_action
from common_pricing_service import customer_price_rub_from_dp, rrp_rub_from_msrp, msrp_offer


def web_price(dp, coefficient=1.34, rate=105, msrp=189.99):
    decision=choose_initial_action(CACHE_ONLY,dp)
    customer=None
    if decision["action"]=="CACHE":
        customer=customer_price_rub_from_dp(dp["dealer_price_usd"],coefficient,rate)
    rrp=rrp_rub_from_msrp(msrp,rate)
    return decision,customer,rrp,msrp_offer(customer,rrp)


def test_fresh_cache_control():
    d,c,r,o=web_price({"fresh":True,"dealer_price_usd":134.50})
    assert d["action"]=="CACHE"
    assert c==19000 and r==19949
    assert o=={"show_msrp":True,"benefit_pct":4.8}


def test_stale_cache_still_has_no_web_price():
    d,c,r,o=web_price({"fresh":False,"dealer_price_usd":134.50})
    assert d["action"]=="UNAVAILABLE" and c is None
    assert r==19949 and o["show_msrp"] is False


def test_cache_miss_still_has_no_web_price():
    d,c,r,o=web_price(None)
    assert d["action"]=="UNAVAILABLE" and c is None
    assert r==19949 and o["show_msrp"] is False


def test_msrp_below_customer_is_hidden():
    d,c,r,o=web_price({"fresh":True,"dealer_price_usd":150.00})
    assert c==21200 and r==19949
    assert o=={"show_msrp":False,"benefit_pct":None}
