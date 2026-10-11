"""Pure bulk text parsing, without catalog lookups or OEM rewriting."""

import re
from dataclasses import replace

from bulk_order_contract import BulkOrderParsedItem, BulkOrderParseResult


_EDGE_WHITESPACE = "\u200b\u200c\u200d\u2060\ufeff"
_OEM_HEADERS = {"oem", "part number", "part no", "p/n", "артикул",
                "каталожный номер", "номер"}
_QTY_HEADERS = {"qty", "quantity", "кол-во", "количество", "шт"}
_OEM = re.compile(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*")


def _trim(value: str) -> str:
    return value.strip().strip(_EDGE_WHITESPACE).strip()


def _is_oem(value: str) -> bool:
    # Numeric content distinguishes the supported part tokens from prose.
    return bool(_OEM.fullmatch(value) and re.search(r"[0-9]", value))


def _cells(text: str) -> list[str] | None:
    for separator in ("\t", ";", ","):
        if separator in text:
            return [_trim(cell) for cell in text.split(separator)]
    return None


def _parse_row(number: int, raw: str, text: str,
               cells: list[str] | None = None,
               columns: tuple[int, int] | None = None) -> BulkOrderParsedItem:
    def item(status: str, error: str | None, oem: str | None = None,
             qty: int | None = None) -> BulkOrderParsedItem:
        return BulkOrderParsedItem(number, raw, oem, _trim(oem) if oem else None,
                                   qty, status, error)

    if cells is not None:
        if columns is not None:
            oi, qi = columns
            oem = cells[oi] if oi < len(cells) else ""
            tail = cells[qi] if qi < len(cells) else ""
        elif len(cells) == 2:
            oem, tail = cells
        else:
            return item("NEEDS_REVIEW", "UNSUPPORTED_ROW")
    else:
        multiple = re.split(r"\s*/\s*", text)
        if len(multiple) > 1:
            tokens = [re.split(r"\s", part)[0] for part in multiple]
            if sum(_is_oem(token) for token in tokens) > 1:
                return item("NEEDS_REVIEW", "MULTIPLE_OEM")
        match = re.match(r"([^\s; ,\t]+)(.*)$", text)
        if not match:
            return item("INVALID", "MISSING_OEM")
        oem, tail = match.groups()
        tail = _trim(tail)
    oem = _trim(oem)
    if not _is_oem(oem):
        return item("INVALID", "MISSING_OEM")
    if not tail:
        return item("PARSED", None, oem, 1)
    # Strip only an explicit quantity operator; dashes inside OEMs survive.
    tail = re.sub(r"^(?:[x×*]|-\s+)\s*", "", tail)
    tail = re.sub(r"\s+шт$", "", tail, flags=re.IGNORECASE)
    if re.fullmatch(r"[+-]?[0-9]+", tail):
        qty = int(tail)
        if qty >= 1:
            return item("PARSED", None, oem, qty)
        return item("INVALID", "INVALID_QTY", oem)
    if re.fullmatch(r"[0-9]+(?:\s+[0-9]+)+", tail):
        return item("NEEDS_REVIEW", "AMBIGUOUS_QTY", oem)
    if re.fullmatch(r"[+-]?[0-9]+\.[0-9]+", tail):
        return item("INVALID", "INVALID_QTY", oem)
    return item("NEEDS_REVIEW", "UNSUPPORTED_ROW", oem)


def parse_bulk_order(raw_input: str) -> BulkOrderParseResult:
    """Parse approved text/list/table formats, retaining each product row."""
    items = []
    columns = None
    for number, raw in enumerate(raw_input.splitlines(), 1):
        text = _trim(raw)
        if not text:
            continue
        text = re.sub(r"^(?:[0-9]+[.)]\s+|[-•]\s+)", "", text)
        # Tabs are column separators even at the edge (an empty OEM cell).
        cells = _cells(raw if "\t" in raw else text)
        if cells is not None:
            headers = [cell.casefold() for cell in cells]
            oi = next((i for i, h in enumerate(headers) if h in _OEM_HEADERS), None)
            qi = next((i for i, h in enumerate(headers) if h in _QTY_HEADERS), None)
            if oi is not None and qi is not None:
                columns = (oi, qi)
                continue
        items.append(_parse_row(number, raw, text, cells, columns))

    by_oem = {}
    for index, row in enumerate(items):
        if row.status == "PARSED":
            by_oem.setdefault(row.normalized_oem, []).append(index)
    groups = {}
    for indices in by_oem.values():
        if len(indices) < 2:
            continue
        group = f"duplicate-{items[indices[0]].source_line_number}"
        groups[group] = tuple(items[i].source_line_number for i in indices)
        total = sum(items[i].quantity for i in indices)
        for i in indices:
            items[i] = replace(items[i], status="DUPLICATE",
                               error_code="DUPLICATE_POSSIBLE",
                               duplicate_group_id=group,
                               suggested_total_quantity=total)
    parsed = sum(row.status == "PARSED" for row in items)
    invalid = sum(row.status == "INVALID" for row in items)
    review = len(items) - parsed - invalid
    status = "EMPTY" if not items else "NEEDS_REVIEW" if review or invalid else "READY"
    return BulkOrderParseResult(tuple(items), len(items), parsed, review,
                                invalid, groups, status)
