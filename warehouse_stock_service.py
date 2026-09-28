#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from pathlib import Path

import warehouse_store
import stock_engine
from warehouse_adapters import WebsiteStockResult, get_adapter

DEFAULT_DB = warehouse_store.DEFAULT_DB


def refresh_warehouse_oem(
    warehouse_id: int,
    oem: str,
    db_file: Path | str = DEFAULT_DB,
) -> WebsiteStockResult:
    warehouse_store.init_warehouse_db(db_file)
    warehouse = warehouse_store.get_warehouse(warehouse_id, db_file)
    if not warehouse or warehouse.get("deleted_at"):
        return WebsiteStockResult(
            status="check_failed",
            details={"error": "warehouse_not_found"},
        )

    source = warehouse_store.get_source(warehouse_id, "website", db_file)
    if not source or not source.get("enabled"):
        return WebsiteStockResult(
            status="check_failed",
            details={"error": "website_source_disabled"},
        )

    adapter_type = str(warehouse.get("adapter_type") or "").strip()
    adapter = get_adapter(adapter_type)
    if adapter is None:
        result = WebsiteStockResult(
            status="check_failed",
            details={
                "error": "adapter_not_configured",
                "adapter_type": adapter_type,
            },
        )
    else:
        result = adapter.lookup(oem)

    source_record = {
        "adapter_type": adapter_type,
        "url": result.url,
        "title": result.title,
        "details": result.details,
    }
    error = None
    if result.status == "check_failed":
        error = str(result.details.get("error") or "check_failed")

    warehouse_store.record_source_stock(
        warehouse_id=warehouse_id,
        oem=oem,
        status=result.status,
        quantity=result.quantity,
        source_type="website",
        name=result.title,
        source_record=source_record,
        error=error,
        db_file=db_file,
    )
    return result


def client_stock_summary(
    oem: str,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict]:
    """Return only client-safe warehouse stock fields."""
    result = []
    for warehouse in warehouse_store.list_warehouses(
        include_inactive=False,
        db_file=db_file,
    ):
        stock = stock_engine.get_available_stock(
            int(warehouse["id"]),
            oem,
            db_file,
        )
        result.append({
            "warehouse_id": int(warehouse["id"]),
            "public_name": warehouse["public_name"],
            "status": stock.get("status"),
            "available_quantity": stock.get("available_quantity"),
            "is_fresh": bool(stock.get("is_fresh")),
        })
    return result


def refresh_all_active_warehouses(
    oem: str,
    db_file: Path | str = DEFAULT_DB,
) -> list[tuple[dict, WebsiteStockResult]]:
    results = []
    for warehouse in warehouse_store.list_warehouses(
        include_inactive=False,
        db_file=db_file,
    ):
        source = warehouse_store.get_source(
            int(warehouse["id"]),
            "website",
            db_file,
        )
        if not source or not source.get("enabled"):
            continue
        result = refresh_warehouse_oem(
            int(warehouse["id"]),
            oem,
            db_file,
        )
        results.append((warehouse, result))
    return results
