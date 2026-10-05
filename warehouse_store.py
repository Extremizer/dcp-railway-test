#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Warehouse / stock source persistence for Extremizer Pro."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

DEFAULT_DB = Path(__file__).with_name("extremizer_orders.db")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def init_warehouse_db(db_file: Path | str = DEFAULT_DB) -> None:
    """Create warehouse, source, import and stock-observation tables."""
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                internal_name TEXT NOT NULL UNIQUE,
                city TEXT NOT NULL,
                code TEXT NOT NULL UNIQUE,
                public_name TEXT NOT NULL UNIQUE,
                website_url TEXT,
                adapter_type TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 100,
                notes TEXT,
                last_checked_at TEXT,
                last_success_at TEXT,
                last_error TEXT,
                deleted_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL,
                source_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                priority INTEGER NOT NULL DEFAULT 100,
                ttl_minutes INTEGER,
                config_json TEXT,
                last_checked_at TEXT,
                last_success_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
                UNIQUE(warehouse_id, source_type)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_mapping_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL,
                profile_name TEXT NOT NULL,
                file_format TEXT,
                sheet_name TEXT,
                mapping_json TEXT NOT NULL,
                import_mode TEXT NOT NULL DEFAULT 'full_snapshot',
                missing_oem_policy TEXT NOT NULL DEFAULT 'unknown',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
            )
        """)
        mapping_profile_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(warehouse_mapping_profiles)"
            ).fetchall()
        }
        if "header_signature" not in mapping_profile_columns:
            conn.execute(
                "ALTER TABLE warehouse_mapping_profiles "
                "ADD COLUMN header_signature TEXT"
            )
        if "header_row" not in mapping_profile_columns:
            conn.execute(
                "ALTER TABLE warehouse_mapping_profiles "
                "ADD COLUMN header_row INTEGER"
            )
        if "last_used_at" not in mapping_profile_columns:
            conn.execute(
                "ALTER TABLE warehouse_mapping_profiles "
                "ADD COLUMN last_used_at TEXT"
            )
        if "use_count" not in mapping_profile_columns:
            conn.execute(
                "ALTER TABLE warehouse_mapping_profiles "
                "ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0"
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL,
                filename TEXT NOT NULL,
                file_format TEXT,
                imported_at TEXT NOT NULL,
                rows_total INTEGER NOT NULL DEFAULT 0,
                rows_success INTEGER NOT NULL DEFAULT 0,
                rows_error INTEGER NOT NULL DEFAULT 0,
                import_mode TEXT NOT NULL DEFAULT 'full_snapshot',
                mapping_json TEXT,
                missing_oem_policy TEXT NOT NULL DEFAULT 'unknown',
                status TEXT NOT NULL,
                notes TEXT,
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
            )
        """)
        import_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(warehouse_imports)"
            ).fetchall()
        }
        if "duplicate_oem_policy" not in import_columns:
            conn.execute(
                "ALTER TABLE warehouse_imports "
                "ADD COLUMN duplicate_oem_policy TEXT "
                "NOT NULL DEFAULT 'sum'"
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_stock_observations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL,
                manufacturer TEXT,
                oem TEXT NOT NULL,
                quantity REAL,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                expires_at TEXT,
                import_id INTEGER,
                source_record TEXT,
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
                FOREIGN KEY (import_id) REFERENCES warehouse_imports(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_stock_current (
                warehouse_id INTEGER NOT NULL,
                manufacturer TEXT,
                oem TEXT NOT NULL,
                name TEXT,
                quantity REAL,
                status TEXT NOT NULL,
                source_type TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                expires_at TEXT,
                import_id INTEGER,
                source_record TEXT,
                PRIMARY KEY (warehouse_id, oem, source_type),
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id),
                FOREIGN KEY (import_id) REFERENCES warehouse_imports(id)
            )
        """)
        stock_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(warehouse_stock_current)").fetchall()
        }
        if "price_rub" not in stock_columns:
            conn.execute(
                "ALTER TABLE warehouse_stock_current "
                "ADD COLUMN price_rub REAL"
            )

        warehouse_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(warehouses)").fetchall()
        }
        if "stock_sync_mode" not in warehouse_columns:
            conn.execute(
                "ALTER TABLE warehouses "
                "ADD COLUMN stock_sync_mode TEXT NOT NULL DEFAULT 'manual'"
            )

        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_stock_reservations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                warehouse_id INTEGER NOT NULL,
                order_id TEXT,
                order_item_id INTEGER,
                manufacturer TEXT,
                oem TEXT NOT NULL,
                quantity REAL NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                expires_at TEXT,
                confirmed_at TEXT,
                committed_at TEXT,
                released_at TEXT,
                absorbed_at TEXT,
                notes TEXT,
                FOREIGN KEY (warehouse_id) REFERENCES warehouses(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS warehouse_stock_reservation_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reservation_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                quantity REAL,
                created_at TEXT NOT NULL,
                details_json TEXT,
                FOREIGN KEY (reservation_id)
                    REFERENCES warehouse_stock_reservations(id)
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_warehouses_active_priority
            ON warehouses(active, priority)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_warehouse_stock_lookup
            ON warehouse_stock_observations(warehouse_id, oem, observed_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_warehouse_imports_warehouse
            ON warehouse_imports(warehouse_id, imported_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_mapping_profiles_lookup
            ON warehouse_mapping_profiles(
                warehouse_id, file_format, header_signature, active
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_warehouse_stock_current_lookup
            ON warehouse_stock_current(warehouse_id, oem, source_type)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_reservations_lookup
            ON warehouse_stock_reservations(
                warehouse_id, oem, status, created_at
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_reservations_order
            ON warehouse_stock_reservations(order_id, order_item_id)
        """)
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_unique_active_item_reservation
            ON warehouse_stock_reservations(order_item_id)
            WHERE order_item_id IS NOT NULL
              AND status IN ('hold', 'reserved', 'committed', 'absorbed')
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_stock_reservation_events
            ON warehouse_stock_reservation_events(reservation_id, created_at)
        """)

        manual_policy_marker = _json({
            "manual_ttl_policy_version": 1,
        })
        conn.execute(
            """
            UPDATE warehouse_sources
            SET ttl_minutes = 60,
                config_json = ?,
                updated_at = ?
            WHERE source_type = 'manual'
              AND ttl_minutes IS NULL
              AND (
                    config_json IS NULL
                    OR TRIM(config_json) = ''
                  )
            """,
            (manual_policy_marker, _now()),
        )
        # Old production rows can already have config_json but still carry
        # ttl_minutes=NULL. Manual stock must never live forever silently.
        conn.execute(
            """
            UPDATE warehouse_sources
            SET ttl_minutes = 60,
                updated_at = ?
            WHERE source_type = 'manual'
              AND ttl_minutes IS NULL
            """,
            (_now(),),
        )
        conn.commit()


def add_warehouse(
    internal_name: str,
    city: str,
    code: str,
    public_name: str,
    website_url: str | None = None,
    adapter_type: str | None = None,
    priority: int = 100,
    notes: str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int:
    now = _now()
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO warehouses (
                internal_name, city, code, public_name, website_url,
                adapter_type, active, priority, notes, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                internal_name.strip(), city.strip(), code.strip().upper(),
                public_name.strip(), (website_url or "").strip() or None,
                (adapter_type or "").strip() or None, int(priority),
                (notes or "").strip() or None, now, now,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def ensure_source(
    warehouse_id: int,
    source_type: str,
    enabled: bool = True,
    priority: int = 100,
    ttl_minutes: int | None = None,
    config: dict[str, Any] | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> None:
    now = _now()
    with sqlite3.connect(db_file) as conn:
        conn.execute(
            """
            INSERT INTO warehouse_sources (
                warehouse_id, source_type, enabled, priority, ttl_minutes,
                config_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(warehouse_id, source_type) DO UPDATE SET
                enabled=excluded.enabled,
                priority=excluded.priority,
                ttl_minutes=excluded.ttl_minutes,
                config_json=excluded.config_json,
                updated_at=excluded.updated_at
            """,
            (
                warehouse_id, source_type, int(enabled), int(priority),
                ttl_minutes, _json(config) if config is not None else None,
                now, now,
            ),
        )
        conn.commit()


def list_warehouses(
    include_inactive: bool = True,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    where = "deleted_at IS NULL"
    if not include_inactive:
        where += " AND active = 1"
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT *
            FROM warehouses
            WHERE {where}
            ORDER BY priority, city, internal_name
            """
        ).fetchall()
    return [dict(row) for row in rows]


