#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Stock file inspection/import helpers for Extremizer Pro warehouses."""

from __future__ import annotations

import csv
import re
from pathlib import Path
from typing import Any

import openpyxl
import xlrd

HEADER_SCAN_ROWS = 20
PREVIEW_ROWS = 8

OEM_NAMES = {
    "oem", "sku", "partnumber", "partno", "article", "artikl",
    "артикул", "каталожныйномер", "катномер", "номенклатуракод",
    "кодтовара", "код", "партномер", "номердетали",
}
QTY_NAMES = {
    "qty", "quantity", "stock", "available", "balance", "onhand",
    "остаток", "остатки", "количество", "колво", "наличие",
    "свободно", "доступно", "склад",
}
MFG_NAMES = {
    "manufacturer", "brand", "make", "производитель", "бренд", "марка",
}
NAME_NAMES = {
    "name", "description", "product", "partname", "название",
    "наименование", "товар", "описание",
}


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    return re.sub(r"[^a-zа-я0-9]+", "", text)


def _clean_row(row: list[Any]) -> list[Any]:
    values = list(row)
    while values and (values[-1] is None or str(values[-1]).strip() == ""):
        values.pop()
    return values


def _score_header(row: list[Any]) -> tuple[int, dict[str, int]]:
    mapping: dict[str, int] = {}
    for index, value in enumerate(row):
        key = _norm(value)
        if not key:
            continue
        if key in OEM_NAMES and "oem" not in mapping:
            mapping["oem"] = index
        elif key in QTY_NAMES and "quantity" not in mapping:
            mapping["quantity"] = index
        elif key in MFG_NAMES and "manufacturer" not in mapping:
            mapping["manufacturer"] = index
        elif key in NAME_NAMES and "name" not in mapping:
            mapping["name"] = index

    score = 0
    if "oem" in mapping:
        score += 5
    if "quantity" in mapping:
        score += 5
    if "manufacturer" in mapping:
        score += 1
    if "name" in mapping:
        score += 1
    return score, mapping


def _detect_header(rows: list[list[Any]]) -> tuple[int, dict[str, int]]:
    best_index = 0
    best_score = -1
    best_mapping: dict[str, int] = {}
    for index, row in enumerate(rows[:HEADER_SCAN_ROWS]):
        score, mapping = _score_header(row)
        if score > best_score:
            best_index = index
            best_score = score
            best_mapping = mapping
    return best_index, best_mapping


def _read_csv(path: Path) -> tuple[str, list[list[Any]]]:
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "cp1251", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("latin1")

    sample = text[:8192]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
        dialect.delimiter = ";"

    rows = [_clean_row(row) for row in csv.reader(text.splitlines(), dialect)]
    return "CSV", rows


def _read_xlsx(path: Path) -> tuple[str, list[list[Any]]]:
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook[workbook.sheetnames[0]]
    rows = [_clean_row(list(row)) for row in sheet.iter_rows(values_only=True)]
    return sheet.title, rows


def _read_xls(path: Path) -> tuple[str, list[list[Any]]]:
    workbook = xlrd.open_workbook(path)
    sheet = workbook.sheet_by_index(0)
    rows = [
        _clean_row([sheet.cell_value(r, c) for c in range(sheet.ncols)])
        for r in range(sheet.nrows)
    ]
    return sheet.name, rows


def read_table(path: str | Path) -> tuple[str, str, list[list[Any]]]:
    file_path = Path(path)
    suffix = file_path.suffix.lower()
    if suffix == ".csv":
        sheet, rows = _read_csv(file_path)
        return "csv", sheet, rows
    if suffix == ".xlsx":
        sheet, rows = _read_xlsx(file_path)
        return "xlsx", sheet, rows
    if suffix == ".xls":
        sheet, rows = _read_xls(file_path)
        return "xls", sheet, rows
    raise ValueError("Поддерживаются только XLSX, XLS и CSV.")


def inspect_stock_file(path: str | Path) -> dict[str, Any]:
    file_format, sheet_name, rows = read_table(path)
    rows = [row for row in rows if any(str(x or "").strip() for x in row)]
    if not rows:
        raise ValueError("Файл не содержит данных.")

    header_index, mapping = _detect_header(rows)
    header = [str(x or "").strip() for x in rows[header_index]]
    data_rows = rows[header_index + 1:]

    return {
        "file_format": file_format,
        "sheet_name": sheet_name,
        "header_row": header_index + 1,
        "headers": header,
        "mapping": mapping,
        "mapping_ready": "oem" in mapping and "quantity" in mapping,
        "rows_total": len(data_rows),
        "preview": data_rows[:PREVIEW_ROWS],
    }


def _parse_quantity(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("\u00a0", "").replace(" ", "")
    text = text.replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def prepare_stock_rows(
    path: str | Path,
    mapping: dict[str, int] | None = None,
) -> dict[str, Any]:
    inspection = inspect_stock_file(path)
    actual_mapping = mapping or inspection["mapping"]
    if "oem" not in actual_mapping or "quantity" not in actual_mapping:
        raise ValueError("Не удалось определить колонки OEM и количества.")

    _, _, rows = read_table(path)
    rows = [row for row in rows if any(str(x or "").strip() for x in row)]
    data_rows = rows[inspection["header_row"]:]

    prepared: list[dict[str, Any]] = []
    errors = 0

    for row_number, row in enumerate(data_rows, start=inspection["header_row"] + 1):
        try:
            oem_index = actual_mapping["oem"]
            qty_index = actual_mapping["quantity"]
            oem = str(row[oem_index] if oem_index < len(row) else "").strip()
            qty_raw = row[qty_index] if qty_index < len(row) else None
            qty = _parse_quantity(qty_raw)
            if not oem or qty is None:
                errors += 1
                continue

            manufacturer = None
            name = None
            if "manufacturer" in actual_mapping:
                idx = actual_mapping["manufacturer"]
                manufacturer = str(row[idx] if idx < len(row) else "").strip() or None
            if "name" in actual_mapping:
                idx = actual_mapping["name"]
                name = str(row[idx] if idx < len(row) else "").strip() or None

            prepared.append({
                "row_number": row_number,
                "manufacturer": manufacturer,
                "oem": oem,
                "name": name,
                "quantity": qty,
                "source_record": {
                    "row": row_number,
                    "raw": [str(x) if x is not None else "" for x in row],
                },
            })
        except Exception:
            errors += 1

    return {
        **inspection,
        "mapping": actual_mapping,
        "rows_success": len(prepared),
        "rows_error": errors,
        "items": prepared,
    }
