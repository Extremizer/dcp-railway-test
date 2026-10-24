#!/usr/bin/env python3
"""EXTREMIZER — SM-08351 exact OEM, READ-ONLY warehouse state inspector.

No DB initialization, UPDATE, INSERT, migrations or product fetching.
Requires authorized access to a consistent local COPY/snapshot of orders DB,
never a write-enabled production connection. Outputs no customers/orders/PII.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from urllib.parse import quote, urlparse


def inspect(db_path: str, oem: str = "SM-08351") -> dict:
    if oem != "SM-08351":
        raise ValueError("ONLY_APPROVED_OEM_SM_08351")
    path = Path(db_path).resolve(strict=True)
    if not path.is_file():
        raise ValueError("DB_FILE_REQUIRED")
    # mode=ro is enforced by SQLite itself; no implicit DB creation.
    uri = "file:" + quote(path.as_posix(), safe="/:") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        tables = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        needed = {"warehouses", "warehouse_sources", "warehouse_stock_current"}
        if not needed <= tables:
            raise ValueError("WAREHOUSE_SCHEMA_INCOMPLETE")
        warehouses = conn.execute(
            """SELECT id, code, public_name, internal_name, city, adapter_type,
                      website_url, active, deleted_at, priority
               FROM warehouses ORDER BY priority, id"""
        ).fetchall()
        sources = conn.execute(
            """SELECT warehouse_id, source_type, enabled, priority, ttl_minutes,
                      last_checked_at, last_success_at, last_error
               FROM warehouse_sources ORDER BY warehouse_id, priority"""
        ).fetchall()
        stocks = conn.execute(
            """SELECT warehouse_id, source_type, oem, status, quantity, price_rub,
                      observed_at, expires_at
               FROM warehouse_stock_current WHERE oem=? ORDER BY warehouse_id, source_type""",
            (oem,),
        ).fetchall()
        reservations = []
        if "warehouse_stock_reservations" in tables:
            reservations = conn.execute(
                """SELECT warehouse_id, status, COUNT(*) AS rows,
                          SUM(quantity) AS quantity_total
                   FROM warehouse_stock_reservations
                   WHERE oem=? AND status IN ('hold','reserved','committed')
                   GROUP BY warehouse_id,status ORDER BY warehouse_id,status""",
                (oem,),
            ).fetchall()
    mapped = []
    for w in warehouses:
        wid = w["id"]
        mapped.append({
            "warehouse_id": wid,
            "code": w["code"],
            "public_name": w["public_name"],
            "internal_name": w["internal_name"],
            "city": w["city"],
            "adapter_type": w["adapter_type"],
            "website_host": urlparse(w["website_url"] or "").hostname,
            "active": bool(w["active"]) and w["deleted_at"] is None,
            "sources": [
                {"type": s["source_type"], "enabled": bool(s["enabled"]),
                 "priority": s["priority"], "ttl_minutes": s["ttl_minutes"],
                 "last_checked_at": s["last_checked_at"],
                 "last_success_at": s["last_success_at"],
                 "has_error": bool(s["last_error"])}
                for s in sources if s["warehouse_id"] == wid
            ],
            "sm08351_snapshots": [
                {"source_type": s["source_type"], "status": s["status"],
                 "quantity": s["quantity"], "price_rub": s["price_rub"],
                 "observed_at": s["observed_at"], "expires_at": s["expires_at"]}
                for s in stocks if s["warehouse_id"] == wid
            ],
            "active_reservations": [
                {"status": r["status"], "rows": r["rows"],
                 "quantity_total": r["quantity_total"]}
                for r in reservations if r["warehouse_id"] == wid
            ],
        })
    return {
        "status": "READ_ONLY_SNAPSHOT",
        "oem": oem,
        "report_scope": "warehouse names/source mapping/stock observations only",
        "warnings": [
            "Snapshot does NOT prove current seller-side stock.",
            "Do NOT infer physical supplier Склад3 equals public Склад 3.",
            "Stock snapshots do NOT subtract all reservation business rules.",
            "No production mutation or live refresh is performed.",
        ],
        "warehouses": mapped,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("db_snapshot", help="Authorized consistent COPY of orders database")
    parser.add_argument("--oem", default="SM-08351")
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.db_snapshot, args.oem), ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, "READ_ONLY_AUDIT_HOLD: " + type(exc).__name__ + ": " + str(exc) + "\n")