def get_warehouse(
    warehouse_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any] | None:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM warehouses WHERE id = ?",
            (warehouse_id,),
        ).fetchone()
    return dict(row) if row else None


def get_sources(
    warehouse_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM warehouse_sources
            WHERE warehouse_id = ?
            ORDER BY priority, source_type
            """,
            (warehouse_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def update_warehouse_field(
    warehouse_id: int,
    field: str,
    value: str | int | None,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    allowed = {
        "internal_name",
        "city",
        "code",
        "public_name",
        "website_url",
        "adapter_type",
        "priority",
        "notes",
    }
    if field not in allowed:
        return False

    if field == "priority":
        value = int(value) if value is not None else 100
    elif field == "code":
        value = str(value or "").strip().upper()
    elif isinstance(value, str):
        value = value.strip() or None

    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            f"UPDATE warehouses SET {field} = ?, updated_at = ? WHERE id = ?",
            (value, _now(), warehouse_id),
        )
        conn.commit()
        return cur.rowcount == 1


def create_import(
    warehouse_id: int,
    filename: str,
    file_format: str | None,
    rows_total: int,
    rows_success: int,
    rows_error: int,
    import_mode: str,
    mapping: dict[str, Any] | None,
    missing_oem_policy: str,
    status: str,
    duplicate_oem_policy: str = "sum",
    notes: str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int:
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO warehouse_imports (
                warehouse_id, filename, file_format, imported_at,
                rows_total, rows_success, rows_error, import_mode,
                mapping_json, missing_oem_policy, status, notes,
                duplicate_oem_policy
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                warehouse_id,
                filename,
                file_format,
                _now(),
                int(rows_total),
                int(rows_success),
                int(rows_error),
                import_mode,
                _json(mapping) if mapping else None,
                missing_oem_policy,
                status,
                notes,
                duplicate_oem_policy,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def warehouse_health_summary(
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    init_warehouse_db(db_file)
    now = datetime.now().astimezone()

    warehouses = list_warehouses(
        include_inactive=True,
        db_file=db_file,
    )
    result: list[dict[str, Any]] = []

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row

        for warehouse in warehouses:
            warehouse_id = int(warehouse["id"])

            source_rows = conn.execute(
                """
                SELECT *
                FROM warehouse_sources
                WHERE warehouse_id = ?
                ORDER BY priority, source_type
                """,
                (warehouse_id,),
            ).fetchall()

            stock_rows = conn.execute(
                """
                SELECT source_type, quantity, expires_at
                FROM warehouse_stock_current
                WHERE warehouse_id = ?
                """,
                (warehouse_id,),
            ).fetchall()

            source_counts: dict[str, dict[str, int]] = {}
            for row in stock_rows:
                source_type = str(row["source_type"] or "")
                bucket = source_counts.setdefault(
                    source_type,
                    {
                        "total": 0,
                        "positive": 0,
                        "fresh": 0,
                    },
                )
                bucket["total"] += 1
                if (
                    row["quantity"] is not None
                    and float(row["quantity"]) > 0
                ):
                    bucket["positive"] += 1

                expires_at = row["expires_at"]
                is_fresh = False
                if not expires_at:
                    is_fresh = True
                else:
                    try:
                        is_fresh = (
                            datetime.fromisoformat(
                                str(expires_at)
                            ) > now
                        )
                    except ValueError:
                        is_fresh = False
                if is_fresh:
                    bucket["fresh"] += 1

            reservations = conn.execute(
                """
                SELECT
                    COUNT(*) AS reservation_count,
                    COALESCE(SUM(quantity), 0) AS reserved_quantity
                FROM warehouse_stock_reservations
                WHERE warehouse_id = ?
                  AND status IN ('hold', 'reserved', 'committed')
                """,
                (warehouse_id,),
            ).fetchone()

            result.append({
                "warehouse": warehouse,
                "sources": [
                    dict(row)
                    for row in source_rows
                ],
                "stock_counts": source_counts,
                "reservation_count": int(
                    reservations["reservation_count"] or 0
                ),
                "reserved_quantity": float(
                    reservations["reserved_quantity"] or 0
                ),
            })

    return result


def list_recent_imports(
    warehouse_id: int,
    limit: int = 20,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM warehouse_imports
            WHERE warehouse_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (
                warehouse_id,
                max(1, min(int(limit), 100)),
            ),
        ).fetchall()
    return [dict(row) for row in rows]


def latest_import(
    warehouse_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any] | None:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM warehouse_imports
            WHERE warehouse_id = ?
            ORDER BY imported_at DESC, id DESC
            LIMIT 1
            """,
            (warehouse_id,),
        ).fetchone()
    return dict(row) if row else None


