"""Regression/acceptance specification for delivery billable weights.

Legacy cases prove that applying the new service changes only cases where the
new minimum/increment rule is intended to change the charged actual weight.
"""
from decimal import Decimal

import pytest

from common_weight_service import delivery_billable_weight_kg


def legacy_total(calculation_type, actual, volume, base, volume_rate):
    actual = Decimal(str(actual))
    volume = Decimal(str(volume))
    base = Decimal(str(base))
    volume_rate = Decimal(str(volume_rate))
    if calculation_type == "actual_only":
        return actual * base
    if calculation_type == "excess_volume":
        return actual * base + max(volume - actual, Decimal("0")) * volume_rate
    if calculation_type == "all_volume":
        return actual * base + volume * volume_rate
    raise ValueError(calculation_type)


def common_core_total(method, calculation_type, actual, volume, base, volume_rate):
    billable_actual = delivery_billable_weight_kg(actual, method)
    volume = Decimal(str(volume))
    base = Decimal(str(base))
    volume_rate = Decimal(str(volume_rate))
    if calculation_type == "actual_only":
        return billable_actual * base
    if calculation_type == "excess_volume":
        return billable_actual * base + max(volume - billable_actual, Decimal("0")) * volume_rate
    if calculation_type == "all_volume":
        return billable_actual * base + volume * volume_rate
    raise ValueError(calculation_type)


@pytest.mark.parametrize(
    "method,calc,actual,volume,base,volume_rate",
    [
        ("econom", "actual_only", "1.0", "0", "1500", "0"),
        ("econom", "actual_only", "1.5", "0", "1500", "0"),
        ("comfort", "excess_volume", "0.5", "0.8", "2500", "650"),
        ("comfort", "excess_volume", "1.3", "1.8", "2500", "650"),
        ("mix", "all_volume", "0.5", "0.8", "3000", "650"),
        ("mix", "all_volume", "1.5", "1.8", "3000", "650"),
    ],
)
def test_legacy_result_unchanged_on_exact_billable_boundaries(
    method, calc, actual, volume, base, volume_rate
):
    assert common_core_total(method, calc, actual, volume, base, volume_rate) == legacy_total(
        calc, actual, volume, base, volume_rate
    )


@pytest.mark.parametrize(
    "method,calc,actual,volume,base,volume_rate,expected",
    [
        ("econom", "actual_only", "0.23", "0", "1500", "0", "1500.0"),
        ("econom", "actual_only", "1.21", "0", "1500", "0", "2250.0"),
        ("comfort", "excess_volume", "0.23", "0.8", "2500", "650", "1445.0"),
        ("comfort", "excess_volume", "1.21", "1.8", "2500", "650", "3575.0"),
        ("mix", "all_volume", "0.23", "0.8", "3000", "650", "2020.0"),
        ("mix", "all_volume", "1.21", "1.8", "3000", "650", "5670.0"),
    ],
)
def test_new_minimum_and_increment_rules(
    method, calc, actual, volume, base, volume_rate, expected
):
    assert common_core_total(method, calc, actual, volume, base, volume_rate) == Decimal(expected)
