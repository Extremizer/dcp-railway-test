"""Pure COMMON CORE pricing rules for EXTREMIZER PRO.

No SQLite, DCP, Telegram, FastAPI, filesystem, or environment dependencies.
Source selection (cache/live/unavailable) belongs to a separate orchestration layer.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP


def _positive_decimal(value, field: str) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite() or result <= 0:
        raise ValueError(f"{field} must be positive and finite")
    return result


def customer_price_rub_from_dp(dp_usd, coefficient, usd_rub_rate) -> int | None:
    """DP × coefficient × rate, always rounded upward to the next 100 RUB."""
    try:
        dp = _positive_decimal(dp_usd, "dp_usd")
        coef = _positive_decimal(coefficient, "coefficient")
        rate = _positive_decimal(usd_rub_rate, "usd_rub_rate")
    except ValueError:
        return None
    raw = dp * coef * rate
    blocks = (raw / Decimal("100")).to_integral_value(rounding=ROUND_CEILING)
    return int(blocks * Decimal("100"))


def rrp_rub_from_msrp(msrp_usd, usd_rub_rate) -> int | None:
    """Convert public MSRP USD to RUB using whole-ruble half-up rounding."""
    try:
        msrp = _positive_decimal(msrp_usd, "msrp_usd")
        rate = _positive_decimal(usd_rub_rate, "usd_rub_rate")
    except ValueError:
        return None
    return int((msrp * rate).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def msrp_offer(customer_rub: int | None, rrp_rub: int | None) -> dict:
    """Return the single shared MSRP visibility/benefit decision."""
    show = (
        isinstance(customer_rub, int)
        and customer_rub > 0
        and isinstance(rrp_rub, int)
        and rrp_rub > customer_rub
    )
    benefit = (
        ((Decimal(rrp_rub) - Decimal(customer_rub)) / Decimal(rrp_rub) * Decimal("100"))
        .quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        if show else None
    )
    return {
        "show_msrp": show,
        "benefit_pct": float(benefit) if benefit is not None else None,
    }


def is_dp_fresh(age_hours, max_age_hours) -> bool:
    """Shared freshness policy: TTL must be enabled and age must be within it."""
    try:
        age = Decimal(str(age_hours))
        maximum = Decimal(str(max_age_hours))
    except Exception:
        return False
    return age.is_finite() and maximum.is_finite() and age >= 0 and maximum > 0 and age <= maximum