def update_source_field(
    warehouse_id: int,
    source_type: str,
    field: str,
    value: int | None,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    allowed = {"enabled", "priority", "ttl_minutes"}
    if field not in allowed:
        return False

    if field in {"enabled", "priority"}:
        if value is None:
            return False
        value = int(value)
    elif field == "ttl_minutes" and value is not None:
        value = int(value)

    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            f"""
            UPDATE warehouse_sources
            SET {field} = ?, updated_at = ?
            WHERE warehouse_id = ? AND source_type = ?
            """,
            (value, _now(), warehouse_id, source_type),
        )
        conn.commit()
        return cur.rowcount == 1


def get_source_config(
    warehouse_id: int,
    source_type: str,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any]:
    source = get_source(
        warehouse_id,
        source_type,
        db_file,
    )
    if not source:
        return {}
    try:
        value = json.loads(source.get("config_json") or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def update_source_config(
    warehouse_id: int,
    source_type: str,
    updates: dict[str, Any],
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    init_warehouse_db(db_file)
    config = get_source_config(
        warehouse_id,
        source_type,
        db_file,
    )
    config.update(updates)
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            UPDATE warehouse_sources
            SET config_json = ?, updated_at = ?
            WHERE warehouse_id = ? AND source_type = ?
            """,
            (
                _json(config),
                _now(),
                warehouse_id,
                source_type,
            ),
        )
        conn.commit()
        return cur.rowcount == 1


def get_source(
    warehouse_id: int,
    source_type: str,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any] | None:
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM warehouse_sources
            WHERE warehouse_id = ? AND source_type = ?
            """,
            (warehouse_id, source_type),
        ).fetchone()
    return dict(row) if row else None


def find_mapping_profile(
    warehouse_id: int,
    file_format: str,
    header_signature: str,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any] | None:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM warehouse_mapping_profiles
            WHERE warehouse_id = ?
              AND active = 1
              AND file_format = ?
              AND header_signature = ?
            ORDER BY last_used_at DESC, updated_at DESC, id DESC
            LIMIT 1
            """,
            (
                warehouse_id,
                file_format.strip().lower(),
                header_signature,
            ),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["mapping"] = json.loads(
            result.get("mapping_json") or "{}"
        )
    except json.JSONDecodeError:
        result["mapping"] = {}
    return result


def save_mapping_profile(
    warehouse_id: int,
    file_format: str,
    sheet_name: str | None,
    header_signature: str,
    header_row: int,
    mapping: dict[str, int],
    import_mode: str = "full_snapshot",
    missing_oem_policy: str = "unknown",
    db_file: Path | str = DEFAULT_DB,
) -> int:
    init_warehouse_db(db_file)
    now = _now()
    file_format = file_format.strip().lower()
    profile_name = (
        f"{file_format.upper()} · {header_signature[:8]}"
    )
    mapping_json = _json(mapping)

    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            """
            SELECT id, use_count
            FROM warehouse_mapping_profiles
            WHERE warehouse_id = ?
              AND active = 1
              AND file_format = ?
              AND header_signature = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (warehouse_id, file_format, header_signature),
        ).fetchone()

        if row:
            profile_id = int(row[0])
            conn.execute(
                """
                UPDATE warehouse_mapping_profiles
                SET profile_name = ?,
                    sheet_name = ?,
                    mapping_json = ?,
                    import_mode = ?,
                    missing_oem_policy = ?,
                    header_row = ?,
                    last_used_at = ?,
                    use_count = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    profile_name,
                    sheet_name,
                    mapping_json,
                    import_mode,
                    missing_oem_policy,
                    int(header_row),
                    now,
                    int(row[1] or 0) + 1,
                    now,
                    profile_id,
                ),
            )
        else:
            cur = conn.execute(
                """
                INSERT INTO warehouse_mapping_profiles (
                    warehouse_id,
                    profile_name,
                    file_format,
                    sheet_name,
                    mapping_json,
                    import_mode,
                    missing_oem_policy,
                    active,
                    created_at,
                    updated_at,
                    header_signature,
                    header_row,
                    last_used_at,
                    use_count
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, 1)
                """,
                (
                    warehouse_id,
                    profile_name,
                    file_format,
                    sheet_name,
                    mapping_json,
                    import_mode,
                    missing_oem_policy,
                    now,
                    now,
                    header_signature,
                    int(header_row),
                    now,
                ),
            )
            profile_id = int(cur.lastrowid)

        conn.commit()
        return profile_id


def list_mapping_profiles(
    warehouse_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM warehouse_mapping_profiles
            WHERE warehouse_id = ?
              AND active = 1
            ORDER BY last_used_at DESC, updated_at DESC, id DESC
            """,
            (warehouse_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_mapping_profile(
    warehouse_id: int,
    profile_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any] | None:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT *
            FROM warehouse_mapping_profiles
            WHERE warehouse_id = ?
              AND id = ?
            """,
            (warehouse_id, profile_id),
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["mapping"] = json.loads(
            result.get("mapping_json") or "{}"
        )
    except json.JSONDecodeError:
        result["mapping"] = {}
    return result


def deactivate_mapping_profile(
    warehouse_id: int,
    profile_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            UPDATE warehouse_mapping_profiles
            SET active = 0,
                updated_at = ?
            WHERE warehouse_id = ?
              AND id = ?
              AND active = 1
            """,
            (_now(), warehouse_id, profile_id),
        )
        conn.commit()
        return cur.rowcount == 1


def import_prepared_stock(
    warehouse_id: int,
    filename: str,
    file_format: str,
    items: list[dict[str, Any]],
    rows_total: int,
    rows_error: int,
    mapping: dict[str, Any],
    import_mode: str = "full_snapshot",
    missing_oem_policy: str = "unknown",
    duplicate_oem_policy: str = "sum",
    db_file: Path | str = DEFAULT_DB,
) -> int:
    import_mode = str(import_mode or "").strip().lower()
    missing_oem_policy = str(
        missing_oem_policy or ""
    ).strip().lower()

    if import_mode not in {"full_snapshot", "delta"}:
        raise ValueError("Некорректный режим импорта.")
    if missing_oem_policy not in {"unknown", "zero"}:
        raise ValueError(
            "Некорректное правило отсутствующего OEM."
        )
    duplicate_oem_policy = str(
        duplicate_oem_policy or ""
    ).strip().lower()
    if duplicate_oem_policy not in {
        "sum", "max", "last", "reject"
    }:
        raise ValueError(
            "Некорректная политика повторяющихся OEM."
        )

    source = get_source(warehouse_id, "file", db_file)
    ttl_minutes = source.get("ttl_minutes") if source else 240

    observed = datetime.now().astimezone()
    expires = (
        observed + timedelta(minutes=int(ttl_minutes))
        if ttl_minutes is not None
        else None
    )
    observed_text = observed.isoformat(timespec="seconds")
    expires_text = expires.isoformat(timespec="seconds") if expires else None

    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO warehouse_imports (
                warehouse_id, filename, file_format, imported_at,
                rows_total, rows_success, rows_error, import_mode,
                mapping_json, missing_oem_policy, status, notes,
                duplicate_oem_policy
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'success', NULL, ?
            )
            """,
            (
                warehouse_id,
                filename,
                file_format,
                observed_text,
                int(rows_total),
                len(items),
                int(rows_error),
                import_mode,
                _json(mapping),
                missing_oem_policy,
                duplicate_oem_policy,
            ),
        )
        import_id = int(cur.lastrowid)

        previous_file_rows = []
        if import_mode == "full_snapshot":
            previous_file_rows = conn.execute(
                """
                SELECT manufacturer, oem, name
                FROM warehouse_stock_current
                WHERE warehouse_id = ?
                  AND source_type = 'file'
                """,
                (warehouse_id,),
            ).fetchall()

            conn.execute(
                """
                DELETE FROM warehouse_stock_current
                WHERE warehouse_id = ? AND source_type = 'file'
                """,
                (warehouse_id,),
            )

        history_rows = []
        current_rows = []
        incoming_oems = {
            str(item["oem"]).strip()
            for item in items
        }

        for item in items:
            quantity = float(item["quantity"])
            status = "in_stock" if quantity > 0 else "out_of_stock"
            source_record = item.get("source_record")
            if isinstance(source_record, dict):
                source_record = _json(source_record)

            common = (
                warehouse_id,
                item.get("manufacturer"),
                str(item["oem"]).strip(),
                quantity,
                status,
                "file",
                observed_text,
                expires_text,
                import_id,
                source_record,
            )
            history_rows.append(common)

            current_rows.append((
                warehouse_id,
                item.get("manufacturer"),
                str(item["oem"]).strip(),
                item.get("name"),
                item.get("price_rub"),
                quantity,
                status,
                "file",
                observed_text,
                expires_text,
                import_id,
                source_record,
            ))

        if (
            import_mode == "full_snapshot"
            and missing_oem_policy == "zero"
        ):
            for manufacturer, oem, name in previous_file_rows:
                normalized_oem = str(oem).strip()
                if normalized_oem in incoming_oems:
                    continue

                inferred_record = _json({
                    "inferred": "missing_from_full_snapshot",
                    "filename": filename,
                })
                history_rows.append((
                    warehouse_id,
                    manufacturer,
                    normalized_oem,
                    0.0,
                    "out_of_stock",
                    "file",
                    observed_text,
                    expires_text,
                    import_id,
                    inferred_record,
                ))
                current_rows.append((
                    warehouse_id,
                    manufacturer,
                    normalized_oem,
                    name,
                    None,
                    0.0,
                    "out_of_stock",
                    "file",
                    observed_text,
                    expires_text,
                    import_id,
                    inferred_record,
                ))

        conn.executemany(
            """
            INSERT INTO warehouse_stock_observations (
                warehouse_id, manufacturer, oem, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            history_rows,
        )

        conn.executemany(
            """
            INSERT INTO warehouse_stock_current (
                warehouse_id, manufacturer, oem, name, price_rub, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(warehouse_id, oem, source_type) DO UPDATE SET
                manufacturer=excluded.manufacturer,
                name=excluded.name,
                price_rub=excluded.price_rub,
                quantity=excluded.quantity,
                status=excluded.status,
                observed_at=excluded.observed_at,
                expires_at=excluded.expires_at,
                import_id=excluded.import_id,
                source_record=excluded.source_record
            """,
            current_rows,
        )

        conn.execute(
            """
            UPDATE warehouse_sources
            SET enabled = 1,
                last_checked_at = ?,
                last_success_at = ?,
                last_error = NULL,
                updated_at = ?
            WHERE warehouse_id = ? AND source_type = 'file'
            """,
            (observed_text, observed_text, observed_text, warehouse_id),
        )
        _absorb_committed_after_snapshot(
            conn,
            warehouse_id,
            observed_text,
            {str(row[2]).strip() for row in current_rows if str(row[2]).strip()},
        )
        conn.commit()
        return import_id


def count_current_stock(
    warehouse_id: int,
    source_type: str = "file",
    db_file: Path | str = DEFAULT_DB,
) -> tuple[int, int]:
    with sqlite3.connect(db_file) as conn:
        total, positive = conn.execute(
            """
            SELECT
                COUNT(*),
                SUM(CASE WHEN quantity > 0 THEN 1 ELSE 0 END)
            FROM warehouse_stock_current
            WHERE warehouse_id = ? AND source_type = ?
            """,
            (warehouse_id, source_type),
        ).fetchone()
    return int(total or 0), int(positive or 0)


def set_warehouse_active(
    warehouse_id: int,
    active: bool,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            "UPDATE warehouses SET active = ?, updated_at = ? WHERE id = ?",
            (int(active), _now(), warehouse_id),
        )
        conn.commit()
        return cur.rowcount == 1


def soft_delete_warehouse(
    warehouse_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    now = _now()
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            UPDATE warehouses
            SET active = 0, deleted_at = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (now, now, warehouse_id),
        )
        conn.commit()
        return cur.rowcount == 1


def add_stock_observation(
    warehouse_id: int,
    oem: str,
    quantity: float | None,
    status: str,
    source_type: str,
    manufacturer: str | None = None,
    ttl_minutes: int | None = None,
    import_id: int | None = None,
    source_record: dict[str, Any] | str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int:
    observed = datetime.now().astimezone()
    expires = (
        observed + timedelta(minutes=int(ttl_minutes))
        if ttl_minutes is not None else None
    )
    if isinstance(source_record, dict):
        source_record = _json(source_record)
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO warehouse_stock_observations (
                warehouse_id, manufacturer, oem, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                warehouse_id, manufacturer, oem.strip(), quantity, status,
                source_type, observed.isoformat(timespec="seconds"),
                expires.isoformat(timespec="seconds") if expires else None,
                import_id, source_record,
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


def clear_current_source_stock(
    warehouse_id: int,
    oem: str,
    source_type: str,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            DELETE FROM warehouse_stock_current
            WHERE warehouse_id = ?
              AND oem = ?
              AND source_type = ?
            """,
            (warehouse_id, oem.strip(), source_type),
        )
        conn.commit()
        return cur.rowcount > 0


def list_recent_stock_observations(
    warehouse_id: int,
    limit: int = 20,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id,
                warehouse_id,
                manufacturer,
                oem,
                quantity,
                status,
                source_type,
                observed_at,
                expires_at,
                import_id
            FROM warehouse_stock_observations
            WHERE warehouse_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (warehouse_id, max(1, min(int(limit), 100))),
        ).fetchall()
    return [dict(row) for row in rows]


def _absorb_committed_after_snapshot(
    conn: sqlite3.Connection,
    warehouse_id: int,
    observed_at: str,
    oems: list[str] | set[str] | tuple[str, ...] | None = None,
) -> int:
    """Stop double-subtracting committed local reservations after a new snapshot."""
    mode = conn.execute(
        "SELECT stock_sync_mode FROM warehouses WHERE id = ?",
        (warehouse_id,),
    ).fetchone()
    if not mode or str(mode[0] or "") != "snapshot_absorbs_committed":
        return 0

    normalized = sorted({
        str(oem or "").strip()
        for oem in (oems or [])
        if str(oem or "").strip()
    })
    params: list[Any] = [warehouse_id, observed_at]
    oem_clause = ""
    if normalized:
        placeholders = ",".join("?" for _ in normalized)
        oem_clause = f" AND oem IN ({placeholders})"
        params.extend(normalized)

    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        f"""
        SELECT id, quantity
        FROM warehouse_stock_reservations
        WHERE warehouse_id = ?
          AND status = 'committed'
          AND committed_at IS NOT NULL
          AND committed_at <= ?
          {oem_clause}
        """,
        params,
    ).fetchall()

    if not rows:
        return 0

    now = _now()
    for row in rows:
        conn.execute(
            """
            UPDATE warehouse_stock_reservations
            SET status = 'absorbed',
                absorbed_at = ?
            WHERE id = ?
            """,
            (now, int(row["id"])),
        )
        conn.execute(
            """
            INSERT INTO warehouse_stock_reservation_events (
                reservation_id, event_type, quantity, created_at, details_json
            ) VALUES (?, 'absorbed_by_snapshot', ?, ?, ?)
            """,
            (
                int(row["id"]),
                float(row["quantity"]),
                now,
                _json({"snapshot_observed_at": observed_at}),
            ),
        )
    return len(rows)


def record_source_stock(
    warehouse_id: int,
    oem: str,
    status: str,
    quantity: float | None,
    source_type: str,
    manufacturer: str | None = None,
    name: str | None = None,
    price_rub: float | None = None,
    source_record: dict[str, Any] | str | None = None,
    error: str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int | None:
    init_warehouse_db(db_file)
    observed = datetime.now().astimezone()
    observed_text = observed.isoformat(timespec="seconds")

    source = get_source(warehouse_id, source_type, db_file)
    ttl_minutes = source.get("ttl_minutes") if source else None
    expires = (
        observed + timedelta(minutes=int(ttl_minutes))
        if ttl_minutes is not None
        else None
    )
    expires_text = expires.isoformat(timespec="seconds") if expires else None

    if isinstance(source_record, dict):
        source_record = _json(source_record)

    with sqlite3.connect(db_file) as conn:
        if status == "check_failed":
            conn.execute(
                """
                UPDATE warehouse_sources
                SET last_checked_at = ?, last_error = ?, updated_at = ?
                WHERE warehouse_id = ? AND source_type = ?
                """,
                (observed_text, error or "check_failed", observed_text, warehouse_id, source_type),
            )
            conn.execute(
                """
                UPDATE warehouses
                SET last_checked_at = ?, last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (observed_text, error or "check_failed", observed_text, warehouse_id),
            )
            conn.commit()
            return None

        cur = conn.execute(
            """
            INSERT INTO warehouse_stock_observations (
                warehouse_id, manufacturer, oem, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                warehouse_id,
                manufacturer,
                oem.strip(),
                quantity,
                status,
                source_type,
                observed_text,
                expires_text,
                source_record,
            ),
        )
        observation_id = int(cur.lastrowid)

        conn.execute(
            """
            INSERT INTO warehouse_stock_current (
                warehouse_id, manufacturer, oem, name, price_rub, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(warehouse_id, oem, source_type) DO UPDATE SET
                manufacturer=excluded.manufacturer,
                name=excluded.name,
                price_rub=excluded.price_rub,
                quantity=excluded.quantity,
                status=excluded.status,
                observed_at=excluded.observed_at,
                expires_at=excluded.expires_at,
                import_id=NULL,
                source_record=excluded.source_record
            """,
            (
                warehouse_id,
                manufacturer,
                oem.strip(),
                name,
                price_rub,
                quantity,
                status,
                source_type,
                observed_text,
                expires_text,
                source_record,
            ),
        )

        conn.execute(
            """
            UPDATE warehouse_sources
            SET enabled = 1,
                last_checked_at = ?,
                last_success_at = ?,
                last_error = NULL,
                updated_at = ?
            WHERE warehouse_id = ? AND source_type = ?
            """,
            (observed_text, observed_text, observed_text, warehouse_id, source_type),
        )
        conn.execute(
            """
            UPDATE warehouses
            SET last_checked_at = ?,
                last_success_at = ?,
                last_error = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (observed_text, observed_text, observed_text, warehouse_id),
        )
        if source_type in {"website", "file", "api"}:
            _absorb_committed_after_snapshot(
                conn,
                warehouse_id,
                observed_text,
                {oem.strip()},
            )
        conn.commit()
        return observation_id


def seed_initial_warehouses(db_file: Path | str = DEFAULT_DB) -> None:
    """Insert the three approved initial warehouses if they do not exist."""
    initial = [
        (
            "Orange ATV", "Москва", "МСК", "склад МСК",
            "https://orangeatv.ru/", "orangeatv", 10,
        ),
        (
            "Мотосервис 76", "Ярославль", "ЯРС", "склад ЯРС",
            "https://мотосервис76.рф/", "motoservice76", 20,
        ),
        (
            "Vlad Extreme Life", "Красноярск", "КРС", "склад КРС",
            "https://vladextremelife.ru/", "vladextremelife", 30,
        ),
    ]
    init_warehouse_db(db_file)
    existing = {row["code"] for row in list_warehouses(True, db_file)}
    for name, city, code, public, url, adapter, priority in initial:
        if code in existing:
            continue
        warehouse_id = add_warehouse(
            name, city, code, public, url, adapter, priority, db_file=db_file
        )
        ensure_source(
            warehouse_id, "website", enabled=True, priority=20,
            ttl_minutes=30, db_file=db_file
        )
        ensure_source(
            warehouse_id, "file", enabled=False, priority=30,
            ttl_minutes=240,
            config={
                "import_mode": "full_snapshot",
                "missing_oem_policy": "unknown",
            },
            db_file=db_file,
        )
        ensure_source(
            warehouse_id, "manual", enabled=True, priority=10,
            ttl_minutes=60,
            config={"manual_ttl_policy_version": 1},
            db_file=db_file,
        )


if __name__ == "__main__":
    seed_initial_warehouses()
    for warehouse in list_warehouses():
        print(
            warehouse["id"],
            warehouse["internal_name"],
            warehouse["public_name"],
        )
