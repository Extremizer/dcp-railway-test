"""Explicit opt-in schema creation on the caller's SQLite file only."""

import sqlite3


def ensure_bulk_customs_schema(db_path) -> None:
    connection = sqlite3.connect(db_path)
    try:
        with connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS part_customs_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                description_ru TEXT,
                description_en TEXT,
                country_of_origin TEXT,
                gross_weight_kg REAL,
                net_weight_kg REAL,
                equipment_type TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(manufacturer, oem)
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS bulk_order_imports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                status TEXT NOT NULL,
                input_type TEXT NOT NULL,
                raw_lines_count INTEGER NOT NULL DEFAULT 0,
                parsed_items_count INTEGER NOT NULL DEFAULT 0,
                review_items_count INTEGER NOT NULL DEFAULT 0,
                invalid_items_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )""")
            connection.execute("""CREATE TABLE IF NOT EXISTS bulk_order_import_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                bulk_order_import_id INTEGER NOT NULL,
                source_line_number INTEGER NOT NULL,
                raw_text TEXT NOT NULL,
                raw_oem TEXT,
                normalized_oem TEXT,
                quantity INTEGER,
                parse_status TEXT NOT NULL,
                error_code TEXT,
                duplicate_group_id TEXT,
                FOREIGN KEY (bulk_order_import_id) REFERENCES bulk_order_imports(id)
            )""")
            connection.execute("""CREATE INDEX IF NOT EXISTS idx_bulk_items_import
                ON bulk_order_import_items(bulk_order_import_id)""")
            connection.execute("""CREATE INDEX IF NOT EXISTS idx_bulk_items_oem
                ON bulk_order_import_items(normalized_oem)""")
    finally:
        connection.close()
