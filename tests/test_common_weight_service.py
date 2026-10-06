from decimal import Decimal

import pytest

from common_weight_service import (
    delivery_billable_weight_kg,
    normalize_oem_weight_kg,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("0", "0"),
        ("0.001", "0.1"),
        ("0.099", "0.1"),
        ("0.100", "0.1"),
        ("0.101", "0.2"),
        ("0.51", "0.6"),
        ("1.00", "1.0"),
        ("1.01", "1.1"),
        ("4.54", "4.6"),
    ],
)
def test_normalize_oem_weight(raw, expected):
    assert normalize_oem_weight_kg(raw) == Decimal(expected)


@pytest.mark.parametrize(
    ("method", "weight", "expected"),
    [
        ("econom", "0", "0"),
        ("econom", "0.23", "1.0"),
        ("econom", "1.0", "1.0"),
        ("econom", "1.01", "1.5"),
        ("econom", "1.21", "1.5"),
        ("econom", "1.50", "1.5"),
        ("econom", "1.51", "2.0"),
        ("comfort", "0", "0"),
        ("comfort", "0.23", "0.5"),
        ("comfort", "0.50", "0.5"),
        ("comfort", "0.51", "0.6"),
        ("comfort", "1.21", "1.3"),
        ("mix", "0", "0"),
        ("mix", "0.23", "0.5"),
        ("mix", "0.50", "0.5"),
        ("mix", "0.51", "1.0"),
        ("mix", "1.21", "1.5"),
    ],
)
def test_delivery_billable_weight(method, weight, expected):
    assert delivery_billable_weight_kg(weight, method) == Decimal(expected)


@pytest.mark.parametrize("value", ["-0.001", "-1"])
def test_negative_weight_rejected(value):
    with pytest.raises(ValueError):
        normalize_oem_weight_kg(value)


def test_unknown_delivery_method_rejected():
    with pytest.raises(ValueError):
        delivery_billable_weight_kg("1.0", "unknown")
