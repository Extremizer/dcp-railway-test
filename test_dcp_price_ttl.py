"""Unit and source-contract regressions: no live DCP or persistent DB writes."""
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dcp_price_ttl import (
    DEALER_PRICE_MAX_AGE_HOURS, MSRP_MAX_AGE_HOURS, is_fresh_timestamp,
)


class DcpPriceTtlTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 16, 12, tzinfo=timezone.utc)

    def stamp(self, hours):
        return (self.now - timedelta(hours=hours)).isoformat()

    def test_dealer_23_24_25_hours(self):
        for hours, expected in ((23, True), (24, True), (25, False)):
            with self.subTest(hours=hours):
                self.assertEqual(is_fresh_timestamp(self.stamp(hours), DEALER_PRICE_MAX_AGE_HOURS, now=self.now), expected)

    def test_msrp_719_720_721_hours(self):
        for hours, expected in ((719, True), (720, True), (721, False)):
            with self.subTest(hours=hours):
                self.assertEqual(is_fresh_timestamp(self.stamp(hours), MSRP_MAX_AGE_HOURS, now=self.now), expected)

    def test_invalid_future_or_ambiguous_age_fails_closed(self):
        for value in (None, "", "invalid", "2026-10-16T12:00:00", self.stamp(-1)):
            with self.subTest(value=value):
                self.assertFalse(is_fresh_timestamp(value, DEALER_PRICE_MAX_AGE_HOURS, now=self.now))
        self.assertFalse(is_fresh_timestamp(self.stamp(0), float("nan"), now=self.now))

    def test_exact_boundary_subsecond(self):
        self.assertFalse(is_fresh_timestamp(
            (self.now - timedelta(hours=24, microseconds=1)).isoformat(),
            DEALER_PRICE_MAX_AGE_HOURS, now=self.now))

    def test_shared_consumers_enforce_cache_freshness(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")
        self.assertIn("is_fresh_timestamp(row[3], DP_CACHE_MAX_AGE_HOURS)", source)
        self.assertIn("is_fresh_timestamp(last_verified_at, MSRP_MAX_AGE_HOURS)", source)
        self.assertIn("is_fresh_timestamp(entry.get(\"captured_at\"), MSRP_MAX_AGE_HOURS)", source)
        self.assertIn('DP_CACHE_MAX_AGE_HOURS = DEALER_PRICE_MAX_AGE_HOURS', source)

    def test_msrp_not_used_as_dealer_price(self):
        source = Path(__file__).with_name("extremizer_bot.py").read_text(encoding="utf-8")
        self.assertIn('if source == "usa" and not (dp and dp.get("fresh")):', source)
        self.assertIn('if cached and cached.get("fresh"):', source)
        self.assertIn('MSRP is never substituted for DP', source)


if __name__ == "__main__":
    unittest.main()
