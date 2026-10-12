"""Read-only metadata for the shared customer offer card."""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from html import escape
import logging
import math
from pathlib import Path
import sqlite3

def verified_msrp(value, source):
    """Accept explicit public retail metadata, never the dealer-price cell."""
    if source != "dcp_public_form_metadata" or isinstance(value, bool):
        return None
    if not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) and value > 0 else None


def msrp_rub(value, rate):
    if any(isinstance(x, bool) or not isinstance(x, (int, float))
           or not math.isfinite(x) or x <= 0 for x in (value, rate)):
        return None
    return int((Decimal(str(value)) * Decimal(str(rate)) / Decimal("50"))
               .to_integral_value(rounding=ROUND_CEILING) * Decimal("50"))


def cached_rrp_rub(db_file, manufacturer, oems, rate, max_age_hours=168):
    """Read a verified, fresh MSRP for this manufacturer and exact/alias OEM."""
    import oem_reference_service

    if not manufacturer or not isinstance(rate, (int, float)) or rate <= 0:
        return None
    path = Path(db_file)
    if not path.is_file():
        return None
    keys = list(dict.fromkeys(oem_reference_service.normalize_oem(x) for x in oems if x))
    if not keys:
        return None
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as conn:
            placeholders = ",".join("?" for _ in keys)
            rows = conn.execute(
                f"""SELECT msrp_usd,last_verified_at FROM oem_catalog_cache
                    WHERE manufacturer=? AND msrp_verified=1
                      AND current_oem IN ({placeholders})
                    ORDER BY last_verified_at DESC""", (manufacturer, *keys),
            ).fetchall()
        now = datetime.now(timezone.utc)
        for msrp, checked_at in rows:
            if not isinstance(msrp, (int, float)) or not math.isfinite(msrp) or msrp <= 0:
                continue
            try:
                checked = datetime.fromisoformat(str(checked_at))
                if checked.tzinfo is None:
                    continue
                age = (now - checked.astimezone(timezone.utc)).total_seconds() / 3600
                if 0 <= age <= max_age_hours:
                    return msrp_rub(msrp, rate)
            except (ValueError, TypeError):
                continue
    except sqlite3.Error:
        logging.getLogger(__name__).warning("Verified MSRP cache unavailable")
    return None


def _kg(value):
    return format(Decimal(str(value)).normalize(), "f").replace(".", ",")


def reference_weight_lines(current_oem, requested_oem=None):
    """Show safe reference weight and customer delivery estimate per piece."""
    import oem_reference_service

    # Do not invoke the seed helper here: displaying a card must not write a DB.
    if not oem_reference_service.DB_FILE.is_file():
        return []
    try:
        reference = oem_reference_service.lookup_first(current_oem, requested_oem) or {}
    except (sqlite3.Error, OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("OEM reference weight unavailable")
        return []
    actual = reference.get("actual_weight_kg")
    volume = reference.get("volume_weight_kg")
    values = [x for x in (actual, volume)
              if isinstance(x, (int, float)) and not isinstance(x, bool)
              and math.isfinite(x) and x > 0]
    if not values:
        return []
    lines = []
    if actual in values:
        lines.append(f"Справочный вес за 1 шт. — <b>{escape(_kg(actual))} кг</b>")
    if volume in values:
        lines.append(f"Объёмный вес за 1 шт. — <b>{escape(_kg(volume))} кг</b>")
    estimate = (Decimal(str(max(values))) / Decimal("0.1")).to_integral_value(
        rounding=ROUND_CEILING) * Decimal("0.1")
    lines.append(f"Расчётный вес для доставки за 1 шт. — <b>{_kg(estimate)} кг</b>")
    return lines
