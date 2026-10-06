"""Pure COMMON CORE weight and delivery calculations.

No database, Telegram, FastAPI, or environment dependencies.
All rounding is upward and uses Decimal to avoid binary-float boundary errors.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING

ZERO = Decimal("0")
OEM_WEIGHT_STEP_KG = Decimal("0.1")

DELIVERY_WEIGHT_RULES = {
    "econom": {"minimum_kg": Decimal("1.0"), "step_kg": Decimal("0.5")},
    "comfort": {"minimum_kg": Decimal("0.5"), "step_kg": Decimal("0.1")},
    "mix": {"minimum_kg": Decimal("0.5"), "step_kg": Decimal("0.5")},
}


def _decimal_kg(value: Decimal | str | int | float) -> Decimal:
    if isinstance(value, Decimal):
        result = value
    else:
        result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("weight must be finite")
    if result < ZERO:
        raise ValueError("weight must not be negative")
    return result


def _ceil_to_step(value: Decimal, step: Decimal) -> Decimal:
    if step <= ZERO:
        raise ValueError("step must be positive")
    if value == ZERO:
        return ZERO
    units = (value / step).to_integral_value(rounding=ROUND_CEILING)
    return units * step


def normalize_oem_weight_kg(raw_weight_kg: Decimal | str | int | float) -> Decimal:
    """Return calculation weight for one OEM; preserve raw weight separately.

    Zero remains zero (no known positive weight to bill).
    Every positive value rounds upward to the next 0.1 kg boundary.
    """
    raw = _decimal_kg(raw_weight_kg)
    return _ceil_to_step(raw, OEM_WEIGHT_STEP_KG)


def delivery_billable_weight_kg(
    weight_kg: Decimal | str | int | float,
    delivery_method: str,
) -> Decimal:
    """Apply delivery minimum and upward increment to a positive arrival weight.

    ECONOM: minimum 1.0 kg, then 0.5 kg increments.
    COMFORT: minimum 0.5 kg, then 0.1 kg increments.
    MIX: minimum 0.5 kg, then 0.5 kg increments.

    Zero remains zero so an empty/non-weighed arrival is never charged a minimum
    merely by calling this pure calculation function.
    """
    weight = _decimal_kg(weight_kg)
    method = str(delivery_method or "").strip().lower()
    try:
        rule = DELIVERY_WEIGHT_RULES[method]
    except KeyError as exc:
        raise ValueError(f"unknown delivery method: {delivery_method!r}") from exc

    if weight == ZERO:
        return ZERO

    minimum = rule["minimum_kg"]
    step = rule["step_kg"]
    if weight <= minimum:
        return minimum
    return _ceil_to_step(weight, step)
