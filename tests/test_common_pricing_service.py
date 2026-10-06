from decimal import Decimal
import math
import pytest

from common_pricing_service import (
    customer_price_rub_from_dp,
    is_dp_fresh,
    msrp_offer,
    rrp_rub_from_msrp,
)


def legacy_extremizer_price(dp, coefficient, rate):
    value = Decimal(str(dp)) * Decimal(str(coefficient)) * Decimal(str(rate))
    return int((value / Decimal("100")).to_integral_value(rounding="ROUND_CEILING") * Decimal("100"))


def legacy_probnik_price(dp, coefficient, rate):
    return int(math.ceil((dp * coefficient * rate) / 100.0) * 100)


@pytest.mark.parametrize(
    "dp,coefficient,rate",
    [
        (134.50, 1.34, 105),
        (1.00, 1.34, 105),
        (100.00, 1.00, 1.00),
        (100.01, 1.00, 1.00),
        (207.07, 1.24, 1.00),
        (999.99, 1.34, 97.5),
    ],
)
def test_customer_price_matches_both_legacy_paths(dp, coefficient, rate):
    common = customer_price_rub_from_dp(dp, coefficient, rate)
    assert common == legacy_extremizer_price(dp, coefficient, rate)
    assert common == legacy_probnik_price(dp, coefficient, rate)


@pytest.mark.parametrize(
    "dp,coefficient,rate",
    [(0,1.34,105),(-1,1.34,105),(10,0,105),(10,1.34,0),(None,1.34,105)],
)
def test_invalid_customer_price_is_unavailable(dp, coefficient, rate):
    assert customer_price_rub_from_dp(dp, coefficient, rate) is None


@pytest.mark.parametrize(
    "msrp,rate,expected",
    [(189.99,105,19949),(1.00,105,105),(10.005,100,1001)],
)
def test_rrp_whole_rub(msrp, rate, expected):
    assert rrp_rub_from_msrp(msrp, rate) == expected


@pytest.mark.parametrize(
    "customer,rrp,show,benefit",
    [(18900,19949,True,5.3),(19949,19949,False,None),(20000,19949,False,None),(None,19949,False,None)],
)
def test_msrp_visibility_and_benefit(customer, rrp, show, benefit):
    result=msrp_offer(customer,rrp)
    assert result["show_msrp"] is show
    assert result["benefit_pct"] == benefit


@pytest.mark.parametrize(
    "age,max_age,expected",
    [(0,168,True),(168,168,True),(168.0001,168,False),(1,0,False),(-1,168,False)],
)
def test_dp_freshness_policy(age,max_age,expected):
    assert is_dp_fresh(age,max_age) is expected
