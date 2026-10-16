"""Controlled one-shot Polaris Category Authority seed for Railway production.

No automatic execution. Release Control must back up the target DB first.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import dcp_category_authority

MANUFACTURER="Polaris"
CURRENT_OEM="2206328"
PREVIOUS_OEM="2205250"
CATEGORY="Parts"
MSRP_USD=239.99
SOURCE_KIND="validated_public_msrp"
SOURCE_REF="theme4_live_dcp_evidence_20261016"
VERIFIED_AT="2026-10-16T18:30:00+00:00"


def _db_path(db_path=None) -> Path:
    raw=str(db_path or os.getenv("EXTREMIZER_ORDERS_DB_FILE","")).strip()
    if not raw:
        raise RuntimeError("AUTHORITY_DB_PATH_REQUIRED")
    path=Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise RuntimeError("AUTHORITY_DB_NOT_FOUND")
    return path


def preflight(db_path=None) -> dict:
    path=_db_path(db_path)
    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True) as conn:
        tables={r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if not {"oem_catalog_cache","oem_catalog_aliases"} <= tables:
            raise RuntimeError("AUTHORITY_TABLES_MISSING")

        direct_old=conn.execute(
            """SELECT catalog FROM oem_catalog_cache
               WHERE manufacturer=? AND current_oem=?""",
            (MANUFACTURER,PREVIOUS_OEM),
        ).fetchall()
        if direct_old:
            raise RuntimeError("AUTHORITY_CONFLICT_OLD_DIRECT")

        current=conn.execute(
            """SELECT catalog FROM oem_catalog_cache
               WHERE manufacturer=? AND current_oem=?""",
            (MANUFACTURER,CURRENT_OEM),
        ).fetchall()
        if current and (
            len(current)!=1 or str(current[0][0] or "").strip()!=CATEGORY
        ):
            raise RuntimeError("AUTHORITY_CONFLICT_CURRENT")

        for alias_oem,want_kind in (
            (PREVIOUS_OEM,"previous"),
            (CURRENT_OEM,"current"),
        ):
            rows=conn.execute(
                """SELECT current_oem,alias_kind FROM oem_catalog_aliases
                   WHERE manufacturer=? AND alias_oem=?""",
                (MANUFACTURER,alias_oem),
            ).fetchall()
            if rows and rows != [(CURRENT_OEM,want_kind)]:
                raise RuntimeError("AUTHORITY_CONFLICT_ALIAS")

    return {"status":"READY","db":str(path)}


def seed(db_path=None) -> dict:
    path=_db_path(db_path)
    preflight(path)

    import extremizer_bot as core
    old_db=core.ORDERS_DB_FILE
    old_snapshot=core.PUBLIC_MSRP_CACHE_FILE
    try:
        core.ORDERS_DB_FILE=path
        core.PUBLIC_MSRP_CACHE_FILE=path.with_name(
            "__release_control_no_implicit_public_snapshot__.json"
        )
        ok=core.upsert_oem_catalog_cache(
            {
                "status":"FOUND",
                "manufacturer":MANUFACTURER,
                "item_sku":CURRENT_OEM,
                "query_oem":PREVIOUS_OEM,
                "previous_oems":[PREVIOUS_OEM],
                "name":"QUICKDRIVE BELT KIT",
                "catalog":CATEGORY,
            },
            MSRP_USD,
            source_kind=SOURCE_KIND,
            source_ref=SOURCE_REF,
            verified_at=VERIFIED_AT,
        )
    finally:
        core.ORDERS_DB_FILE=old_db
        core.PUBLIC_MSRP_CACHE_FILE=old_snapshot

    if ok is not True:
        raise RuntimeError("AUTHORITY_SEED_WRITE_FAILED")

    with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True) as conn:
        current=dcp_category_authority.resolve(conn,MANUFACTURER,CURRENT_OEM)
        previous=dcp_category_authority.resolve(conn,MANUFACTURER,PREVIOUS_OEM)
    if not current or not previous:
        raise RuntimeError("AUTHORITY_SEED_VERIFY_FAILED")
    if (
        current.get("resolved_oem")!=CURRENT_OEM
        or previous.get("resolved_oem")!=CURRENT_OEM
        or current.get("item_category")!=CATEGORY
        or previous.get("item_category")!=CATEGORY
    ):
        raise RuntimeError("AUTHORITY_SEED_VERIFY_FAILED")

    return {
        "status":"SEEDED",
        "manufacturer":MANUFACTURER,
        "previous_oem":PREVIOUS_OEM,
        "current_oem":CURRENT_OEM,
        "item_category":CATEGORY,
    }


def main():
    result=seed()
    print(
        f"{result['status']} {result['manufacturer']} "
        f"{result['previous_oem']}->{result['current_oem']} "
        f"{result['item_category']}"
    )


if __name__=="__main__":
    main()
