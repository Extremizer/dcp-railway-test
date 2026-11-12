"""Isolated category-authority records for DCP transaction tests only.

The rows are synthetic test inputs. Never import this module from runtime code.
Does not mock dcp_category_authority.resolve or bypass any of its guards.
"""
from datetime import datetime, timezone
import sqlite3

SOURCE_REF = "synthetic://test-only/category-authority"
SOURCE_KIND = "live_public_form_metadata"


def seed_test_category_authority(db_file, *identities):
    """Prepare the exact catalog + current-alias contract in disposable SQLite.

    identities: pairs of (canonical manufacturer, exact requested/current OEM).
    """
    if not identities:
        identities = (
            ("Arctic Cat", "2670-265"),
            ("Honda", "91303-PA9-004"),
            ("Yamaha", "9999"),
        )
    checked = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with sqlite3.connect(db_file) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS oem_catalog_cache (
            manufacturer TEXT NOT NULL,
            current_oem TEXT NOT NULL,
            name TEXT,
            msrp_usd REAL,
            first_seen_at TEXT,
            catalog TEXT NOT NULL,
            msrp_verified INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            last_verified_at TEXT NOT NULL,
            previous_oems_json TEXT NOT NULL DEFAULT '[]',
            PRIMARY KEY (manufacturer, current_oem)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS oem_catalog_aliases (
            manufacturer TEXT NOT NULL,
            current_oem TEXT NOT NULL,
            alias_oem TEXT NOT NULL,
            alias_kind TEXT NOT NULL,
            PRIMARY KEY (manufacturer, alias_oem)
        )""")
        for manufacturer, oem in identities:
            conn.execute("""INSERT INTO oem_catalog_cache
                (manufacturer,current_oem,catalog,msrp_verified,source_kind,
                 source_ref,last_verified_at,previous_oems_json)
                VALUES(?,?,?,1,?,?,?,'[]')""",
                (manufacturer, oem, "Parts", SOURCE_KIND, SOURCE_REF, checked))
            conn.execute("""INSERT INTO oem_catalog_aliases
                (manufacturer,current_oem,alias_oem,alias_kind)
                VALUES(?,?,?,'current')""", (manufacturer, oem, oem))
