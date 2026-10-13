"""Isolated Stage 1 contracts; no runtime integration."""

from dataclasses import dataclass


EQUIPMENT_TYPE_VALUES = (
    "ATV", "AUTO", "BOAT", "MOTO", "SNOWMOBILE", "WATERCRAFT",
    "ACCESSORIES", "APPAREL & GEAR", "CLOTHES", "COMPUTER",
    "FOOD & SUPPLEMENTS", "HOUSEHOLD PRODUCTS", "HUNTING & FISHING",
    "MOBILE GADGETS", "PERFUMERY", "PET GOODS", "SHOES", "SPORT",
    "THINGS", "TOOL", "VALUABLES", "ДРУГОЕ",
)


@dataclass(frozen=True)
class BulkOrderParsedItem:
    source_line_number: int
    raw_text: str
    raw_oem: str | None = None
    normalized_oem: str | None = None
    quantity: int | None = None
    status: str = "INVALID"
    error_code: str | None = None
    duplicate_group_id: str | None = None
    suggested_total_quantity: int | None = None


@dataclass(frozen=True)
class BulkOrderParseResult:
    """Counts exclude blanks/headers; review_count includes duplicate rows.

    duplicate_groups maps a group ID to original source line numbers.
    """

    items: tuple[BulkOrderParsedItem, ...]
    total_lines: int
    parsed_count: int
    review_count: int
    invalid_count: int
    duplicate_groups: dict[str, tuple[int, ...]]
    status: str
