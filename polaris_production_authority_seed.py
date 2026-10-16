"""Controlled production Category Authority seed for one accepted Polaris pair.

No automatic execution. Release Control must back up the target DB first and
invoke this module explicitly.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import dcp_category_authority

MANUFACTURER = "Polaris"
CURRENT_OEM = "2206328"
PREVIOUS_OEM = "2205250"
CATEGORY = "Parts"
MSRP_USD = 239.99
SOURCE_KIND = "validated_public_msrp"
SNAPSHOT_FILE = Path(__file__).with_name("dcp_public_msrp_cache.json")


def _db_path(db_path) -> Path:
    path = Path(str(db_path or "")).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise RuntimeError("AUTHORITY_DB_NOT_FOUND")
    return path


def _read_seed_snapshot(snapshot_path=SNAPSHOT_FILE) -> dict:
    payload = json.loads(Path(snapshot_path).read_text(encoding="utf-8"))
    entries = payload.get("entries") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        raise RuntimeError("AUTHORITY_SNAPSHOT_INVALID")
    matches = [
        e for e in entries
        if isinstance(e, dict)
        and e.get("manufacturer") == MANUFACTURER
        and e.get("oem") == CURRENT_OEM
    ]
    if len(matches) != 1:
        raise RuntimeError("AUTHORITY_SNAPSHOT_INVALID")
    entry = matches[0]
    if (
        entry.get("previous_oems") != [PREVIOUS_OEM]
        or entry.get("catalog") != CATEGORY
        or float(entry.get("msrp_usd") or 0) != MSRP_USD
        or entry.get("source_kind") != SOURCE_KIND
        or not str(entry.get("source_file") or "").strip()
        or not str(entry.get("captured_at") or "").strip()
    ):
        raise RuntimeError("AUTHORITY_SNAPSHOT_INVALID")
    return entry


def preflight(db_path) -> dict:
    path = _db_path(db_path)
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if not {"oem_catalog_cache", "oem_catalog_aliases"} <= tables:
            raise RuntimeError("AUTHORITY_TABLES_MISSING")

        direct_old = conn.execute(
            """SELECT catalog FROM oem_catalog_cache
                 WHERE manufacturer=? AND current_oem=?""",
            (MANUFACTURER, PREVIOUS_OEM),
        ).fetchall()
        if direct_old:
            raise RuntimeError("AUTHORITY_CONFLICT_OLD_DIRECT")

        current = conn.execute(
            """SELECT catalog FROM oem_catalog_cache
                 WHERE manufacturer=? AND current_oem=?""",
            (MANUFACTURER, CURRENT_OEM),
        ).fetchall()
        if current and (
            len(current) != 1
            or str(current[0][0] or "").strip() != CATEGORY
        ):
            raise RuntimeError("AUTHORITY_CONFLICT_CURRENT")

        previous_alias = conn.execute(
            """SELECT current_oem,alias_kind FROM oem_catalog_aliases
                 WHERE manufacturer=? AND alias_oem=?""",
            (MANUFACTURER, PREVIOUS_OEM),
        ).fetchall()
        if previous_alias and previous_alias != [(CURRENT_OEM, "previous")]:
            raise RuntimeError("AUTHORITY_CONFLICT_PREVIOUS_ALIAS")

        current_alias = conn.execute(
            """SELECT current_oem,alias_kind FROM oem_catalog_aliases
                 WHERE manufacturer=? AND alias_oem=?""",
            (MANUFACTURER, CURRENT_OEM),
        ).fetchall()
        if current_alias and current_alias != [(CURRENT_OEM, "current")]:
            raise RuntimeError("AUTHORITY_CONFLICT_CURRENT_ALIAS")

    return {
        "status": "READY",
        "manufacturer": MANUFACTURER,
        "previous_oem": PREVIOUS_OEM,
        "current_oem": CURRENT_OEM,
    }


def seed(db_path, *, snapshot_path=SNAPSHOT_FILE) -> dict:
    path = _db_path(db_path)
    preflight(path)
    entry = _read_seed_snapshot(snapshot_path)

    import extremizer_bot as core

    old_db = core.ORDERS_DB_FILE
    old_snapshot = core.PUBLIC_MSRP_CACHE_FILE
    try:
        core.ORDERS_DB_FILE = path
        # Keep the write explicit: do not let init_orders_db bootstrap the JSON
        # implicitly while this controlled seed is running.
        core.PUBLIC_MSRP_CACHE_FILE = path.with_name(
            "__release_control_no_implicit_public_snapshot__.json"
        )
        ok = core.upsert_oem_catalog_cache(
            {
                "status": "FOUND",
                "manufacturer": MANUFACTURER,
                "item_sku": CURRENT_OEM,
                "query_oem": PREVIOUS_OEM,
                "previous_oems": [PREVIOUS_OEM],
                "name": entry.get("name"),
                "catalog": CATEGORY,
            },
            MSRP_USD,
            source_kind=SOURCE_KIND,
            source_ref=str(entry["source_file"]),
            verified_at=str(entry["captured_at"]),
        )
    finally:
        core.ORDERS_DB_FILE = old_db
        core.PUBLIC_MSRP_CACHE_FILE = old_snapshot

    if ok is not True:
        raise RuntimeError("AUTHORITY_SEED_WRITE_FAILED")

    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True) as conn:
        current = dcp_category_authority.resolve(conn, MANUFACTURER, CURRENT_OEM)
        previous = dcp_category_authority.resolve(conn, MANUFACTURER, PREVIOUS_OEM)

    if not current or not previous:
        raise RuntimeError("AUTHORITY_SEED_VERIFY_FAILED")
    if (
        current.get("resolved_oem") != CURRENT_OEM
        or previous.get("resolved_oem") != CURRENT_OEM
        or current.get("item_category") != CATEGORY
        or previous.get("item_category") != CATEGORY
    ):
        raise RuntimeError("AUTHORITY_SEED_VERIFY_FAILED")

    return {
        "status": "SEEDED",
        "manufacturer": MANUFACTURER,
        "previous_oem": PREVIOUS_OEM,
        "current_oem": CURRENT_OEM,
        "item_category": CATEGORY,
        "source_kind": current.get("category_source_kind"),
    }
