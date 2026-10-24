#!/usr/bin/env python3
"""EXTREMIZER DCP/Probnik -- exact OEM read-only state audit, OFFLINE ONLY.

Accepts only an authorized CONSISTENT COPY of extremizer_orders.db.
No live DCP HTTP, no SQLite migrations, writes, requests or claims.
Never emits dollar amounts, tokens, request identifiers or owner details.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
from urllib.parse import quote

OEM_PAIRS = (("Ski-Doo", "417300574"), ("Arctic Cat", "0746-965"))
HEALTH_MAX_AGE_SECONDS = 5400
MAX_CACHE_HOURS = 24


def _date(value):
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _tables(conn):
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _cols(conn, name):
    return {r[1] for r in conn.execute("PRAGMA table_info(" + name + ")")}


def _snapshot(conn, manufacturer, oem, now, age_hours, health_enforced):
    tables = _tables(conn)
    value = {
        "manufacturer": manufacturer, "oem": oem,
        "cache": "SCHEMA_MISSING",
        "catalog_search_route": "UNVERIFIED",
        "live_admission": "UNKNOWN",
        "health": "UNKNOWN",
        "health_is_stale": None,
        "health_enforced": health_enforced,
        "coordination": "UNKNOWN",
        "recent_live_results": [],
        "blocker_candidates": [],
    }

    if "dealer_price_cache" in tables and {
        "manufacturer", "oem", "dealer_price_usd", "last_verified_at"
    } <= _cols(conn, "dealer_price_cache"):
        rows = conn.execute(
            "SELECT dealer_price_usd,last_verified_at FROM dealer_price_cache "
            "WHERE manufacturer=? AND oem=? ORDER BY last_verified_at DESC LIMIT 15",
            (manufacturer, oem),
        ).fetchall()
        if not rows:
            value["cache"] = "MISSING"
        else:
            fresh = False
            valid = False
            for price, stamp in rows:
                try:
                    numeric = float(price)
                    good = math.isfinite(numeric) and numeric > 0
                except (ValueError, TypeError):
                    good = False
                if not good:
                    continue
                valid = True
                verified = _date(stamp)
                if verified:
                    diff = max(0, (now - verified).total_seconds() / 3600)
                    if diff <= age_hours:
                        fresh = True
            value["cache"] = "FRESH" if fresh else "STALE" if valid else "INVALID"
    if value["cache"] != "FRESH":
        value["blocker_candidates"].append("NO_FRESH_VERIFIED_CACHE")
    if value["cache"] == "SCHEMA_MISSING":
        value["blocker_candidates"].append("PROBNIK_RETURN_BEFORE_LIVE_WHEN_CACHE_TABLE_MISSING")

    if {"oem_catalog_cache", "oem_catalog_aliases"} <= tables:
        try:
            import dcp_category_authority
            route = dcp_category_authority.resolve_search_route(conn, manufacturer, oem)
            if route:
                value["catalog_search_route"] = (
                    "VERIFIED_CATEGORY" if route["category_source_kind"]
                    != "explicit_manufacturer_parts_search" else "EXPLICIT_PARTS_ROUTE"
                )
        except (sqlite3.Error, KeyError, ValueError, ImportError):
            value["catalog_search_route"] = "UNVERIFIED"
    else:
        # Only explicit manufacturer Parts routes can exist without catalog authority.
        try:
            import dcp_category_authority
            route = dcp_category_authority.resolve_search_route(conn, manufacturer, oem)
            if route and route.get("category_source_kind") == "explicit_manufacturer_parts_search":
                value["catalog_search_route"] = "EXPLICIT_PARTS_ROUTE"
        except (sqlite3.Error, ImportError, ValueError):
            pass
    if value["catalog_search_route"] == "UNVERIFIED":
        value["blocker_candidates"].append("CATEGORY_AUTHORITY_UNVERIFIED")

    if "dp_live_admission" in tables:
        row = conn.execute("SELECT mode FROM dp_live_admission WHERE singleton_id=1").fetchone()
        value["live_admission"] = row[0] if row and row[0] in ("OPEN", "CLOSED") else "UNKNOWN"
    if value["live_admission"] != "OPEN":
        value["blocker_candidates"].append("LIVE_ADMISSION_NOT_CONFIRMED_OPEN")

    if "dp_live_health" in tables:
        row = conn.execute(
            "SELECT status,checked_at FROM dp_live_health WHERE singleton_id=1"
        ).fetchone()
        if row:
            raw = str(row[0] or "UNKNOWN").upper()
            checked = _date(row[1])
            stale = checked is None or (now - checked).total_seconds() > HEALTH_MAX_AGE_SECONDS
            value["health"] = raw if raw in (
                "READY", "CLOUDFLARE", "AUTH_REQUIRED", "BROWSER_DOWN",
                "TECHNICAL_ERROR", "UNKNOWN"
            ) else "TECHNICAL_ERROR"
            value["health_is_stale"] = stale
    if health_enforced is True and (
        value["health"] != "READY" or value["health_is_stale"] is not False
    ):
        value["blocker_candidates"].append("HEALTH_GATE_BLOCKING")
    if health_enforced is None and (
        value["health"] != "READY" or value["health_is_stale"] is not False
    ):
        value["blocker_candidates"].append("HEALTH_GATE_REQUIRES_ENV_CONFIRMATION")

    if "dp_live_coordination" in tables:
        row = conn.execute(
            "SELECT blocked_status FROM dp_live_coordination WHERE singleton_id=1"
        ).fetchone()
        value["coordination"] = (
            str(row[0]) if row and row[0] in ("CLOUDFLARE", "AUTH_REQUIRED") else
            "NOT_BLOCKED" if row and row[0] is None else "UNKNOWN"
        )
    if value["coordination"] not in ("NOT_BLOCKED",):
        value["blocker_candidates"].append("COORDINATION_NOT_CONFIRMED_CLEAR")

    if "dp_live_requests" in tables and {
        "manufacturer", "oem", "status", "result_status",
        "error_code", "requested_at"
    } <= _cols(conn, "dp_live_requests"):
        rows = conn.execute(
            "SELECT status,result_status,error_code FROM dp_live_requests "
            "WHERE manufacturer=? AND oem=? ORDER BY requested_at DESC LIMIT 5",
            (manufacturer, oem)
        ).fetchall()
        allowed = {"FOUND", "NO_PRICE", "TIMEOUT", "AUTH_REQUIRED", "CLOUDFLARE",
                   "TECHNICAL_ERROR", "CATEGORY_UNVERIFIED", "ADMISSION_CLOSED"}
        for status, result, error in rows:
            value["recent_live_results"].append({
                "request": status if status in {"pending", "claimed", "finished"} else "OTHER",
                "result": result if result in allowed else "OTHER_OR_NONE",
                "has_error_code": bool(error)
            })
        if any(x["request"] == "claimed" for x in value["recent_live_results"]):
            value["blocker_candidates"].append("ACTIVE_OR_UNRECONCILED_CLAIM_CHECK_REQUIRED")

    return value


def inspect_copy(path, now=None, cache_age_hours=24, health_enforced=None):
    if health_enforced not in (True, False, None):
        raise ValueError("INVALID_HEALTH_CONFIG")
    age = min(MAX_CACHE_HOURS, float(cache_age_hours))
    if not 0 < age <= MAX_CACHE_HOURS:
        raise ValueError("INVALID_CACHE_TTL")
    target = Path(path).resolve(strict=True)
    if not target.is_file():
        raise ValueError("FILE_REQUIRED")
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("TZ_AWARE_NOW_REQUIRED")
    # mode=ro is enforced by SQLite; query_only reinforces it.
    uri = "file:" + quote(target.as_posix(), safe="/:") + "?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=3) as conn:
        conn.execute("PRAGMA query_only=ON")
        rows = [
            _snapshot(conn, manufacturer, oem, now, age, health_enforced)
            for manufacturer, oem in OEM_PAIRS
        ]
    return {
        "status": "READ_ONLY_COPY_DIAGNOSTIC",
        "cache_ttl_hours_assumed": age,
        "health_enforced": health_enforced,
        "live_requests_created": False,
        "dcp_or_network_access": False,
        "prices_or_secrets_exported": False,
        "items": rows,
        "note": "No live runtime state asserted; observations reflect the supplied DB copy.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("consistent_db_copy")
    parser.add_argument("--cache-hours", type=float, default=24)
    parser.add_argument("--health-enforced", choices=["yes", "no", "unknown"], default="unknown")
    args = parser.parse_args()
    enforcement = {"yes": True, "no": False, "unknown": None}[args.health_enforced]
    try:
        print(json.dumps(inspect_copy(args.consistent_db_copy,
            cache_age_hours=args.cache_hours, health_enforced=enforcement),
            ensure_ascii=False, indent=2))
    except (ValueError, OSError, sqlite3.Error) as exc:
        parser.exit(2, "READ_ONLY_DCP_AUDIT_HOLD: " + type(exc).__name__ + "\n")
