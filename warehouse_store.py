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
    notes: str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int:
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            INSERT INTO warehouse_imports (
                warehouse_id, filename, file_format, imported_at,
                rows_total, rows_success, rows_error, import_mode,
                mapping_json, missing_oem_policy, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )
        conn.commit()
        return int(cur.lastrowid)


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
    db_file: Path | str = DEFAULT_DB,
) -> int:
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
                mapping_json, missing_oem_policy, status, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'success', NULL)
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
            ),
        )
        import_id = int(cur.lastrowid)

        if import_mode == "full_snapshot":
            conn.execute(
                """
                DELETE FROM warehouse_stock_current
                WHERE warehouse_id = ? AND source_type = 'file'
                """,
                (warehouse_id,),
            )

        history_rows = []
        current_rows = []

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
                quantity,
                status,
                "file",
                observed_text,
                expires_text,
                import_id,
                source_record,
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
                warehouse_id, manufacturer, oem, name, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(warehouse_id, oem, source_type) DO UPDATE SET
                manufacturer=excluded.manufacturer,
                name=excluded.name,
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


def record_source_stock(
    warehouse_id: int,
    oem: str,
    status: str,
    quantity: float | None,
    source_type: str,
    manufacturer: str | None = None,
    name: str | None = None,
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
                warehouse_id, manufacturer, oem, name, quantity, status,
                source_type, observed_at, expires_at, import_id, source_record
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
            ON CONFLICT(warehouse_id, oem, source_type) DO UPDATE SET
                manufacturer=excluded.manufacturer,
                name=excluded.name,
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
            ttl_minutes=None, db_file=db_file
        )


if __name__ == "__main__":
    seed_initial_warehouses()
    for warehouse in list_warehouses():
        print(
            warehouse["id"],
            warehouse["internal_name"],
            warehouse["public_name"],
        )
