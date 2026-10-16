"""Production wiring for ADMIN-only Polaris dealer-price lookup.

This module is an explicit entrypoint. It does not alter the normal dealer price
path, does not promote global FOUND, and opens a fresh read-only authority DB
connection for every category resolution.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import dcp_category_authority
from oemixi_admin_polaris_browser_price import admin_browser_price


def _orders_db_path(db_path=None) -> Path:
    raw = str(db_path or os.getenv("EXTREMIZER_ORDERS_DB_FILE", "")).strip()
    if not raw:
        raise RuntimeError("EXTREMIZER_ORDERS_DB_FILE_REQUIRED")
    path = Path(raw).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise RuntimeError("EXTREMIZER_ORDERS_DB_FILE_NOT_FOUND")
    return path


def _connect_read_only(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def make_category_resolver(db_path=None):
    path = _orders_db_path(db_path)

    def resolve_category(manufacturer, requested_oem):
        with _connect_read_only(path) as conn:
            return dcp_category_authority.resolve(
                conn,
                str(manufacturer or ""),
                str(requested_oem or ""),
            )

    return resolve_category


def make_admin_polaris_provider(db_path=None):
    category_resolver = make_category_resolver(db_path)

    def provider(oem):
        return admin_browser_price(
            oem,
            category_resolver=category_resolver,
        )

    return provider


def build_application(token=None, *, db_path=None):
    import oemixibot_app

    application = oemixibot_app.build_application(token)
    oemixibot_app.bind_admin_polaris_price_resolver(
        application,
        make_admin_polaris_provider(db_path),
    )
    return application


def main():
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.info("OEMixiBOT ADMIN Polaris production runtime starting")
    build_application().run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
