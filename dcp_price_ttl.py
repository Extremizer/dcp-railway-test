"""Authoritative bounded freshness checks for verified DCP prices.

A timestamp must be timezone-aware and not in the future. Unknown/invalid age
is stale (fail closed); MSRP is never an alternative to Dealer Price.
"""
from datetime import datetime, timezone
import math

DEALER_PRICE_MAX_AGE_HOURS = 24.0
MSRP_MAX_AGE_HOURS = 720.0


def is_fresh_timestamp(verified_at, max_age_hours, *, now=None):
    """Inclusive TTL: age <= max_age_hours. Future/invalid timestamps fail closed."""
    try:
        if not isinstance(verified_at, str) or not verified_at.strip():
            return False
        parsed = datetime.fromisoformat(verified_at.strip().replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            return False
        current = now if now is not None else datetime.now(timezone.utc)
        if current.tzinfo is None or current.utcoffset() is None:
            return False
        ttl = float(max_age_hours)
        if not math.isfinite(ttl) or ttl <= 0:
            return False
        age_seconds = (current - parsed).total_seconds()
        return 0 <= age_seconds <= ttl * 3600
    except (TypeError, ValueError, OverflowError):
        return False
