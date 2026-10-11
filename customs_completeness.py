"""Filled-field check only; no value verification or shipment validation."""

from dataclasses import dataclass
from collections.abc import Mapping


REQUIRED_CUSTOMS_FIELDS = (
    "description_ru", "description_en", "manufacturer", "country_of_origin",
    "gross_weight_kg", "net_weight_kg", "equipment_type",
)


@dataclass(frozen=True)
class CustomsCompletenessResult:
    is_complete: bool
    missing_fields: tuple[str, ...]


def validate_customs_profile(profile) -> CustomsCompletenessResult:
    """Accept a mapping or field-bearing object; zero is a filled value."""
    missing = []
    for field in REQUIRED_CUSTOMS_FIELDS:
        value = profile.get(field) if isinstance(profile, Mapping) else getattr(profile, field, None)
        if value is None or isinstance(value, str) and not value.strip():
            missing.append(field)
    return CustomsCompletenessResult(not missing, tuple(missing))
