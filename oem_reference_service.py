#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import shutil
import sqlite3
from pathlib import Path

BUNDLED_DB = Path(__file__).with_name("oem_reference.db")
DATA_DIR = Path(os.getenv("EXTREMIZER_DATA_DIR", "/data"))
DEFAULT_PERSISTENT_DB = DATA_DIR / "oem_reference.db"
DB_FILE = Path(
    os.getenv("EXTREMIZER_OEM_REFERENCE_DB_FILE", "").strip()
    or (
        DEFAULT_PERSISTENT_DB
        if DATA_DIR.exists() and DATA_DIR.is_dir()
        else BUNDLED_DB
    )
)

VALID_ITEM_TYPES = {"part", "accessory", "gear"}


def _db_has_reference_rows(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    conn = None
    try:
        conn = sqlite3.connect(path)
        row = conn.execute(
            "SELECT COUNT(*) FROM oem_reference"
        ).fetchone()
        return bool(row and int(row[0] or 0) > 0)
    except sqlite3.Error:
        return False
    finally:
        if conn is not None:
            conn.close()


def ensure_persistent_seed() -> dict:
    """Seed /data once from the bundled DB; never overwrite an existing DB."""
    target = DB_FILE.resolve()
    bundled = BUNDLED_DB.resolve()

    if target == bundled:
        return {
            "ok": _db_has_reference_rows(target),
            "mode": "bundled",
            "path": str(target),
            "seeded": False,
        }

    if _db_has_reference_rows(target):
        return {
            "ok": True,
            "mode": "persistent",
            "path": str(target),
            "seeded": False,
        }

    if target.exists():
        return {
            "ok": False,
            "mode": "persistent",
            "path": str(target),
            "seeded": False,
            "reason": "existing_invalid_db",
        }

    if not _db_has_reference_rows(bundled):
        return {
            "ok": False,
            "mode": "persistent",
            "path": str(target),
            "seeded": False,
            "reason": "bundled_seed_invalid",
        }

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + ".seed.tmp")
        if tmp.exists():
            tmp.unlink()
        shutil.copyfile(bundled, tmp)
        if not _db_has_reference_rows(tmp):
            tmp.unlink(missing_ok=True)
            return {
                "ok": False,
                "mode": "persistent",
                "path": str(target),
                "seeded": False,
                "reason": "seed_copy_invalid",
            }
        os.replace(tmp, target)
    except OSError as exc:
        return {
            "ok": False,
            "mode": "persistent",
            "path": str(target),
            "seeded": False,
            "reason": type(exc).__name__,
            "detail": str(exc),
        }

    return {
        "ok": True,
        "mode": "persistent",
        "path": str(target),
        "seeded": True,
    }


def normalize_oem(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\xa0", " ").strip()).upper()


def _connect_ro():
    seed = ensure_persistent_seed()
    if not seed.get("ok"):
        return None
    path = DB_FILE.resolve()
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def lookup_oem(oem: str | None) -> dict | None:
    normalized = normalize_oem(oem)
    if not normalized:
        return None

    conn = _connect_ro()
    if conn is None:
        return None

    try:
        row = conn.execute(
            """
            SELECT
                oem,
                item_type,
                actual_weight_kg,
                volume_weight_kg,
                weight_state,
                needs_review,
                type_conflict,
                actual_weight_conflict,
                volume_weight_conflict,
                direct_observation_count,
                linked_group_count
            FROM oem_reference
            WHERE oem = ?
            LIMIT 1
            """,
            (normalized,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return None

    (
        stored_oem,
        item_type,
        actual_weight_kg,
        volume_weight_kg,
        weight_state,
        needs_review,
        type_conflict,
        actual_weight_conflict,
        volume_weight_conflict,
        direct_observation_count,
        linked_group_count,
    ) = row

    safe_item_type = (
        str(item_type)
        if item_type in VALID_ITEM_TYPES and not bool(type_conflict)
        else None
    )
    safe_actual_weight = (
        float(actual_weight_kg)
        if str(weight_state or "") in {"ACTUAL_ONLY", "BOTH"}
        and actual_weight_kg is not None
        and float(actual_weight_kg) > 0
        and not bool(actual_weight_conflict)
        else None
    )
    safe_volume_weight = (
        float(volume_weight_kg)
        if str(weight_state or "") == "BOTH"
        and volume_weight_kg is not None
        and float(volume_weight_kg) > 0
        and not bool(volume_weight_conflict)
        else None
    )

    return {
        "oem": str(stored_oem),
        "item_type": safe_item_type,
        "actual_weight_kg": safe_actual_weight,
        "volume_weight_kg": safe_volume_weight,
        "weight_state": str(weight_state or ""),
        "needs_review": bool(needs_review),
        "type_conflict": bool(type_conflict),
        "actual_weight_conflict": bool(actual_weight_conflict),
        "volume_weight_conflict": bool(volume_weight_conflict),
        "direct_observation_count": int(direct_observation_count or 0),
        "linked_group_count": int(linked_group_count or 0),
    }


def lookup_first(*oems: str | None) -> dict | None:
    seen = set()
    for raw in oems:
        normalized = normalize_oem(raw)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        result = lookup_oem(normalized)
        if result:
            return result
    return None


def health() -> dict:
    seed = ensure_persistent_seed()
    path = DB_FILE.resolve()
    if not seed.get("ok"):
        return {
            "ok": False,
            "path": str(path),
            "reason": seed.get("reason") or "unreadable",
            "mode": seed.get("mode"),
        }

    conn = _connect_ro()
    if conn is None:
        return {
            "ok": False,
            "path": str(path),
            "reason": "unreadable",
            "mode": seed.get("mode"),
        }

    try:
        count = conn.execute("SELECT COUNT(*) FROM oem_reference").fetchone()[0]
        types = dict(
            conn.execute(
                """
                SELECT COALESCE(item_type, 'NULL'), COUNT(*)
                FROM oem_reference
                GROUP BY item_type
                """
            ).fetchall()
        )
    finally:
        conn.close()

    return {
        "ok": True,
        "path": str(path),
        "mode": seed.get("mode"),
        "seeded": bool(seed.get("seeded")),
        "unique_oem": int(count),
        "item_types": types,
    }
