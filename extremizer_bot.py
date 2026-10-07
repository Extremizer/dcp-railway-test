#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
telegram_bot_v2_7_3.py

Telegram frontend for the frozen DealerCostParts V6.6 search engine.

V2.6.1 Telegram manager-order handoff + two UX fixes:
- V6.6 is imported and NOT modified.
- Search/parser behavior is unchanged from V2.2.
- Preserves V2.3 one/multi-OEM input for the selected manufacturer.
- Preserves V2.4 mixed-manufacturer input and improves only the post-search UX.
- Supports manufacturer headers followed by multiple OEMs.
- Every item is searched sequentially through the same frozen V6.6 service.
- FOUND / HUMAN_REVIEW / NOT_FOUND / technical failures remain per-item.
- V2.1 false-NOT_FOUND technical guard is preserved.
"""

import asyncio
import base64
import json
import logging
import math
import os
import re
import secrets
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_CEILING, ROUND_HALF_UP
from html import escape
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import RetryAfter
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from playwright.async_api import async_playwright

import dealercostparts_manufacturer_finder_v6_6 as finder
from dcp_private_price import get_dealer_price
import warehouse_admin
import warehouse_store
import stock_engine
import warehouse_stock_service
import warehouse_refresh_scheduler
import warehouse_alerts
import web_handoff
import oem_reference_service
import supplier_runtime
import supplier_telegram_admin
import supplier_telegram_handlers
import warehouse_recipient
import pricing_analytics


# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

TOKEN = os.getenv("EXTREMIZER_BOT_TOKEN", "").strip()
MANAGER_CHAT_ID = os.getenv("TELEGRAM_MANAGER_CHAT_ID", "").strip()
EXTREMIZER_CHANNEL_URL = os.getenv(
    "EXTREMIZER_CHANNEL_URL", "https://t.me/ExtremizerPro"
).strip()
EXTREMIZER_CLUB_URL = os.getenv("EXTREMIZER_CLUB_URL", "").strip()
USD_RUB_RATE_RAW = os.getenv("EXTREMIZER_USD_RUB_RATE", "").strip().replace(",", ".")
USD_RUB_RATE = float(USD_RUB_RATE_RAW) if USD_RUB_RATE_RAW else 0.0
PRICE_COEFFICIENT_RAW = os.getenv("EXTREMIZER_PRICE_COEFFICIENT", "1.34").strip().replace(",", ".")
PRICE_COEFFICIENT = float(PRICE_COEFFICIENT_RAW) if PRICE_COEFFICIENT_RAW else 1.34
DP_CACHE_MAX_AGE_HOURS_RAW = os.getenv(
    "EXTREMIZER_DP_CACHE_MAX_AGE_HOURS", "168"
).strip().replace(",", ".")
try:
    DP_CACHE_MAX_AGE_HOURS = max(0.0, float(DP_CACHE_MAX_AGE_HOURS_RAW))
except ValueError:
    DP_CACHE_MAX_AGE_HOURS = 168.0

DCP_MODE = os.getenv("EXTREMIZER_DCP_MODE", "live").strip().lower()
DCP_CACHE_ONLY = DCP_MODE == "cache_only"

RATE_FILE = Path(__file__).with_name("usd_rub_rate.txt")
PRICE_COEFFICIENT_FILE = Path(__file__).with_name("price_coefficient.txt")
PUBLIC_MSRP_CACHE_FILE = Path(__file__).with_name("dcp_public_msrp_cache.json")
RATE_ADMIN_USER_ID = 52637605
ORDERS_DB_FILE = Path(
    os.getenv("EXTREMIZER_ORDERS_DB_FILE", "").strip()
    or Path(__file__).with_name("extremizer_orders.db")
)
# Shared by Telegram ADMIN and WEB ADMIN; one DB and one service contract.
supplier_order_service = supplier_runtime.get_supplier_order_service(ORDERS_DB_FILE)

DELIVERY_SEPARATE_NOTICE = (
    "🚚 Доставка из США в указанную стоимость не входит и оплачивается отдельно. "
    "Окончательная стоимость доставки определяется после прихода груза в Москву, "
    "исходя из фактического веса заказа, его размеров и выбранного способа доставки."
)
REFERENCE_WEIGHT_NOTICE = "Данные по весу носят справочный характер."


def load_usd_rub_rate() -> float:
    """Load saved manager rate; fall back to EXTREMIZER_USD_RUB_RATE."""
    if RATE_FILE.exists():
        try:
            value = float(RATE_FILE.read_text(encoding="utf-8").strip().replace(",", "."))
            if value > 0:
                return value
        except (OSError, ValueError):
            log.warning("Could not read USD/RUB rate from %s", RATE_FILE)

    return USD_RUB_RATE


def save_usd_rub_rate(value: float) -> None:
    RATE_FILE.write_text(f"{value:g}", encoding="utf-8")


def load_price_coefficient() -> float:
    if PRICE_COEFFICIENT_FILE.exists():
        try:
            value = float(
                PRICE_COEFFICIENT_FILE.read_text(encoding="utf-8")
                .lstrip("\ufeff")
                .strip()
                .replace(",", ".")
            )
            if value > 0:
                return value
        except (OSError, ValueError):
            log.warning(
                "Could not read price coefficient from %s",
                PRICE_COEFFICIENT_FILE,
            )
    return PRICE_COEFFICIENT


def save_price_coefficient(value: float) -> None:
    PRICE_COEFFICIENT_FILE.write_text(f"{value:g}", encoding="utf-8")


def public_msrp_cache_result(manufacturer: str, oem: str) -> dict | None:
    """Return a validated public MSRP snapshot for an exact manufacturer/OEM."""
    persistent = get_oem_catalog_cache_result(manufacturer, oem)
    if persistent:
        return persistent

    # Legacy JSON snapshot remains as a bootstrap/fallback source.
    if not PUBLIC_MSRP_CACHE_FILE.exists():
        return None

    canonical = finder.manufacturer_alias(manufacturer)
    normalized_oem = finder.normalize_oem(oem)
    if not canonical or not normalized_oem:
        return None

    try:
        payload = json.loads(PUBLIC_MSRP_CACHE_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        log.exception("Could not read public MSRP cache from %s", PUBLIC_MSRP_CACHE_FILE)
        return None

    for entry in payload.get("entries") or []:
        entry_manufacturer = finder.manufacturer_alias(str(entry.get("manufacturer") or ""))
        entry_oem = finder.normalize_oem(str(entry.get("oem") or ""))
        previous_oems = [
            finder.normalize_oem(str(value or ""))
            for value in (entry.get("previous_oems") or [])
        ]
        previous_oems = [value for value in previous_oems if value]
        alias_match = normalized_oem in previous_oems
        if entry_manufacturer != canonical or (
            entry_oem != normalized_oem and not alias_match
        ):
            continue

        msrp = entry.get("msrp_usd")
        if not isinstance(msrp, (int, float)) or float(msrp) <= 0:
            return None

        slug = None
        for catalog in finder.CATALOGS:
            if catalog.manufacturer == canonical and catalog.kind == "parts":
                slug = catalog.slug
                break
        search_url = (
            f"{finder.BASE}/oemparts/partsearch/{slug}?partsearch={normalized_oem}"
            if slug else None
        )

        return {
            "found": True,
            "status": "FOUND",
            "manufacturer": canonical,
            "catalog": str(entry.get("catalog") or "Parts"),
            "query_oem": normalized_oem,
            "oem": entry_oem,
            "item_sku": entry_oem,
            "name": str(entry.get("name") or "") or None,
            "price": float(msrp),
            "currency": "USD",
            "previous_oems": list(entry.get("previous_oems") or []),
            "image": None,
            "product_url": None,
            "search_url": search_url,
            "variants": [],
            "_public_msrp_cache": {
                "captured_at": entry.get("captured_at"),
                "source_kind": entry.get("source_kind"),
                "source_file": entry.get("source_file"),
            },
            "_diagnostic": {
                "match_rule": (
                    "validated_public_msrp_cache_previous_oem_match"
                    if alias_match and entry_oem != normalized_oem
                    else "validated_public_msrp_cache_exact_match"
                ),
                "price_source": "public_msrp_cache",
                "qoh_extracted": False,
            },
        }

    return None


def init_orders_db() -> None:
    """Create persistent order-history tables if they do not exist."""
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                order_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                telegram_user_id INTEGER NOT NULL,
                customer_name TEXT,
                username TEXT,
                total_usd REAL NOT NULL,
                usd_rub_rate REAL NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                manufacturer TEXT,
                oem TEXT,
                requested_oem TEXT,
                name TEXT,
                quantity INTEGER NOT NULL,
                price_usd REAL,
                FOREIGN KEY (order_id) REFERENCES orders(order_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_created_at "
            "ON orders(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_telegram_user_id "
            "ON orders(telegram_user_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_items_oem "
            "ON order_items(oem)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_order_items_oem_nocase "
            "ON order_items(oem COLLATE NOCASE)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_username_nocase "
            "ON orders(username COLLATE NOCASE)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_orders_customer_name_nocase "
            "ON orders(customer_name COLLATE NOCASE)"
        )

        order_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(orders)").fetchall()
        }
        for column, column_type in (
            ("status", "TEXT NOT NULL DEFAULT 'new'"),
            ("customer_total_rub", "REAL"),
            ("delivery_tariff", "TEXT"),
            ("actual_weight_kg", "REAL"),
            ("volume_weight_kg", "REAL"),
            ("delivery_calculated_rub", "REAL"),
            ("delivery_manual_rub", "REAL"),
            ("delivery_rub", "REAL"),
            ("delivery_pending", "INTEGER NOT NULL DEFAULT 0"),
            ("updated_at", "TEXT"),
            ("customer_confirmation_status", "TEXT"),
            ("customer_confirmed_at", "TEXT"),
            ("quote_sent_at", "TEXT"),
            ("quote_message_id", "INTEGER"),
            ("final_quote_sent_at", "TEXT"),
            ("final_quote_message_id", "INTEGER"),
            ("pricing_coefficient", "REAL"),
            ("auto_pricing_status", "TEXT"),
            ("origin", "TEXT NOT NULL DEFAULT 'telegram'"),
        ):
            if column not in order_columns:
                conn.execute(f"ALTER TABLE orders ADD COLUMN {column} {column_type}")

        # ---------------------------------------------------------
        # UPS DELIVERY ARCHITECTURE
        # ---------------------------------------------------------

        # Snapshot of item classification inside each order.
        order_item_columns = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(order_items)"
            ).fetchall()
        }

        if "item_type" not in order_item_columns:
            conn.execute(
                "ALTER TABLE order_items ADD COLUMN item_type TEXT"
            )

        if "selected_delivery_tariff" not in order_item_columns:
            conn.execute(
                "ALTER TABLE order_items "
                "ADD COLUMN selected_delivery_tariff TEXT"
            )

        if "delivery_selected_at" not in order_item_columns:
            conn.execute(
                "ALTER TABLE order_items "
                "ADD COLUMN delivery_selected_at TEXT"
            )

        for column, column_type in (
            ("requested_oem", "TEXT"),
            ("dealer_price_usd", "REAL"),
            ("dealer_price_source", "TEXT"),
            ("dealer_price_checked_at", "TEXT"),
            ("dealer_price_status", "TEXT"),
            ("customer_unit_rub", "INTEGER"),
            ("item_type_source", "TEXT"),
            ("reference_actual_weight_kg", "REAL"),
            ("reference_volume_weight_kg", "REAL"),
            ("reference_weight_state", "TEXT"),
            ("reference_weight_source", "TEXT"),
            ("offer_source", "TEXT NOT NULL DEFAULT 'usa'"),
            ("warehouse_id", "INTEGER"),
            ("warehouse_public_name", "TEXT"),
            ("price_snapshot_rub", "REAL"),
            ("available_snapshot", "REAL"),
        ):
            if column not in order_item_columns:
                conn.execute(
                    f"ALTER TABLE order_items ADD COLUMN {column} {column_type}"
                )

        # One client order may be fulfilled by several independent sources.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS order_fulfillment_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                group_key TEXT NOT NULL,
                source_type TEXT NOT NULL,
                warehouse_id INTEGER,
                warehouse_public_name TEXT,
                delivery_preference TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                UNIQUE(order_id, group_key),
                FOREIGN KEY (order_id) REFERENCES orders(order_id)
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_fulfillment_order "
            "ON order_fulfillment_groups(order_id)"
        )

        # Persistent OEM classification / knowledge base.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oem_delivery_profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                item_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(manufacturer, oem)
            )
            """
        )

        # Persistent dealer-price cache.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dealer_price_cache (
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                dealer_price_usd REAL NOT NULL,
                source TEXT,
                first_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                last_used_at TEXT,
                use_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (manufacturer, oem)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_dealer_price_cache_verified
            ON dealer_price_cache(last_verified_at)
            """
        )

        # Seed cache from previously verified live DP snapshots only.
        # CACHE_FALLBACK rows are deliberately excluded because they are not
        # independent price verifications.
        historical_dp_rows = conn.execute(
            """
            SELECT
                oi.manufacturer,
                oi.oem,
                oi.dealer_price_usd,
                oi.dealer_price_source,
                COALESCE(oi.dealer_price_checked_at, o.created_at)
            FROM order_items AS oi
            JOIN orders AS o ON o.order_id = oi.order_id
            WHERE
                oi.dealer_price_usd IS NOT NULL
                AND oi.dealer_price_usd > 0
                AND UPPER(COALESCE(oi.dealer_price_status, '')) = 'FOUND'
            ORDER BY COALESCE(oi.dealer_price_checked_at, o.created_at)
            """
        ).fetchall()
        for (
            cache_manufacturer,
            cache_oem,
            cache_price,
            cache_source,
            cache_verified_at,
        ) in historical_dp_rows:
            if not cache_manufacturer or not cache_oem or not cache_verified_at:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO dealer_price_cache (
                    manufacturer,
                    oem,
                    dealer_price_usd,
                    source,
                    first_seen_at,
                    last_verified_at,
                    last_used_at,
                    use_count
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
                """,
                (
                    str(cache_manufacturer),
                    str(cache_oem),
                    float(cache_price),
                    str(cache_source or "") or None,
                    str(cache_verified_at),
                    str(cache_verified_at),
                ),
            )

        # Persistent verified OEM/catalog cache for cloud/offline lookup.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oem_catalog_cache (
                manufacturer TEXT NOT NULL,
                current_oem TEXT NOT NULL,
                name TEXT,
                catalog TEXT,
                msrp_usd REAL,
                msrp_verified INTEGER NOT NULL DEFAULT 0,
                previous_oems_json TEXT NOT NULL DEFAULT '[]',
                source_kind TEXT,
                source_ref TEXT,
                first_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                PRIMARY KEY (manufacturer, current_oem)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oem_catalog_aliases (
                manufacturer TEXT NOT NULL,
                alias_oem TEXT NOT NULL,
                current_oem TEXT NOT NULL,
                alias_kind TEXT NOT NULL,
                PRIMARY KEY (manufacturer, alias_oem)
            )
            """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oem_catalog_alias_current
            ON oem_catalog_aliases(manufacturer, current_oem)
            """
        )

        # Seed only from explicitly validated public MSRP snapshots.
        if PUBLIC_MSRP_CACHE_FILE.exists():
            try:
                public_seed_payload = json.loads(
                    PUBLIC_MSRP_CACHE_FILE.read_text(encoding="utf-8")
                )
            except (OSError, ValueError, json.JSONDecodeError):
                public_seed_payload = {}
                log.exception(
                    "Could not seed OEM catalog cache from %s",
                    PUBLIC_MSRP_CACHE_FILE,
                )

            for public_entry in public_seed_payload.get("entries") or []:
                cache_manufacturer = finder.manufacturer_alias(
                    str(public_entry.get("manufacturer") or "")
                )
                cache_current_oem = finder.normalize_oem(
                    str(public_entry.get("oem") or "")
                )
                cache_msrp = public_entry.get("msrp_usd")
                if (
                    not cache_manufacturer
                    or not cache_current_oem
                    or not isinstance(cache_msrp, (int, float))
                    or float(cache_msrp) <= 0
                ):
                    continue

                cache_previous = []
                for previous_value in public_entry.get("previous_oems") or []:
                    normalized_previous = finder.normalize_oem(
                        str(previous_value or "")
                    )
                    if (
                        normalized_previous
                        and normalized_previous != cache_current_oem
                        and normalized_previous not in cache_previous
                    ):
                        cache_previous.append(normalized_previous)

                cache_verified_at = (
                    str(public_entry.get("captured_at") or "").strip()
                    or datetime.now().astimezone().isoformat(timespec="seconds")
                )
                cache_source_kind = (
                    str(public_entry.get("source_kind") or "").strip()
                    or "validated_public_msrp"
                )
                cache_source_ref = (
                    str(public_entry.get("source_file") or "").strip()
                    or str(PUBLIC_MSRP_CACHE_FILE.name)
                )

                conn.execute(
                    """
                    INSERT INTO oem_catalog_cache (
                        manufacturer,
                        current_oem,
                        name,
                        catalog,
                        msrp_usd,
                        msrp_verified,
                        previous_oems_json,
                        source_kind,
                        source_ref,
                        first_seen_at,
                        last_verified_at
                    )
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                    ON CONFLICT(manufacturer, current_oem) DO UPDATE SET
                        name = excluded.name,
                        catalog = excluded.catalog,
                        msrp_usd = excluded.msrp_usd,
                        msrp_verified = 1,
                        previous_oems_json = excluded.previous_oems_json,
                        source_kind = excluded.source_kind,
                        source_ref = excluded.source_ref,
                        last_verified_at = excluded.last_verified_at
                    """,
                    (
                        cache_manufacturer,
                        cache_current_oem,
                        str(public_entry.get("name") or "") or None,
                        str(public_entry.get("catalog") or "Parts"),
                        float(cache_msrp),
                        json.dumps(cache_previous, ensure_ascii=False),
                        cache_source_kind,
                        cache_source_ref,
                        cache_verified_at,
                        cache_verified_at,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO oem_catalog_aliases (
                        manufacturer,
                        alias_oem,
                        current_oem,
                        alias_kind
                    )
                    VALUES (?, ?, ?, 'current')
                    ON CONFLICT(manufacturer, alias_oem) DO UPDATE SET
                        current_oem = excluded.current_oem,
                        alias_kind = excluded.alias_kind
                    """,
                    (
                        cache_manufacturer,
                        cache_current_oem,
                        cache_current_oem,
                    ),
                )
                for cache_previous_oem in cache_previous:
                    conn.execute(
                        """
                        INSERT INTO oem_catalog_aliases (
                            manufacturer,
                            alias_oem,
                            current_oem,
                            alias_kind
                        )
                        VALUES (?, ?, ?, 'previous')
                        ON CONFLICT(manufacturer, alias_oem) DO UPDATE SET
                            current_oem = excluded.current_oem,
                            alias_kind = excluded.alias_kind
                        """,
                        (
                            cache_manufacturer,
                            cache_previous_oem,
                            cache_current_oem,
                        ),
                    )

        # Delivery tariffs must exist before delivery_groups references them.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_tariffs (
                code TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                base_rub_per_kg REAL NOT NULL,
                volume_rub_per_kg REAL NOT NULL DEFAULT 0,
                calculation_type TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO delivery_tariffs
                (code, name, base_rub_per_kg, volume_rub_per_kg,
                 calculation_type, enabled)
            VALUES (?, ?, ?, ?, ?, 1)
            """,
            [
                ("comfort", "Комфорт", 2500.0, 650.0, "excess_volume"),
                ("economy", "Эконом", 1500.0, 0.0, "actual_only"),
                ("mix", "MIX", 3000.0, 650.0, "all_volume"),
            ],
        )

        # Physical delivery groups inside one customer order.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id TEXT NOT NULL,
                group_number INTEGER NOT NULL,
                tariff_code TEXT,
                customer_selected_at TEXT,
                actual_weight_kg REAL,
                width_cm REAL,
                height_cm REAL,
                length_cm REAL,
                volume_weight_kg REAL,
                calculated_rub REAL,
                manual_rub REAL,
                final_rub REAL,
                status TEXT NOT NULL DEFAULT 'awaiting_tariff',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,

                FOREIGN KEY (order_id)
                    REFERENCES orders(order_id),

                FOREIGN KEY (tariff_code)
                    REFERENCES delivery_tariffs(code),

                UNIQUE(order_id, group_number)
            )
            """
        )
        group_columns = {
            row[1] for row in conn.execute(
                "PRAGMA table_info(delivery_groups)"
            ).fetchall()
        }
        for column, column_type in (
            ("volume_weight_divisor", "REAL"),
            ("applied_calculation_type", "TEXT"),
            ("applied_base_rub_per_kg", "REAL"),
            ("applied_volume_rub_per_kg", "REAL"),
        ):
            if column not in group_columns:
                conn.execute(
                    f"ALTER TABLE delivery_groups ADD COLUMN "
                    f"{column} {column_type}"
                )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT
            )
            """
        )
        # One source of truth for delivery calculation + client-facing terms.
        # Migrate only the untouched legacy divisor. A manually edited value wins.
        conn.execute(
            """
            INSERT OR IGNORE INTO delivery_settings
                (setting_key, setting_value, updated_at)
            VALUES ('volume_weight_divisor', '6000', NULL)
            """
        )
        conn.execute(
            """
            UPDATE delivery_settings
            SET setting_value = '6000'
            WHERE setting_key = 'volume_weight_divisor'
              AND setting_value = '365'
              AND updated_at IS NULL
            """
        )
        conn.executemany(
            """
            INSERT OR IGNORE INTO delivery_settings
                (setting_key, setting_value, updated_at)
            VALUES (?, ?, NULL)
            """,
            [
                ("comfort_short_eta", "от 5 недель"),
                ("comfort_transit_eta", "3–4 недели"),
                ("comfort_dispatch_day", "четверг"),
                ("economy_eta", "от 12 недель с момента формирования партии"),
                ("mix_eta", "от 5 недель с момента формирования партии"),
                ("supplier_processing_standard", "1–2 недели"),
                ("supplier_processing_brp", "2–4 недели"),
            ],
        )

        # Positions assigned to delivery groups.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS delivery_group_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                delivery_group_id INTEGER NOT NULL,
                order_item_id INTEGER NOT NULL,

                FOREIGN KEY (delivery_group_id)
                    REFERENCES delivery_groups(id)
                    ON DELETE CASCADE,

                FOREIGN KEY (order_item_id)
                    REFERENCES order_items(id),

                UNIQUE(delivery_group_id, order_item_id)
            )
            """
        )

        # Full history of actual OEM arrivals.
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oem_delivery_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                order_id TEXT,
                delivery_group_id INTEGER,
                quantity INTEGER NOT NULL DEFAULT 1,
                actual_weight_kg REAL,
                width_cm REAL,
                height_cm REAL,
                length_cm REAL,
                volume_weight_kg REAL,
                recorded_at TEXT NOT NULL,

                FOREIGN KEY (order_id)
                    REFERENCES orders(order_id),

                FOREIGN KEY (delivery_group_id)
                    REFERENCES delivery_groups(id)
            )
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oem_delivery_profiles_oem
            ON oem_delivery_profiles(manufacturer, oem)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_delivery_groups_order
            ON delivery_groups(order_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_delivery_group_items_group
            ON delivery_group_items(delivery_group_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_delivery_group_items_item
            ON delivery_group_items(order_item_id)
            """
        )

        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_oem_delivery_history_oem
            ON oem_delivery_history(manufacturer, oem)
            """
        )

        conn.commit()


def upsert_oem_catalog_cache(
    result: dict,
    verified_msrp_usd: float,
    *,
    source_kind: str,
    source_ref: str | None = None,
    verified_at: str | None = None,
) -> bool:
    """Persist only a catalog result with independently verified public MSRP."""
    if str(result.get("status") or "").upper() != "FOUND":
        return False
    if not isinstance(verified_msrp_usd, (int, float)):
        return False

    msrp = float(verified_msrp_usd)
    if msrp <= 0:
        return False

    manufacturer = finder.manufacturer_alias(
        str(result.get("manufacturer") or "")
    )
    current_oem = finder.normalize_oem(
        str(result.get("item_sku") or result.get("oem") or "")
    )
    query_oem = finder.normalize_oem(
        str(result.get("query_oem") or current_oem)
    )
    if not manufacturer or not current_oem:
        return False

    previous_oems = []
    for raw_previous in result.get("previous_oems") or []:
        normalized_previous = finder.normalize_oem(str(raw_previous or ""))
        if (
            normalized_previous
            and normalized_previous != current_oem
            and normalized_previous not in previous_oems
        ):
            previous_oems.append(normalized_previous)
    if (
        query_oem
        and query_oem != current_oem
        and query_oem not in previous_oems
    ):
        previous_oems.append(query_oem)

    init_orders_db()
    verified_at = (
        str(verified_at or "").strip()
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        existing = conn.execute(
            """
            SELECT previous_oems_json, first_seen_at
            FROM oem_catalog_cache
            WHERE manufacturer = ? AND current_oem = ?
            """,
            (manufacturer, current_oem),
        ).fetchone()

        if existing:
            try:
                existing_previous = json.loads(existing[0] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                existing_previous = []
            for raw_previous in existing_previous:
                normalized_previous = finder.normalize_oem(
                    str(raw_previous or "")
                )
                if (
                    normalized_previous
                    and normalized_previous != current_oem
                    and normalized_previous not in previous_oems
                ):
                    previous_oems.append(normalized_previous)
            first_seen_at = str(existing[1] or verified_at)
        else:
            first_seen_at = verified_at

        conn.execute(
            """
            INSERT INTO oem_catalog_cache (
                manufacturer,
                current_oem,
                name,
                catalog,
                msrp_usd,
                msrp_verified,
                previous_oems_json,
                source_kind,
                source_ref,
                first_seen_at,
                last_verified_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(manufacturer, current_oem) DO UPDATE SET
                name = excluded.name,
                catalog = excluded.catalog,
                msrp_usd = excluded.msrp_usd,
                msrp_verified = 1,
                previous_oems_json = excluded.previous_oems_json,
                source_kind = excluded.source_kind,
                source_ref = excluded.source_ref,
                last_verified_at = excluded.last_verified_at
            """,
            (
                manufacturer,
                current_oem,
                str(result.get("name") or "") or None,
                str(result.get("catalog") or "") or None,
                msrp,
                json.dumps(previous_oems, ensure_ascii=False),
                str(source_kind or "validated_public_msrp"),
                str(source_ref or "") or None,
                first_seen_at,
                verified_at,
            ),
        )

        aliases = [(current_oem, "current")]
        aliases.extend((value, "previous") for value in previous_oems)
        for alias_oem, alias_kind in aliases:
            conn.execute(
                """
                INSERT INTO oem_catalog_aliases (
                    manufacturer,
                    alias_oem,
                    current_oem,
                    alias_kind
                )
                VALUES (?, ?, ?, ?)
                ON CONFLICT(manufacturer, alias_oem) DO UPDATE SET
                    current_oem = excluded.current_oem,
                    alias_kind = excluded.alias_kind
                """,
                (
                    manufacturer,
                    alias_oem,
                    current_oem,
                    alias_kind,
                ),
            )
        conn.commit()

    return True


def infer_manufacturer_from_verified_cache(oem: str) -> str | None:
    """Infer manufacturer only when one verified cache manufacturer matches OEM."""
    normalized_oem = finder.normalize_oem(str(oem or ""))
    if not normalized_oem:
        return None
    init_orders_db()
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        rows = conn.execute(
            """SELECT manufacturer
                 FROM oem_catalog_cache
                WHERE current_oem=? AND msrp_verified=1
                  AND msrp_usd IS NOT NULL AND msrp_usd>0
                UNION
               SELECT a.manufacturer
                 FROM oem_catalog_aliases AS a
                 JOIN oem_catalog_cache AS c
                   ON c.manufacturer=a.manufacturer AND c.current_oem=a.current_oem
                WHERE a.alias_oem=? AND c.msrp_verified=1
                  AND c.msrp_usd IS NOT NULL AND c.msrp_usd>0""",
            (normalized_oem, normalized_oem),
        ).fetchall()
    manufacturers = [str(row[0]) for row in rows if row and row[0]]
    return manufacturers[0] if len(manufacturers) == 1 else None

def infer_manufacturer_from_known_sources(oem: str) -> tuple[str | None, list[str]]:
    """Resolve a bare OEM from trusted local sources; never guess on conflicts."""
    normalized = finder.normalize_oem(str(oem or ""))
    if not normalized:
        return None, []
    candidates = set()
    cached = infer_manufacturer_from_verified_cache(normalized)
    if cached:
        candidates.add(finder.manufacturer_alias(cached) or cached)
    init_orders_db()
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "warehouse_stock_current" in tables:
            for row in conn.execute("SELECT DISTINCT manufacturer,name FROM warehouse_stock_current WHERE oem=? AND manufacturer IS NOT NULL AND TRIM(manufacturer)<>''", (normalized,)):
                raw = str(row[0] or "").strip()
                name = str(row[1] or "").lower()
                value = finder.manufacturer_alias(raw)
                if not value and raw.upper() == "BRP":
                    if "ski-doo" in name or "ski doo" in name:
                        value = "Ski-Doo"
                    elif "sea-doo" in name or "sea doo" in name:
                        value = "Sea-Doo"
                    elif "can-am" in name or "can am" in name:
                        value = "Can-Am"
                if value:
                    candidates.add(value)
        if "order_items" in tables:
            for row in conn.execute("SELECT DISTINCT manufacturer FROM order_items WHERE (oem=? OR requested_oem=?) AND manufacturer IS NOT NULL AND TRIM(manufacturer)<>''", (normalized, normalized)):
                value = finder.manufacturer_alias(str(row[0])) or str(row[0]).strip()
                if value:
                    candidates.add(value)
    values = sorted(candidates)
    return (values[0] if len(values) == 1 else None), values

def resolve_oem_identity(oem: str) -> dict:
    normalized = finder.normalize_oem(str(oem or ""))
    manufacturer, candidates = infer_manufacturer_from_known_sources(normalized)
    reference = oem_reference_service.lookup_oem(normalized) or {}
    name = None
    init_orders_db()
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "warehouse_stock_current" in tables:
            row = conn.execute("SELECT name FROM warehouse_stock_current WHERE oem=? AND name IS NOT NULL AND TRIM(name)<>'' ORDER BY observed_at DESC LIMIT 1", (normalized,)).fetchone()
            name = str(row[0]).strip() if row and row[0] else None
    return {"oem": normalized, "manufacturer": manufacturer, "manufacturer_candidates": candidates, "item_type": reference.get("item_type"), "name": name, "identified": bool(manufacturer)}

def get_oem_catalog_cache_result(
    manufacturer: str,
    oem: str,
) -> dict | None:
    """Return a cloud-safe cached result only when public MSRP is verified."""
    canonical = finder.manufacturer_alias(str(manufacturer or ""))
    normalized_oem = finder.normalize_oem(str(oem or ""))
    if not canonical or not normalized_oem:
        return None

    init_orders_db()
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        row = conn.execute(
            """
            SELECT
                c.current_oem,
                c.name,
                c.catalog,
                c.msrp_usd,
                c.previous_oems_json,
                c.source_kind,
                c.source_ref,
                c.first_seen_at,
                c.last_verified_at,
                a.alias_kind
            FROM oem_catalog_aliases AS a
            JOIN oem_catalog_cache AS c
              ON c.manufacturer = a.manufacturer
             AND c.current_oem = a.current_oem
            WHERE
                a.manufacturer = ?
                AND a.alias_oem = ?
                AND c.msrp_verified = 1
                AND c.msrp_usd IS NOT NULL
                AND c.msrp_usd > 0
            LIMIT 1
            """,
            (canonical, normalized_oem),
        ).fetchone()

    if not row:
        return None

    (
        current_oem,
        name,
        catalog_label,
        msrp_usd,
        previous_json,
        source_kind,
        source_ref,
        first_seen_at,
        last_verified_at,
        alias_kind,
    ) = row

    try:
        previous_oems = json.loads(previous_json or "[]")
    except (TypeError, ValueError, json.JSONDecodeError):
        previous_oems = []

    search_url = None
    for catalog in finder.CATALOGS:
        if (
            catalog.manufacturer == canonical
            and str(catalog.label or "") == str(catalog_label or "")
            and catalog.kind == "parts"
        ):
            search_url = (
                f"{catalog.search_url}?partsearch={normalized_oem}"
            )
            break

    return {
        "found": True,
        "status": "FOUND",
        "manufacturer": canonical,
        "catalog": str(catalog_label or "Parts"),
        "query_oem": normalized_oem,
        "oem": str(current_oem),
        "item_sku": str(current_oem),
        "name": str(name or "") or None,
        "price": float(msrp_usd),
        "currency": "USD",
        "previous_oems": list(previous_oems),
        "image": None,
        "product_url": None,
        "search_url": search_url,
        "variants": [],
        "_oem_catalog_cache": {
            "source_kind": source_kind,
            "source_ref": source_ref,
            "first_seen_at": first_seen_at,
            "last_verified_at": last_verified_at,
        },
        "_diagnostic": {
            "match_rule": (
                "verified_oem_cache_previous_oem_match"
                if str(alias_kind) == "previous"
                else "verified_oem_cache_exact_match"
            ),
            "price_source": "verified_oem_catalog_cache",
            "qoh_extracted": False,
        },
    }


def generate_order_id() -> str:
    """Generate a unique public order number in EDDM-XXXX format."""
    init_orders_db()

    now = datetime.now().astimezone()
    month_codes = "ABCDEFGHIJKL"
    date_code = f"{now.day:02d}{month_codes[now.month - 1]}"

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        for _ in range(100):
            order_id = f"E{date_code}-{secrets.randbelow(10000):04d}"
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE order_id = ? LIMIT 1",
                (order_id,),
            ).fetchone()

            if not exists:
                return order_id

    raise RuntimeError("Could not generate unique order ID")


def infer_item_type_from_dcp_catalog(catalog: str | None) -> str | None:
    """Conservative AUTO-TYPE from an explicit DCP catalog label."""
    label = str(catalog or "").strip().lower()
    if not label:
        return None
    if label == "parts":
        return "part"
    if "accessories" in label:
        return "accessory"
    if label == "apparel" or "apparel & gear" in label:
        return "gear"
    return None


def get_oem_reference(
    current_oem: str | None,
    requested_oem: str | None = None,
) -> dict | None:
    """Read-only lookup in the local OEM reference database."""
    try:
        return oem_reference_service.lookup_first(current_oem, requested_oem)
    except Exception:
        log.exception(
            "OEM reference lookup failed for current=%r requested=%r",
            current_oem,
            requested_oem,
        )
        return None


def _format_weight_kg(value) -> str:
    number = Decimal(str(value))
    text = format(number.normalize(), "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def reference_weight_lines(
    actual_weight_kg,
    volume_weight_kg,
    quantity: int = 1,
) -> list[str]:
    """Customer-safe per-piece reference weight lines."""
    actual = (
        float(actual_weight_kg)
        if isinstance(actual_weight_kg, (int, float)) and actual_weight_kg > 0
        else None
    )
    volume = (
        float(volume_weight_kg)
        if isinstance(volume_weight_kg, (int, float)) and volume_weight_kg > 0
        else None
    )
    qty = max(1, int(quantity or 1))
    lines = []
    if actual is not None and volume is not None:
        lines.append(
            "Вес за 1 шт.: "
            f"фактический <b>{_format_weight_kg(actual)} кг</b>; "
            f"объёмный <b>{_format_weight_kg(volume)} кг</b>"
        )
    elif actual is not None:
        lines.append(
            f"Фактический вес за 1 шт.: <b>{_format_weight_kg(actual)} кг</b>"
        )
    elif volume is not None:
        lines.append(
            f"Объёмный вес за 1 шт.: <b>{_format_weight_kg(volume)} кг</b>"
        )
    else:
        lines.append("Вес: неизвестен")
    if actual is not None and qty > 1:
        lines.append(
            f"Фактический вес за {qty} шт.: "
            f"<b>{_format_weight_kg(Decimal(str(actual)) * Decimal(qty))} кг</b>"
        )
    return lines


def customer_rub_price_from_dp(
    dealer_price_usd,
    coefficient: float | None = None,
    rate: float | None = None,
) -> int | None:
    """DP × coefficient × USD/RUB rate, rounded UP to the next 100 RUB."""
    if not isinstance(dealer_price_usd, (int, float)):
        return None
    coefficient_value = PRICE_COEFFICIENT if coefficient is None else coefficient
    rate_value = USD_RUB_RATE if rate is None else rate
    if coefficient_value <= 0 or rate_value <= 0:
        return None
    value = (
        Decimal(str(dealer_price_usd))
        * Decimal(str(coefficient_value))
        * Decimal(str(rate_value))
    )
    rounded_blocks = (value / Decimal("100")).to_integral_value(
        rounding=ROUND_CEILING
    )
    return int(rounded_blocks * Decimal("100"))


def _dealer_price_cache_key(
    manufacturer: str,
    oem: str,
) -> tuple[str, str]:
    canonical = finder.manufacturer_alias(str(manufacturer or "").strip())
    normalized_oem = finder.normalize_oem(str(oem or "").strip())
    return canonical, normalized_oem


def upsert_dealer_price_cache(
    manufacturer: str,
    oem: str,
    dealer_price_usd: float,
    source: str | None,
    verified_at: str | None = None,
) -> bool:
    """Persist only successfully verified DP values."""
    if not isinstance(dealer_price_usd, (int, float)):
        return False
    price = float(dealer_price_usd)
    if price <= 0:
        return False

    canonical, normalized_oem = _dealer_price_cache_key(manufacturer, oem)
    if not canonical or not normalized_oem:
        return False

    init_orders_db()
    verified_at = (
        str(verified_at or "").strip()
        or datetime.now().astimezone().isoformat(timespec="seconds")
    )
    source_value = str(source or "").strip() or None

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO dealer_price_cache (
                manufacturer,
                oem,
                dealer_price_usd,
                source,
                first_seen_at,
                last_verified_at,
                last_used_at,
                use_count
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
            ON CONFLICT(manufacturer, oem) DO UPDATE SET
                dealer_price_usd = excluded.dealer_price_usd,
                source = excluded.source,
                last_verified_at = excluded.last_verified_at
            """,
            (
                canonical,
                normalized_oem,
                price,
                source_value,
                verified_at,
                verified_at,
            ),
        )
        conn.commit()
    return True


def get_dealer_price_cache(
    manufacturer: str,
    oem: str,
    *,
    mark_used: bool = False,
) -> dict | None:
    """Return DP cache metadata, including freshness against configured TTL."""
    canonical, normalized_oem = _dealer_price_cache_key(manufacturer, oem)
    if not canonical or not normalized_oem:
        return None

    init_orders_db()
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        row = conn.execute(
            """
            SELECT
                dealer_price_usd,
                source,
                first_seen_at,
                last_verified_at,
                last_used_at,
                use_count
            FROM dealer_price_cache
            WHERE manufacturer = ? AND oem = ?
            """,
            (canonical, normalized_oem),
        ).fetchone()
        if not row:
            return None

        try:
            verified_dt = datetime.fromisoformat(str(row[3]))
            now_dt = datetime.now().astimezone()
            if verified_dt.tzinfo is None:
                verified_dt = verified_dt.replace(tzinfo=now_dt.tzinfo)
            age_hours = max(
                0.0,
                (now_dt - verified_dt.astimezone(now_dt.tzinfo)).total_seconds()
                / 3600.0,
            )
        except (TypeError, ValueError):
            age_hours = float("inf")

        fresh = (
            DP_CACHE_MAX_AGE_HOURS > 0
            and age_hours <= DP_CACHE_MAX_AGE_HOURS
        )

        if mark_used and fresh:
            used_at = datetime.now().astimezone().isoformat(timespec="seconds")
            conn.execute(
                """
                UPDATE dealer_price_cache
                SET
                    last_used_at = ?,
                    use_count = use_count + 1
                WHERE manufacturer = ? AND oem = ?
                """,
                (used_at, canonical, normalized_oem),
            )
            conn.commit()

    return {
        "manufacturer": canonical,
        "oem": normalized_oem,
        "dealer_price_usd": float(row[0]),
        "source": row[1],
        "first_seen_at": row[2],
        "last_verified_at": row[3],
        "last_used_at": row[4],
        "use_count": int(row[5] or 0),
        "age_hours": age_hours,
        "fresh": fresh,
        "max_age_hours": DP_CACHE_MAX_AGE_HOURS,
    }


async def enrich_found_result_with_dealer_price(result: dict) -> dict:
    """Attach private DCP dealer-price metadata without changing customer UI."""
    if str(result.get("status") or "").upper() != "FOUND":
        return result
    catalog_is_parts = (
        str(result.get("catalog") or "").strip().lower() == "parts"
    )

    manufacturer = str(result.get("manufacturer") or "").strip()
    oem = str(result.get("item_sku") or result.get("oem") or "").strip()
    checked_at = datetime.now().astimezone().isoformat(timespec="seconds")
    if not manufacturer or not oem:
        return result

    # Verified DP cache is catalog-agnostic. Live private lookup remains
    # intentionally restricted to the OEM Parts endpoint below.
    if not catalog_is_parts:
        cached = get_dealer_price_cache(manufacturer, oem, mark_used=True)
        result["_dealer_price_live_status"] = "NOT_APPLICABLE_NON_PARTS"
        if cached and cached.get("fresh"):
            result["_dealer_price_status"] = "CACHE_FALLBACK"
            result["_dealer_price_usd"] = float(cached["dealer_price_usd"])
            result["_dealer_price_source"] = (
                "dp_cache:" + str(cached.get("source") or "verified_live_dp")
            )
            result["_dealer_price_checked_at"] = str(
                cached.get("last_verified_at") or ""
            )
            result["_dealer_price_cache_age_hours"] = float(
                cached.get("age_hours") or 0.0
            )
        else:
            result["_dealer_price_status"] = "CACHE_MISS"
            result["_dealer_price_checked_at"] = checked_at
        return result

    if DCP_CACHE_ONLY:
        cached = get_dealer_price_cache(
            manufacturer,
            oem,
            mark_used=True,
        )
        result["_dealer_price_live_status"] = "CACHE_ONLY"
        if cached and cached.get("fresh"):
            result["_dealer_price_status"] = "CACHE_FALLBACK"
            result["_dealer_price_usd"] = float(cached["dealer_price_usd"])
            result["_dealer_price_source"] = (
                "dp_cache:"
                + str(cached.get("source") or "verified_live_dp")
            )
            result["_dealer_price_checked_at"] = str(
                cached.get("last_verified_at") or ""
            )
            result["_dealer_price_cache_age_hours"] = float(
                cached.get("age_hours") or 0.0
            )
        else:
            result["_dealer_price_status"] = "CACHE_MISS"
            result["_dealer_price_checked_at"] = checked_at
        return result

    live_status = None
    try:
        private = await asyncio.to_thread(get_dealer_price, manufacturer, oem)
        live_status = str(private.status or "").upper()
        result["_dealer_price_status"] = live_status
        result["_dealer_price_checked_at"] = checked_at

        if live_status == "FOUND" and private.dealer_price_usd is not None:
            dealer_price_usd = float(private.dealer_price_usd)
            dealer_price_source = str(private.source or "")
            result["_dealer_price_usd"] = dealer_price_usd
            result["_dealer_price_source"] = dealer_price_source
            upsert_dealer_price_cache(
                manufacturer,
                oem,
                dealer_price_usd,
                dealer_price_source,
                checked_at,
            )
            return result
    except Exception:
        live_status = "TECHNICAL_ERROR"
        result["_dealer_price_status"] = live_status
        result["_dealer_price_checked_at"] = checked_at
        log.exception(
            "Private DCP dealer-price lookup failed for %s / %s",
            manufacturer,
            oem,
        )

    # Cache fallback is intentionally allowed only when live DP could not be
    # checked for technical/session reasons. Explicit NOT_FOUND / NO_PRICE
    # never reuse an older DP automatically.
    if live_status in {"CLOUDFLARE", "AUTH_REQUIRED", "TECHNICAL_ERROR"}:
        cached = get_dealer_price_cache(
            manufacturer,
            oem,
            mark_used=True,
        )
        if cached and cached.get("fresh"):
            result["_dealer_price_status"] = "CACHE_FALLBACK"
            result["_dealer_price_live_status"] = live_status
            result["_dealer_price_usd"] = float(cached["dealer_price_usd"])
            result["_dealer_price_source"] = (
                "dp_cache:"
                + str(cached.get("source") or "verified_live_dp")
            )
            result["_dealer_price_checked_at"] = str(
                cached.get("last_verified_at") or ""
            )
            result["_dealer_price_cache_age_hours"] = float(
                cached.get("age_hours") or 0.0
            )

    return result


def reserve_local_order_items(order_id: str) -> dict:
    init_orders_db()
    created = []
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, manufacturer, oem, quantity, warehouse_id
            FROM order_items
            WHERE order_id = ?
              AND offer_source = 'warehouse'
            ORDER BY position
            """,
            (order_id,),
        ).fetchall()
    for row in rows:
        result = stock_engine.reserve_stock(
            int(row["warehouse_id"]),
            str(row["oem"]),
            float(row["quantity"]),
            order_id=order_id,
            order_item_id=int(row["id"]),
            manufacturer=row["manufacturer"],
            notes="automatic reservation after client checkout",
            require_fresh=True,
            db_file=ORDERS_DB_FILE,
        )
        if not result.get("ok"):
            for reservation_id in created:
                stock_engine.release_reservation(
                    reservation_id,
                    reason="rollback_after_partial_order_reservation",
                    db_file=ORDERS_DB_FILE,
                )
            return {
                "ok": False,
                "reason": result.get("reason"),
                "order_item_id": int(row["id"]),
            }
        created.append(int(result["reservation_id"]))
    return {"ok": True, "reservation_ids": created}


def save_order_to_history(
    order_id: str,
    user,
    cart: dict,
    delivery_preference: str | None = None,
    origin: str = "telegram",
) -> None:
    """Persist one confirmed request and all of its cart positions."""
    init_orders_db()
    origin = "web" if str(origin or "").strip().lower() == "web" else "telegram"

    total_usd = 0.0
    prepared_items = []
    auto_customer_total_rub = 0
    auto_pricing_complete = bool(cart)

    for position, item in enumerate(cart.values(), 1):
        qty = int(item.get("qty", 1))
        price = item.get("price")

        if isinstance(price, (int, float)):
            price_usd = float(price)
            total_usd += price_usd * qty
        else:
            price_usd = None

        dealer_value = item.get("_dealer_price_usd")
        dealer_price_usd = (
            float(dealer_value)
            if isinstance(dealer_value, (int, float))
            else None
        )
        dealer_price_source = str(item.get("_dealer_price_source") or "") or None
        dealer_price_checked_at = (
            str(item.get("_dealer_price_checked_at") or "") or None
        )
        dealer_price_status = str(item.get("_dealer_price_status") or "") or None
        offer_source = str(item.get("offer_source") or "usa").strip().lower()
        if offer_source == "warehouse":
            snapshot_value = item.get("price_snapshot_rub")
            customer_unit_rub = (
                int(round(float(snapshot_value)))
                if isinstance(snapshot_value, (int, float))
                else None
            )
        else:
            offer_source = "usa"
            customer_unit_rub = customer_rub_price_from_dp(
                dealer_price_usd,
                PRICE_COEFFICIENT,
                USD_RUB_RATE,
            )
        if customer_unit_rub is None:
            auto_pricing_complete = False
        else:
            auto_customer_total_rub += customer_unit_rub * qty

        current_oem = str(item.get("oem") or "")
        requested_oem = str(item.get("requested_oem") or current_oem)

        reference = get_oem_reference(current_oem, requested_oem)
        reference_item_type = reference.get("item_type") if reference else None
        dcp_item_type = infer_item_type_from_dcp_catalog(item.get("catalog"))
        auto_item_type = reference_item_type or dcp_item_type
        auto_item_type_source = (
            "oem_reference"
            if reference_item_type
            else ("dcp_catalog" if dcp_item_type else None)
        )
        reference_actual_weight_kg = (
            reference.get("actual_weight_kg") if reference else None
        )
        reference_volume_weight_kg = (
            reference.get("volume_weight_kg") if reference else None
        )
        reference_weight_state = (
            str(reference.get("weight_state") or "") or None
            if reference
            else None
        )
        reference_weight_source = (
            "oem_reference"
            if reference
            and (
                reference_actual_weight_kg is not None
                or reference_volume_weight_kg is not None
            )
            else None
        )

        prepared_items.append(
            (
                order_id,
                position,
                str(item.get("manufacturer") or ""),
                current_oem,
                requested_oem,
                str(item.get("name") or ""),
                qty,
                price_usd,
                auto_item_type,
                auto_item_type_source,
                dealer_price_usd,
                dealer_price_source,
                dealer_price_checked_at,
                dealer_price_status,
                customer_unit_rub,
                reference_actual_weight_kg,
                reference_volume_weight_kg,
                reference_weight_state,
                reference_weight_source,
                str(item.get("offer_source") or "usa"),
                item.get("warehouse_id"),
                item.get("warehouse_public_name"),
                item.get("price_snapshot_rub"),
                item.get("available_snapshot"),
                item.get("selected_delivery_tariff"),
                item.get("delivery_selected_at"),
            )
        )

    customer_total_rub = (
        int(auto_customer_total_rub) if auto_pricing_complete else None
    )
    auto_pricing_status = (
        "calculated" if auto_pricing_complete else "needs_review"
    )
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        try:
            conn.execute("BEGIN")

            conn.execute(
                """
                INSERT INTO orders (
                    order_id,
                    created_at,
                    telegram_user_id,
                    customer_name,
                    username,
                    total_usd,
                    usd_rub_rate,
                    customer_total_rub,
                    pricing_coefficient,
                    auto_pricing_status,
                    origin
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    order_id,
                    created_at,
                    int(user.id),
                    str(user.full_name or ""),
                    str(user.username or ""),
                    total_usd,
                    float(USD_RUB_RATE),
                    customer_total_rub,
                    float(PRICE_COEFFICIENT),
                    auto_pricing_status,
                    origin,
                ),
            )

            if delivery_preference and delivery_preference != "local_only":
                conn.execute(
                    "UPDATE orders SET delivery_tariff = ?, updated_at = ? WHERE order_id = ?",
                    (delivery_preference, created_at, order_id),
                )

            fulfillment_groups = {}
            for item in cart.values():
                source = str(item.get("offer_source") or "usa")
                if source == "warehouse":
                    warehouse_id = int(item.get("warehouse_id") or 0)
                    group_key = f"warehouse:{warehouse_id}"
                    fulfillment_groups[group_key] = (
                        "warehouse",
                        warehouse_id,
                        str(item.get("warehouse_public_name") or "") or None,
                        None,
                    )
                else:
                    fulfillment_groups["usa"] = (
                        "usa",
                        None,
                        None,
                        delivery_preference if delivery_preference != "local_only" else None,
                    )
            for group_key, group in fulfillment_groups.items():
                source_type, warehouse_id, warehouse_public_name, group_delivery = group
                conn.execute(
                    """
                    INSERT INTO order_fulfillment_groups (
                        order_id, group_key, source_type, warehouse_id,
                        warehouse_public_name, delivery_preference, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        order_id, group_key, source_type, warehouse_id,
                        warehouse_public_name, group_delivery, created_at,
                    ),
                )

            for prepared in prepared_items:
                (
                    item_order_id,
                    position,
                    manufacturer,
                    oem,
                    requested_oem,
                    name,
                    quantity,
                    price_usd,
                    auto_item_type,
                    auto_item_type_source,
                    dealer_price_usd,
                    dealer_price_source,
                    dealer_price_checked_at,
                    dealer_price_status,
                    customer_unit_rub,
                    reference_actual_weight_kg,
                    reference_volume_weight_kg,
                    reference_weight_state,
                    reference_weight_source,
                    offer_source,
                    warehouse_id,
                    warehouse_public_name,
                    price_snapshot_rub,
                    available_snapshot,
                    selected_delivery_tariff,
                    delivery_selected_at,
                ) = prepared

                profile = conn.execute(
                    """
                    SELECT item_type
                    FROM oem_delivery_profiles
                    WHERE manufacturer = ? COLLATE NOCASE
                      AND oem = ? COLLATE NOCASE
                    LIMIT 1
                    """,
                    (manufacturer, oem),
                ).fetchone()
                resolved_item_type = profile[0] if profile else auto_item_type
                resolved_item_type_source = (
                    "manual_profile" if profile else auto_item_type_source
                )
                allowed_delivery = ITEM_TYPE_ALLOWED_TARIFFS.get(
                    str(resolved_item_type or ""),
                    (),
                )
                resolved_delivery_tariff = (
                    str(selected_delivery_tariff or "").strip().lower()
                    if str(offer_source or "usa").strip().lower() == "usa"
                    and str(selected_delivery_tariff or "").strip().lower()
                    in allowed_delivery
                    else None
                )
                resolved_delivery_selected_at = (
                    str(delivery_selected_at or created_at)
                    if resolved_delivery_tariff
                    else None
                )

                conn.execute(
                    """
                    INSERT INTO order_items (
                        order_id,
                        position,
                        manufacturer,
                        oem,
                        requested_oem,
                        name,
                        quantity,
                        price_usd,
                        item_type,
                        item_type_source,
                        dealer_price_usd,
                        dealer_price_source,
                        dealer_price_checked_at,
                        dealer_price_status,
                        customer_unit_rub,
                        reference_actual_weight_kg,
                        reference_volume_weight_kg,
                        reference_weight_state,
                        reference_weight_source,
                        offer_source,
                        warehouse_id,
                        warehouse_public_name,
                        price_snapshot_rub,
                        available_snapshot,
                        selected_delivery_tariff,
                        delivery_selected_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item_order_id,
                        position,
                        manufacturer,
                        oem,
                        requested_oem,
                        name,
                        quantity,
                        price_usd,
                        resolved_item_type,
                        resolved_item_type_source,
                        dealer_price_usd,
                        dealer_price_source,
                        dealer_price_checked_at,
                        dealer_price_status,
                        customer_unit_rub,
                        reference_actual_weight_kg,
                        reference_volume_weight_kg,
                        reference_weight_state,
                        reference_weight_source,
                        offer_source,
                        warehouse_id,
                        warehouse_public_name,
                        price_snapshot_rub,
                        available_snapshot,
                        resolved_delivery_tariff,
                        resolved_delivery_selected_at,
                    ),
                )

            conn.commit()

        except Exception:
            conn.rollback()
            raise


MANUFACTURERS = [
    "Arctic Cat",
    "Can-Am",
    "CFMoto",
    "Honda",
    "Indian",
    "Kawasaki",
    "KTM",
    "Polaris",
    "Sea-Doo",
    "Ski-Doo",
    "Suzuki",
    "Yamaha",
    "Aftermarket",
]

CALLBACK_TO_MANUFACTURER = {
    f"mfg:{i}": manufacturer
    for i, manufacturer in enumerate(MANUFACTURERS)
}

MAX_CONCURRENT_SEARCHES = 1

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
# Telegram's HTTP client logs full request URLs at INFO level, and Telegram
# bot tokens are embedded in those URLs. Never persist them in Railway logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger("telegram_bot_v2_5")


# ---------------------------------------------------------------------------
# V2.7.3 CUSTOMER RUB PRICE + TELEGRAM FLOOD CONTROL
# ---------------------------------------------------------------------------

def customer_rub_price(usd_price):
    """Convert internal US MSRP to customer-facing RUB. USD is never shown to customers."""
    if not isinstance(usd_price, (int, float)):
        return None
    return int(round(float(usd_price) * USD_RUB_RATE))

def format_rub(value) -> str:
    if not isinstance(value, (int, float)):
        return "—"

    rounded_value = math.ceil(value / 50) * 50
    return f"{rounded_value:,}".replace(",", " ") + " ₽"

async def safe_reply_text(message, *args, **kwargs):
    """Send one Telegram reply and transparently respect RetryAfter flood control."""
    try:
        return await message.reply_text(*args, **kwargs)
    except RetryAfter as exc:
        retry_after = float(getattr(exc, "retry_after", 1) or 1)
        log.warning("Telegram flood control: retrying reply in %.1f sec", retry_after)
        await asyncio.sleep(retry_after + 0.5)
        return await message.reply_text(*args, **kwargs)

# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def ecosystem_navigation_rows() -> list[list[InlineKeyboardButton]]:
    """Shared navigation between media, community and service layers."""
    row = []
    if EXTREMIZER_CHANNEL_URL:
        row.append(InlineKeyboardButton("📡 EXTREMIZER PRO", url=EXTREMIZER_CHANNEL_URL))
    if EXTREMIZER_CLUB_URL:
        row.append(InlineKeyboardButton("🔥 EXTREMIZER | CLUB", url=EXTREMIZER_CLUB_URL))
    return [row] if row else []


def manufacturer_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(MANUFACTURERS), 2):
        row = []
        for j in range(i, min(i + 2, len(MANUFACTURERS))):
            row.append(
                InlineKeyboardButton(
                    MANUFACTURERS[j],
                    callback_data=f"mfg:{j}",
                )
            )
        rows.append(row)
    rows.extend(ecosystem_navigation_rows())
    return InlineKeyboardMarkup(rows)


def change_manufacturer_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("🏭 Сменить производителя", callback_data="change_mfg")]]
    )


def result_keyboard(*, allow_retry: bool = False) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again"),
            InlineKeyboardButton("🏭 Сменить производителя", callback_data="change_mfg"),
        ]
    ]
    if allow_retry:
        rows.insert(0, [InlineKeyboardButton("🔄 Повторить запрос", callback_data="retry_last")])
    return InlineKeyboardMarkup(rows)


def mixed_result_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[
            InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again"),
            InlineKeyboardButton("🏭 Выбрать производителя", callback_data="change_mfg"),
        ]]
    )


def _cart_item_key(result: dict, offer_source: str = "usa") -> str:
    manufacturer = str(result.get("manufacturer") or "")
    item = str(result.get("item_sku") or result.get("oem") or result.get("query_oem") or "")
    source = str(offer_source or "usa").strip().lower()
    return f"{manufacturer}|{item}|{source}"


def _warehouse_offer_key(result: dict, warehouse_id: int) -> str:
    return _cart_item_key(result, f"warehouse:{int(warehouse_id)}")


def found_keyboard(result: dict) -> InlineKeyboardMarkup:
    rows = []
    usa_key = _cart_item_key(result, "usa")
    customer_price_rub = customer_rub_price_from_dp(
        result.get("_dealer_price_usd"),
        rate=load_usd_rub_rate(),
    )
    if customer_price_rub is not None:
        rows.append([
            InlineKeyboardButton(
                f"🛒 В корзину: 🇺🇸 склад США • {format_rub(customer_price_rub)}",
                callback_data=f"cartadd:{usa_key}",
            )
        ])

    oem = _client_stock_oem(result)
    for offer in warehouse_stock_service.client_stock_summary(
        oem,
        db_file=ORDERS_DB_FILE,
    ):
        if not offer.get("is_fresh"):
            continue
        available = offer.get("available_quantity")
        price_rub = offer.get("price_rub")
        if available is None or float(available) <= 0 or price_rub is None:
            continue
        key = _warehouse_offer_key(result, int(offer["warehouse_id"]))
        rows.append([
            InlineKeyboardButton(
                f"🛒 В корзину: 🇷🇺 {offer['public_name']} • {format_rub(float(price_rub))}",
                callback_data=f"cartadd:{key}",
            )
        ])

    if not rows:
        rows.append([InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again")])
    else:
        rows.append([
            InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again"),
            InlineKeyboardButton("🛒 Моя корзина", callback_data="cart"),
        ])
    rows.append([InlineKeyboardButton("🏭 Сменить производителя", callback_data="change_mfg")])
    return InlineKeyboardMarkup(rows)


def _requested_oem_from_result_message(query, current_oem: str) -> str:
    """Recover the originally requested OEM from a rendered FOUND card."""
    message = getattr(query, "message", None)
    text = str(getattr(message, "text", "") or "")
    match = re.search(
        r"Запрошен\s+OEM:\s*([A-Za-z0-9._/-]+)",
        text,
        flags=re.IGNORECASE,
    )
    if match:
        requested = finder.normalize_oem(match.group(1))
        if requested:
            return requested
    return finder.normalize_oem(current_oem)


async def restore_found_item_from_cache(query, key: str) -> dict | None:
    """Rebuild a cart-add result after bot/Railway restart from persistent caches."""
    if "|" not in key:
        return None

    manufacturer, current_oem = key.split("|", 1)
    canonical = finder.manufacturer_alias(manufacturer)
    normalized_current = finder.normalize_oem(current_oem)
    if not canonical or not normalized_current:
        return None

    requested_oem = _requested_oem_from_result_message(query, normalized_current)
    result = (
        public_msrp_cache_result(canonical, requested_oem)
        or public_msrp_cache_result(canonical, normalized_current)
    )
    if not result or str(result.get("status") or "").upper() != "FOUND":
        return None

    resolved_current = finder.normalize_oem(
        str(result.get("item_sku") or result.get("oem") or "")
    )
    if resolved_current != normalized_current:
        return None

    result["query_oem"] = requested_oem or normalized_current
    return await enrich_found_result_with_dealer_price(result)


def cache_client_offer_result(context, result: dict) -> None:
    found = context.user_data.setdefault("found_items", {})
    found[_cart_item_key(result, "usa")] = result
    oem = _client_stock_oem(result)
    for offer in warehouse_stock_service.client_stock_summary(oem, db_file=ORDERS_DB_FILE):
        if offer.get("is_fresh") and offer.get("available_quantity") is not None:
            found[_warehouse_offer_key(result, int(offer["warehouse_id"]))] = result


def keyboard_for_result(result: dict) -> InlineKeyboardMarkup:
    status = str(result.get("status") or "").upper()
    if status in {"FOUND", "PARTIAL"}:
        return found_keyboard(result)
    technical = status == "NOT_FOUND" and technical_failure_reason(result) is not None
    return result_keyboard(allow_retry=technical)


def cart_keyboard(cart: dict) -> InlineKeyboardMarkup:
    rows = []
    for key, item in cart.items():
        label = str(item.get("oem") or "позиция")
        rows.append([
            InlineKeyboardButton(f"➖  {label} × {item.get('qty', 1)}", callback_data=f"cartminus:{key}"),
            InlineKeyboardButton("➕", callback_data=f"cartplus:{key}"),
            InlineKeyboardButton("🗑", callback_data=f"cartremove:{key}"),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить позицию", callback_data="search_again")])
    if cart:
        rows.append([InlineKeyboardButton("✅ Оформить запрос", callback_data="checkout")])
        rows.append([InlineKeyboardButton("🗑 Очистить корзину", callback_data="cartclear")])
    return InlineKeyboardMarkup(rows)


def format_cart(cart: dict, checkout: bool = False) -> str:
    if not cart:
        return "🛒 <b>Моя корзина:</b>\n\nКорзина пока пуста."

    lines = ["📦 <b>Проверь запрос:</b>" if checkout else "🛒 <b>Моя корзина:</b>", ""]
    total_rub = 0.0
    groups: dict[tuple, dict] = {}
    for item in cart.values():
        source = str(item.get("offer_source") or "usa")
        if source == "warehouse":
            warehouse_id = int(item.get("warehouse_id") or 0)
            group_key = ("warehouse", warehouse_id)
            label = f"🇷🇺 {item.get('warehouse_public_name') or 'склад'}"
        else:
            group_key = ("usa", 0)
            label = "🇺🇸 ИЗ США"
        group = groups.setdefault(group_key, {"label": label, "items": []})
        group["items"].append(item)

    for group in groups.values():
        group_label = str(group["label"])
        items = group["items"]
        lines.append(f"<b>{escape(group_label)}</b>")
        lines.append("")
        for item in items:
            qty = int(item.get("qty", 1))
            source = str(item.get("offer_source") or "usa")
            if source == "warehouse":
                rub_price = item.get("price_snapshot_rub")
            else:
                rub_price = customer_rub_price_from_dp(item.get("_dealer_price_usd"))
                if rub_price is None:
                    rub_price = item.get("price_snapshot_rub")
            subtotal_rub = float(rub_price) * qty if rub_price is not None else None
            if subtotal_rub is not None:
                total_rub += subtotal_rub
            current_oem = escape(str(item.get("oem") or "—"))
            lines.append(f"<b><code>{current_oem}</code></b> — <b>{qty} шт.</b>")
            if rub_price is not None:
                lines.append(
                    f"{format_rub(float(rub_price))} × {qty} = "
                    f"<b>{format_rub(subtotal_rub)}</b>"
                )
            else:
                lines.append("Цена: —")
            lines.append("")
        if group_label == "🇺🇸 ИЗ США":
            lines.append("🚚 Доставка из США оплачивается отдельно.")
            lines.append("")

    lines.append(f"<b>Товары: {format_rub(total_rub)}</b>")
    lines.append(f"📦 <b>Отправлений: {len(groups)}</b>")
    lines.append("")
    if checkout:
        lines.append("Если всё верно, жми ✅ <b>Подтвердить запрос</b>.")
    else:
        lines.append("Если всё верно, жми ✅ <b>Оформить запрос</b>.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# V2.1 TECHNICAL-FAILURE GUARD
# ---------------------------------------------------------------------------

def _attempt_has_technical_failure(attempt: dict) -> bool:
    """
    Inspect V6.6 diagnostics only.
    Does NOT change any V6.6 search/parser rule.
    """
    if not isinstance(attempt, dict):
        return False

    timing = attempt.get("timing") or {}
    error = str(attempt.get("error") or "").strip()
    error_l = error.lower()

    # Explicit Cloudflare flags emitted by V6.6 direct paths.
    cloudflare_keys = (
        "cloudflare_in_parts_response",
        "cloudflare_in_direct_response",
        "cloudflare_in_product_response",
    )
    if any(timing.get(key) is True for key in cloudflare_keys):
        return True

    # Direct HTTP response was blocked.
    status_keys = (
        "direct_parts_http_status",
        "http_status",
        "direct_product_http_status",
    )
    for key in status_keys:
        status = timing.get(key)
        if status in (401, 403, 429, 500, 502, 503, 504):
            return True

    # V6.6 direct request failed and had to use browser fallback.
    # Fallback itself is not automatically an error; we only flag it when
    # V6.6 also reports an error / Cloudflare / unusable response.
    diagnostic_errors = " ".join(
        str(timing.get(key) or "")
        for key in (
            "direct_parts_error",
            "direct_error",
            "direct_product_error",
        )
    ).lower()

    technical_words = (
        "cloudflare",
        "verification",
        "challenge",
        "http 403",
        "http 429",
        "unusable",
        "timeout",
        "timed out",
        "execution context was destroyed",
        "form not found",
    )

    combined = f"{error_l} {diagnostic_errors}"
    if any(word in combined for word in technical_words):
        return True

    # A catalog attempt explicitly ended as ERROR.
    if str(attempt.get("status") or "").upper() == "ERROR":
        return True

    return False


def technical_failure_reason(result: dict) -> str | None:
    """
    Return an internal diagnostic reason if NOT_FOUND is unreliable.
    Genuine clean NOT_FOUND returns None.
    """
    if not isinstance(result, dict):
        return "invalid result object"

    if str(result.get("status") or "").upper() != "NOT_FOUND":
        return None

    attempts = result.get("_attempts") or []
    bad_attempts = []

    for attempt in attempts:
        if _attempt_has_technical_failure(attempt):
            catalog = attempt.get("catalog") or "unknown catalog"
            error = attempt.get("error")
            timing = attempt.get("timing") or {}

            details = []
            if timing.get("cloudflare_in_parts_response"):
                details.append("Cloudflare Parts")
            if timing.get("cloudflare_in_direct_response"):
                details.append("Cloudflare catalog")
            if timing.get("cloudflare_in_product_response"):
                details.append("Cloudflare product")

            for key in (
                "direct_parts_http_status",
                "http_status",
                "direct_product_http_status",
            ):
                status = timing.get(key)
                if status and status != 200:
                    details.append(f"HTTP {status}")

            if error:
                details.append(str(error))

            if not details:
                details.append("technical failure")

            bad_attempts.append(f"{catalog}: {', '.join(details)}")

    if bad_attempts:
        return "; ".join(bad_attempts)

    return None


def _client_stock_oem(result: dict) -> str:
    return str(
        result.get("item_sku")
        or result.get("oem")
        or result.get("query_oem")
        or ""
    ).strip()


def _format_client_stock_message(oem: str) -> str | None:
    rows = warehouse_stock_service.client_stock_summary(
        oem,
        db_file=ORDERS_DB_FILE,
    )
    if not rows:
        return None

    positive = [
        row
        for row in rows
        if row.get("is_fresh")
        and row.get("available_quantity") is not None
        and float(row["available_quantity"]) > 0
    ]

    if positive:
        lines = [
            "🏬 <b>Наличие на складах</b>",
            f"OEM: <code>{escape(oem)}</code>",
            "",
        ]
        for row in positive:
            qty = float(row["available_quantity"])
            price_rub = row.get("price_rub")
            if price_rub is not None:
                lines.append(
                    f"• <b>{escape(str(row['public_name']))}</b> — "
                    f"<b>{format_rub(float(price_rub))}</b> • "
                    f"<b>{qty:g} шт.</b>"
                )
            else:
                lines.append(
                    f"• <b>{escape(str(row['public_name']))}</b> — "
                    f"<b>{qty:g} шт.</b>"
                )
        return "\n".join(lines)

    all_known = all(
        row.get("is_fresh")
        and row.get("available_quantity") is not None
        for row in rows
    )
    if all_known:
        return (
            "🏬 <b>Наличие на складах</b>\n"
            f"OEM: <code>{escape(oem)}</code>\n\n"
            "Подтверждённого остатка сейчас нет."
        )

    return None


async def _refresh_client_stock_and_notify(
    application: Application,
    message,
    result: dict,
) -> None:
    if str(result.get("status") or "").upper() not in {"FOUND", "PARTIAL"}:
        return

    chat = getattr(message, "chat", None)
    if not chat or getattr(chat, "type", None) != "private":
        return

    oem = _client_stock_oem(result)
    if not oem:
        return

    pending = application.bot_data.setdefault(
        "client_stock_pending",
        set(),
    )
    pending_key = (getattr(chat, "id", None), oem)
    if pending_key in pending:
        return
    pending.add(pending_key)

    try:
        semaphore = application.bot_data.get(
            "client_stock_refresh_semaphore"
        )
        if semaphore is None:
            semaphore = asyncio.Semaphore(1)
            application.bot_data[
                "client_stock_refresh_semaphore"
            ] = semaphore

        async with semaphore:
            rows = warehouse_stock_service.client_stock_summary(
                oem,
                db_file=ORDERS_DB_FILE,
            )
            all_fresh = bool(rows) and all(
                row.get("is_fresh")
                for row in rows
            )

            if not all_fresh:
                warehouses = warehouse_store.list_warehouses(
                    include_inactive=False,
                    db_file=ORDERS_DB_FILE,
                )
                refresh_jobs = [
                    asyncio.to_thread(
                        warehouse_stock_service.refresh_warehouse_oem,
                        int(warehouse["id"]),
                        oem,
                        ORDERS_DB_FILE,
                    )
                    for warehouse in warehouses
                ]
                if refresh_jobs:
                    await asyncio.gather(
                        *refresh_jobs,
                        return_exceptions=True,
                    )

            stock_text = _format_client_stock_message(oem)
            if stock_text:
                await safe_reply_text(
                    message,
                    stock_text,
                    parse_mode=ParseMode.HTML,
                )
    except Exception:
        log.exception(
            "Client stock refresh failed for OEM %s",
            oem,
        )
    finally:
        pending.discard(pending_key)


def _schedule_client_stock_update(
    context: ContextTypes.DEFAULT_TYPE,
    message,
    result: dict,
) -> None:
    if str(result.get("status") or "").upper() != "FOUND":
        return

    context.application.create_task(
        _refresh_client_stock_and_notify(
            context.application,
            message,
            result,
        )
    )


def _client_safe_item_name(value) -> str:
    """Remove storefront/site branding from item names shown to customers."""
    text = str(value or "—").strip()
    text = re.sub(
        r"\s*\|\s*Интернет-магазин\b.*$",
        "",
        text,
        flags=re.I,
    ).strip()
    text = re.sub(
        r"\s*[-—]\s*Интернет-магазин\b.*$",
        "",
        text,
        flags=re.I,
    ).strip()
    return text or "—"


def format_client_offer_card(result: dict) -> str:
    status = str(result.get("status") or "").upper()
    if status not in {"FOUND", "PARTIAL"}:
        return format_result(result)

    oem = _client_stock_oem(result)
    manufacturer = escape(str(result.get("manufacturer") or "—"))
    lines = [f"🔎 <b>{escape(oem)}</b>", "", f"<b>{manufacturer}</b>"]

    # A PARTIAL identity may come from warehouse/OEM-reference data.  Warehouse
    # descriptions are supplier metadata and must never become a customer title.
    if status == "FOUND":
        clean_name = _client_safe_item_name(result.get("name"))
        if clean_name and clean_name != "—":
            lines.append(f"<i>{escape(clean_name)}</i>")

    customer_price = customer_rub_price_from_dp(
        result.get("_dealer_price_usd"),
        rate=load_usd_rub_rate(),
    )
    rrp_rub = customer_rub_price(result.get("price"))

    lines.append("")
    if customer_price is not None:
        lines.append(f"🇺🇸 <b>склад США— {format_rub(customer_price)}</b>")
        if rrp_rub is not None and rrp_rub > customer_price:
            lines.append(f"РРЦ: {format_rub(rrp_rub)}")
            benefit_pct = (rrp_rub - customer_price) / rrp_rub * 100
            lines.append(f"<b>Выгода:</b> {benefit_pct:.1f}%")
        lines.append("🚚 Доставка из США оплачивается отдельно.")
    else:
        lines.append("🇺🇸 <b>склад США— цена уточняется</b>")

    offers = []
    for row in warehouse_stock_service.client_stock_summary(oem, db_file=ORDERS_DB_FILE):
        if not row.get("is_fresh"):
            continue
        qty = row.get("available_quantity")
        if qty is None or float(qty) <= 0:
            continue
        offers.append(row)

    if offers:
        lines += ["", "🇷🇺 <b>В НАЛИЧИИ В РФ</b>", ""]
        for row in offers:
            qty = float(row["available_quantity"])
            price = row.get("price_rub")
            warehouse = escape(str(row.get("public_name") or "склад"))
            if price is not None:
                lines.append(
                    f"<b>{warehouse} — {format_rub(float(price))} • {qty:g} шт.</b>"
                )
            else:
                lines.append(f"<b>{warehouse} — {qty:g} шт. • цена уточняется</b>")

    return "\n".join(lines)


def format_result(result: dict) -> str:
    status = result.get("status")

    if status == "FOUND":
        manufacturer = escape(str(result.get("manufacturer") or "—"))
        catalog = escape(str(result.get("catalog") or "—"))
        raw_item = str(result.get("item_sku") or result.get("oem") or "—")
        item = escape(raw_item)
        requested_oem = str(result.get("query_oem") or raw_item).strip()
        name = escape(_client_safe_item_name(result.get("name")))

        # Public pricing: MSRP -> RRP; private DP -> customer price.
        # DP itself is never rendered to the client.
        msrp_usd = result.get("price")
        rrp_rub = customer_rub_price(msrp_usd)
        customer_price_rub = customer_rub_price_from_dp(
            result.get("_dealer_price_usd"),
            rate=load_usd_rub_rate(),
        )

        lines = [
            "✅ <b>Найдено</b>",
            "",
            f"<b>Производитель:</b> {manufacturer}",
            f"<b>Каталог:</b> {catalog}",
            f"<b>OEM / Item:</b> <code>{item}</code>",
        ]

        if requested_oem and requested_oem != raw_item:
            lines.append(
                f"<b>Запрошен OEM:</b> <code>{escape(requested_oem)}</code> → "
                f"<b>актуальный:</b> <code>{item}</code>"
            )

        dist_number = result.get("dist_number")
        if dist_number:
            lines.append(f"<b>Dist #:</b> <code>{escape(str(dist_number))}</code>")

        lines.append(f"<b>Название:</b> {name}")
        if customer_price_rub is not None:
            if rrp_rub is not None and rrp_rub > customer_price_rub:
                lines.append(f"<b>РРЦ:</b> {format_rub(rrp_rub)}")
                lines.append(f"<b>Ваша цена:</b> {format_rub(customer_price_rub)}")
                benefit_pct = (rrp_rub - customer_price_rub) / rrp_rub * 100
                lines.append(f"<b>Выгода:</b> {benefit_pct:.1f}%")
            else:
                lines.append(f"<b>Ваша цена:</b> {format_rub(customer_price_rub)}")
        else:
            # Safe fallback while DP is unavailable: never label a calculated
            # customer price as RRP. MSRP-derived RRP remains public.
            lines.append(f"<b>РРЦ:</b> {format_rub(rrp_rub)}")

        previous = result.get("previous_oems") or []
        if previous:
            lines.append("")
            lines.append(
                "<b>Предыдущие OEM:</b> "
                + ", ".join(f"<code>{escape(str(x))}</code>" for x in previous)
            )

        return "\n".join(lines)

    if status == "PARTIAL":
        manufacturer = escape(str(result.get("manufacturer") or "—"))
        item = escape(str(result.get("item_sku") or result.get("oem") or result.get("query_oem") or "—"))
        name = escape(_client_safe_item_name(result.get("name")))
        item_type = str(result.get("_resolved_item_type") or "").strip()
        lines = [
            "✅ <b>OEM определён</b>",
            "",
            f"<b>Производитель:</b> {manufacturer}",
            f"<b>OEM / Item:</b> <code>{item}</code>",
        ]
        if name and name != "—":
            lines.append(f"<b>Название:</b> {name}")
        if item_type:
            lines.append(f"<b>Тип позиции:</b> {escape(item_type)}")
        lines += ["", "⚠️ Цена сейчас уточняется. Наличие на складах проверяется независимо."]
        return "\n".join(lines)

    if status == "HUMAN_REVIEW":
        manufacturer = escape(str(result.get("manufacturer") or "—"))
        item = escape(
            str(
                result.get("item_sku")
                or result.get("oem")
                or result.get("query_oem")
                or "—"
            )
        )
        name = escape(_client_safe_item_name(result.get("name")))

        lines = [
            "⚠️ <b>Нужна ручная проверка</b>",
            "",
            f"<b>Производитель:</b> {manufacturer}",
            f"<b>OEM / Item:</b> <code>{item}</code>",
            f"<b>Название:</b> {name}",
            "",
            "По этому номеру нет подтверждённой актуальной цены.",
        ]

        previous = result.get("previous_oems") or []
        replacement = result.get("replacement_oem")

        if previous:
            lines.append(
                "<b>Предыдущие OEM:</b> "
                + ", ".join(f"<code>{escape(str(x))}</code>" for x in previous)
            )
        if replacement:
            lines.append(
                f"<b>Возможная замена:</b> <code>{escape(str(replacement))}</code>"
            )

        return "\n".join(lines)

    if status == "NOT_FOUND":
        manufacturer = escape(str(result.get("manufacturer") or "—"))
        query = escape(str(result.get("query_oem") or "—"))

        technical_reason = technical_failure_reason(result)
        if technical_reason:
            log.warning(
                "V2.1 suppressed false NOT_FOUND for %s / %s: %s",
                result.get("manufacturer"),
                result.get("query_oem"),
                technical_reason,
            )
            return (
                "⚠️ <b>Не удалось получить данные по этой позиции</b>\n\n"
                f"<b>Производитель:</b> {manufacturer}\n"
                f"<b>Запрос:</b> <code>{query}</code>\n\n"
                "Попробуй повторить запрос чуть позже."
            )

        return (
            "❌ <b>Номер не найден</b>\n\n"
            f"<b>Производитель:</b> {manufacturer}\n"
            f"<b>Запрос:</b> <code>{query}</code>\n\n"
            "Проверь номер или выбери другого производителя."
        )

    return (
        "⚠️ <b>Не удалось получить данные по запросу</b>\n\n"
        "Попробуй повторить запрос чуть позже."
    )


# ---------------------------------------------------------------------------
# V6.6 SERVICE — UNCHANGED SEARCH PATH
# ---------------------------------------------------------------------------

class FinderService:
    def __init__(self):
        self.pw = None
        self.browser = None
        self.context = None
        self.page = None
        self.lock = asyncio.Semaphore(MAX_CONCURRENT_SEARCHES)

    async def start(self):
        if DCP_CACHE_ONLY:
            print("V6.6 SEARCH ENGINE: CACHE-ONLY MODE")
            return

        self.pw = await async_playwright().start()

        print("Connecting V6.6 to existing Chrome:", finder.CDP_URL)
        self.browser = await self.pw.chromium.connect_over_cdp(finder.CDP_URL)

        if not self.browser.contexts:
            raise RuntimeError("Chrome context not found.")

        self.context = self.browser.contexts[0]
        self.page = (
            self.context.pages[0]
            if self.context.pages
            else await self.context.new_page()
        )

        # Same speed route used by V6.6.
        await finder.install_speed_routes(self.page)

        print("V6.6 SEARCH ENGINE READY")

    async def stop(self):
        # Do not close the user's Chrome.
        # Only stop Playwright client.
        if self.pw:
            await self.pw.stop()

    async def _page_has_cloudflare_challenge(self) -> bool:
        """Telegram-layer diagnostic only; does not alter V6.6 parsing/search rules."""
        try:
            title = (await self.page.title()).lower()
            body = (await self.page.locator("body").inner_text(timeout=2500)).lower()
        except Exception:
            return False

        markers = (
            "verify you are human",
            "verifying you are human",
            "performing security verification",
            "security verification",
            "checking your browser",
            "just a moment",
            "cloudflare",
            "подтвердите, что вы человек",
            "проверка безопасности",
        )
        text = f"{title}\n{body}"
        return any(marker in text for marker in markers)

    async def _recover_catalog_session(self, target_url: str | None) -> bool:
        """
        Best-effort recovery of the already-authorized Chrome session.
        No challenge bypassing: if Cloudflare asks for a human check, V2.5.1
        waits for that check to be completed in the attached Chrome window.
        """
        print("V2.5.1 SESSION RECOVERY: starting")

        try:
            if target_url:
                await self.page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
        except Exception as exc:
            print("V2.5.1 SESSION RECOVERY: navigation warning:", type(exc).__name__, exc)

        # Give ordinary cookie/session refresh a brief chance first. If an
        # interactive challenge is visible, wait for manual completion.
        deadline = asyncio.get_running_loop().time() + 60.0
        challenge_seen = False

        while asyncio.get_running_loop().time() < deadline:
            challenged = await self._page_has_cloudflare_challenge()
            if not challenged:
                print("V2.5.1 SESSION RECOVERY: Chrome session looks usable")
                return True

            if not challenge_seen:
                challenge_seen = True
                print("CLOUDFLARE_REAUTH_REQUIRED")
                print("Complete the verification manually in the attached Chrome window.")
                print("The Telegram request is being held and will retry automatically.")

            await asyncio.sleep(2.0)

        print("V2.5.1 SESSION RECOVERY: timed out waiting for usable Chrome session")
        return False

    async def _cache_verified_public_result(self, result: dict) -> None:
        """Cache only public MSRP metadata; never cache visible dealer price."""
        if str(result.get("status") or "").upper() != "FOUND":
            return
        if result.get("_oem_catalog_cache"):
            return

        price = result.get("price")
        verified_msrp = None
        source_kind = None

        # A result from the old validated JSON bootstrap is already safe.
        if result.get("_public_msrp_cache"):
            if isinstance(price, (int, float)) and float(price) > 0:
                verified_msrp = float(price)
                source_kind = "validated_public_msrp_cache"

        # For live DCP pages, independently re-read only public form metadata.
        # Never use .c2/.dbl here because an authorized session can show DP.
        if verified_msrp is None and self.page is not None:
            current_oem = finder.normalize_oem(
                str(result.get("item_sku") or result.get("oem") or "")
            )
            if current_oem:
                try:
                    public_price_text = await self.page.evaluate(
                        """(oem) => {
                          const forms=[...document.querySelectorAll('form')];
                          const form=forms.find(f =>
                            f.id === ('add_' + oem) ||
                            (f.querySelector('input[name="sku"]')?.value || '').trim() === oem
                          );
                          if(!form) return null;
                          const values=[
                            form.getAttribute('data-retail'),
                            form.querySelector('input[name="retail"]')?.value,
                            form.querySelector('input[name="msrp"]')?.value,
                            form.querySelector('[data-retail]')?.getAttribute('data-retail')
                          ];
                          return values.find(v => String(v || '').trim()) || null;
                        }""",
                        current_oem,
                    )
                    parsed_public_price = finder.money(public_price_text)
                    if (
                        isinstance(parsed_public_price, (int, float))
                        and float(parsed_public_price) > 0
                    ):
                        verified_msrp = float(parsed_public_price)
                        source_kind = "live_public_form_metadata"
                except Exception:
                    log.exception(
                        "Could not verify public MSRP metadata for OEM cache: %s",
                        current_oem,
                    )

        if verified_msrp is None:
            return

        upsert_oem_catalog_cache(
            result,
            verified_msrp,
            source_kind=source_kind or "validated_public_msrp",
            source_ref=str(result.get("search_url") or "") or None,
        )

    async def search(self, manufacturer: str, raw_oem: str) -> dict:
        manufacturer = finder.manufacturer_alias(manufacturer)
        if not manufacturer:
            raise ValueError("Unknown manufacturer.")

        oem = finder.normalize_oem(raw_oem)

        async with self.lock:
            # If Playwright never attached to Chrome, a validated public MSRP
            # snapshot may still safely satisfy this exact lookup. Never use
            # an authenticated visible dealer price as MSRP.
            if self.page is None:
                cached = public_msrp_cache_result(manufacturer, oem)
                if cached:
                    cached["_telegram_session_recovery"] = {
                        "attempted": False,
                        "recovered": False,
                        "fallback": "validated_public_msrp_cache",
                        "initial_reason": (
                            "cache_only"
                            if DCP_CACHE_ONLY
                            else "finder_page_unavailable"
                        ),
                    }
                    return cached

                if DCP_CACHE_ONLY:
                    return {
                        "found": False,
                        "status": "CACHE_MISS",
                        "manufacturer": manufacturer,
                        "query_oem": oem,
                        "_diagnostic": {
                            "match_rule": "cache_only_miss",
                            "price_source": None,
                        },
                    }

            # FIRST ATTEMPT: frozen V6.6, unchanged.
            result = await finder.find_oem(
                self.page,
                manufacturer,
                oem,
            )

            reason = technical_failure_reason(result)
            if not reason:
                await self._cache_verified_public_result(result)
                return result

            # Before recovery, prefer a validated public MSRP snapshot over
            # reading a visible price from an authenticated dealer page.
            cached = public_msrp_cache_result(manufacturer, oem)
            if cached:
                cached["_telegram_session_recovery"] = {
                    "attempted": True,
                    "recovered": False,
                    "fallback": "validated_public_msrp_cache",
                    "initial_reason": reason,
                }
                return cached

            # Telegram-only recovery layer. A technical false-NOT_FOUND never
            # changes V6.6's result rules; it only triggers one session recovery
            # and one retry of the exact same request.
            print(f"V2.5.1 TECHNICAL RESULT: {manufacturer} / {oem}: {reason}")
            target_url = result.get("search_url")
            recovered = await self._recover_catalog_session(target_url)

            if recovered:
                print(f"V2.5.1 AUTO RETRY: {manufacturer} / {oem}")
                retry_result = await finder.find_oem(
                    self.page,
                    manufacturer,
                    oem,
                )
                retry_result["_telegram_session_recovery"] = {
                    "attempted": True,
                    "recovered": technical_failure_reason(retry_result) is None,
                    "initial_reason": reason,
                }
                if technical_failure_reason(retry_result) is None:
                    await self._cache_verified_public_result(retry_result)
                return retry_result

            result["_telegram_session_recovery"] = {
                "attempted": True,
                "recovered": False,
                "initial_reason": reason,
                "manual_verification_timeout": True,
            }
            return result


finder_service = FinderService()


# ---------------------------------------------------------------------------
# V2.3 BATCH INPUT — TELEGRAM LAYER ONLY
# ---------------------------------------------------------------------------

MAX_BATCH_OEMS = 20


def parse_oem_batch(text: str) -> list[str]:
    """
    Split a Telegram message into independent OEM candidates.
    This is UI/input handling only; every candidate is still normalized and
    searched by the frozen V6.6 functions.

    Supported separators: new lines, commas, semicolons and whitespace.
    Duplicate normalized OEMs are searched once, preserving input order.
    """
    raw_tokens = [x.strip() for x in re.split(r"[\s,;]+", text or "") if x.strip()]
    if not raw_tokens:
        raise ValueError("No OEM numbers supplied.")

    oems = []
    seen = set()
    for raw in raw_tokens:
        oem = finder.normalize_oem(raw)
        if oem not in seen:
            seen.add(oem)
            oems.append(oem)

    if len(oems) > MAX_BATCH_OEMS:
        raise OverflowError(f"Too many OEM numbers: {len(oems)}")

    return oems


# ---------------------------------------------------------------------------
# V2.4 MIXED-MANUFACTURER INPUT — TELEGRAM LAYER ONLY
# ---------------------------------------------------------------------------

def _manufacturer_prefix(line: str) -> tuple[str | None, str]:
    """Return (canonical manufacturer, remainder) when a line starts with one."""
    text = (line or "").strip()
    if not text:
        return None, ""

    # Longest names first (important for names containing spaces / hyphens).
    for manufacturer in sorted(MANUFACTURERS, key=len, reverse=True):
        # Accept: "Ski-Doo 417...", "Ski-Doo: 417...", "Ski-Doo - 417..."
        pattern = rf"^{re.escape(manufacturer)}(?=$|[\s:—–-])\s*(?::|—|–|-)?\s*(.*)$"
        match = re.match(pattern, text, flags=re.IGNORECASE)
        if match:
            return manufacturer, (match.group(1) or "").strip()

    return None, text


def parse_mixed_manufacturer_batch(text: str) -> list[tuple[str, str]] | None:
    """
    Parse explicit manufacturer/OEM groups.

    Returns None when the message contains no explicit manufacturer at all;
    caller then uses the unchanged V2.3 selected-manufacturer parser.

    Supported examples:
        Ski-Doo 417300571
        Arctic Cat: 0746-933
        Honda: 08U70-HS0-AD0

        Ski-Doo:
        417300571
        417300574
        Arctic Cat:
        0746-933
        0627-033
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        return None

    detected = [_manufacturer_prefix(line) for line in lines]
    if not any(manufacturer for manufacturer, _ in detected):
        return None

    jobs: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    current_manufacturer: str | None = None

    for original_line, (line_manufacturer, remainder) in zip(lines, detected):
        if line_manufacturer:
            current_manufacturer = line_manufacturer
            payload = remainder
        else:
            if current_manufacturer is None:
                raise ValueError(
                    f"OEM without manufacturer before first manufacturer: {original_line}"
                )
            payload = original_line

        # Header-only line, e.g. "Ski-Doo:".
        if not payload:
            continue

        # Reuse the V2.3 OEM splitter/normalizer for the payload only.
        for oem in parse_oem_batch(payload):
            key = (current_manufacturer, oem)
            if key not in seen:
                seen.add(key)
                jobs.append(key)

    if not jobs:
        raise ValueError("No OEM numbers supplied for manufacturers.")
    if len(jobs) > MAX_BATCH_OEMS:
        raise OverflowError(f"Too many OEM numbers: {len(jobs)}")

    return jobs


def batch_summary(results: list[dict]) -> str:
    counts = {"FOUND": 0, "HUMAN_REVIEW": 0, "NOT_FOUND": 0, "ERROR": 0}
    for entry in results:
        if entry.get("exception"):
            counts["ERROR"] += 1
            continue
        status = str((entry.get("result") or {}).get("status") or "ERROR").upper()
        if status == "NOT_FOUND" and technical_failure_reason(entry.get("result") or {}) is not None:
            counts["ERROR"] += 1
        elif status in counts:
            counts[status] += 1
        else:
            counts["ERROR"] += 1

    parts = []
    if counts["FOUND"]:
        parts.append(f"✅ найдено: {counts['FOUND']}")
    if counts["HUMAN_REVIEW"]:
        parts.append(f"⚠️ ручная проверка: {counts['HUMAN_REVIEW']}")
    if counts["NOT_FOUND"]:
        parts.append(f"❌ не найдено: {counts['NOT_FOUND']} (проверь каталожный номер)")
    if counts["ERROR"]:
        parts.append(f"🔄 не удалось проверить: {counts['ERROR']}")
    return " • ".join(parts)

# ---------------------------------------------------------------------------
# V2.7 GROUP CHAT QUIET MODE
# ---------------------------------------------------------------------------

def _is_group_chat(update: Update) -> bool:
    chat = update.effective_chat
    return bool(chat and chat.type in ("group", "supergroup"))


def _bot_username(context: ContextTypes.DEFAULT_TYPE) -> str:
    username = getattr(context.bot, "username", None) or ""
    return str(username).lstrip("@").lower()


def _strip_bot_mention(
    text: str,
    context: ContextTypes.DEFAULT_TYPE,
) -> tuple[str, bool]:
    """Remove @botusername from text and report whether the bot was explicitly mentioned."""
    raw = text or ""
    username = _bot_username(context)

    if not username:
        return raw.strip(), False

    pattern = re.compile(
        rf"(?i)(?<!\w)@{re.escape(username)}(?!\w)"
    )
    mentioned = bool(pattern.search(raw))

    return pattern.sub("", raw).strip(), mentioned


def _is_reply_to_bot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    message = update.effective_message
    replied = getattr(message, "reply_to_message", None) if message else None
    replied_from = getattr(replied, "from_user", None) if replied else None

    return bool(replied_from and replied_from.id == context.bot.id)


GROUP_SESSION_TTL_SECONDS = 15 * 60


def _activate_group_session(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Activate quiet-mode input for this user in this exact group for a short time."""
    if not _is_group_chat(update) or not update.effective_chat:
        return
    context.user_data["group_session_chat_id"] = update.effective_chat.id
    context.user_data["group_session_until"] = time.monotonic() + GROUP_SESSION_TTL_SECONDS


def _group_session_is_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not _is_group_chat(update) or not update.effective_chat:
        return False
    if context.user_data.get("group_session_chat_id") != update.effective_chat.id:
        return False
    until = context.user_data.get("group_session_until")
    return isinstance(until, (int, float)) and time.monotonic() <= until


def _group_text_is_explicit_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> tuple[bool, str]:
    """
    Quiet-mode gate for group text.

    The bot accepts text when the user explicitly mentions/replies to the bot,
    or while that same user's short-lived bot session is active in this group.
    Ordinary messages from everyone else remain ignored.
    """
    message = update.effective_message
    raw = (message.text or "").strip() if message else ""
    cleaned, mentioned = _strip_bot_mention(raw, context)

    if mentioned or _is_reply_to_bot(update, context):
        _activate_group_session(update, context)
        return True, cleaned

    if _group_session_is_active(update, context):
        return True, raw

    return False, raw

# ---------------------------------------------------------------------------
# TELEGRAM HANDLERS
# ---------------------------------------------------------------------------

def format_manager_order(
    order_id: str,
    user,
    cart: dict,
    delivery_preference: str | None = None,
    origin: str | None = None,
) -> str:
    origin = (
        "web"
        if str(origin or "").strip().lower() == "web"
        or any(str(item.get("_origin") or "").lower() == "web" for item in cart.values())
        else "telegram"
    )
    origin_text = "🌐 WEB" if origin == "web" else "🤖 Telegram"
    lines = [
        "🆕 <b>НОВЫЙ ЗАПРОС</b>",
        "",
        f"<b>Запрос:</b> <code>{escape(order_id)}</code>",
        f"<b>Клиент:</b> {escape(user.full_name or '—')}",
        f"<b>Username:</b> @{escape(user.username)}" if user.username else "<b>Username:</b> —",
        f"<b>Telegram ID:</b> <code>{user.id}</code>",
        f"<b>Источник:</b> {origin_text}",
        "",
    ]

    total_usa = 0.0
    auto_customer_total_rub = 0
    auto_pricing_complete = bool(cart)
    has_usa = False
    has_warehouse = False
    for i, item in enumerate(cart.values(), 1):
        qty = int(item.get("qty", 1))
        price = item.get("price")
        source = str(item.get("offer_source") or "usa").strip().lower()

        if source == "warehouse":
            has_warehouse = True
            snapshot = item.get("price_snapshot_rub")
            customer_unit_rub = (
                int(round(float(snapshot)))
                if isinstance(snapshot, (int, float))
                else None
            )
        else:
            source = "usa"
            has_usa = True
            if isinstance(price, (int, float)):
                total_usa += float(price) * qty
            customer_unit_rub = customer_rub_price_from_dp(
                item.get("_dealer_price_usd"),
                PRICE_COEFFICIENT,
                USD_RUB_RATE,
            )
        if customer_unit_rub is None:
            auto_pricing_complete = False
        else:
            auto_customer_total_rub += customer_unit_rub * qty

        current_oem = str(item.get("oem") or "—")
        requested_oem = str(item.get("requested_oem") or current_oem)

        lines.extend([
            f"<b>{i}. {escape(str(item.get('manufacturer') or '—'))}</b>",
            f"OEM: <code>{escape(current_oem)}</code>",
        ])
        if requested_oem and requested_oem != current_oem:
            lines.append(
                f"Запрошен OEM: <code>{escape(requested_oem)}</code> → "
                f"актуальный: <code>{escape(current_oem)}</code>"
            )
        lines.extend([
            f"Название: {escape(str(item.get('name') or '—'))}",
            f"Количество: <b>{qty} шт.</b>",
            (
                (
                    f"Источник: 🇷🇺 {item.get('warehouse_public_name') or 'склад'} | "
                    f"Цена склада: {format_rub_whole(customer_unit_rub)} × {qty}"
                )
                if source == "warehouse" and customer_unit_rub is not None
                else (
                    "Источник: 🇺🇸 США | Цена в США: <b>$" + f"{price:.2f}" + f"</b> × {qty}"
                    if isinstance(price, (int, float))
                    else "Источник: 🇺🇸 США | Цена в США: —"
                )
            ),
        ])
        if source == "usa":
            selected_delivery = str(
                item.get("selected_delivery_tariff") or ""
            ).strip().lower()
            selected_label = CUSTOMER_TARIFF_LABELS.get(selected_delivery)
            if selected_label:
                lines.append(
                    f"🚚 Доставка: <b>{selected_label}</b>"
                )
        lines.append("")

    if has_usa:
        lines.extend([
            "<b>Итого по ценам США: $" + f"{total_usa:.2f}" + "</b>",
            "",
        ])
    if has_usa and delivery_preference in {"comfort", "economy", "mix"}:
        delivery_labels = {"comfort": "🟢 Комфорт", "economy": "🔵 Эконом", "mix": "🔴 MIX"}
        lines.extend([
            f"🚚 <b>Предпочтение по доставке:</b> {delivery_labels.get(delivery_preference, escape(delivery_preference))}",
            "",
        ])
    if auto_pricing_complete:
        lines.extend([
            f"🧮 <b>Стоимость товаров клиенту (авто): {format_rub_whole(auto_customer_total_rub)}</b>",
            (
                "Расчёт: цена склада"
                if not has_usa
                else (
                    f"Расчёт: США — DP × {PRICE_COEFFICIENT:g} × {USD_RUB_RATE:g} ₽/$; склад — цена склада"
                    if has_warehouse
                    else f"Расчёт: DP × {PRICE_COEFFICIENT:g} × {USD_RUB_RATE:g} ₽/$"
                )
            ),
            "",
            ("🚚 Доставка из США в стоимость товаров не входит и оплачивается отдельно после прихода груза в Москву." if has_usa else ""),
        ])
    else:
        lines.extend([
            "⚠️ <b>Авторасчёт товаров не завершён.</b>",
            "По одной или нескольким позициям нужен DP / ручная проверка.",
        ])
    return "\n".join(lines)


def admin_main_keyboard() -> InlineKeyboardMarkup:
    try:
        warehouse_problem_count = warehouse_alerts.problem_counts(
            ORDERS_DB_FILE
        )["total"]
    except Exception:
        log.exception("Could not calculate warehouse problem count")
        warehouse_problem_count = 0
    problem_label = (
        f"⚠️ Проблемы складов · {warehouse_problem_count}"
        if warehouse_problem_count
        else "✅ Проблемы складов · 0"
    )
    rows = [
        [InlineKeyboardButton("📦 Запросы", callback_data="admin:orders")],
        [InlineKeyboardButton("📦 Заказы поставщикам", callback_data="admin:suppliers")],
        [InlineKeyboardButton("🔎 Поиск", callback_data="admin:search")],
        [InlineKeyboardButton("💱 Курс / коэффициент", callback_data="admin:rates")],
        [InlineKeyboardButton("🚚 Доставка", callback_data="admin:delivery")],
        [InlineKeyboardButton("🏬 Склады", callback_data="admin:warehouses")],
        [InlineKeyboardButton(
            problem_label,
            callback_data="admin:warehouse:problems",
        )],
    ]
    rows.extend(ecosystem_navigation_rows())
    return InlineKeyboardMarkup(rows)


def admin_back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ Назад в админку", callback_data="admin:home")]
    ])


def admin_search_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧾 По OEM", callback_data="admin:searchtype:oem")],
        [InlineKeyboardButton("🔤 По названию детали", callback_data="admin:searchtype:part_name")],
        [InlineKeyboardButton("👤 По клиенту", callback_data="admin:searchtype:customer")],
        [InlineKeyboardButton("📋 По номеру запроса", callback_data="admin:searchtype:order")],
        [InlineKeyboardButton("🔎 Универсальный поиск", callback_data="admin:searchtype:universal")],
        [InlineKeyboardButton("⬅️ Назад в админку", callback_data="admin:home")],
    ])


def admin_search_prompt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⬅️ К поиску", callback_data="admin:search")],
        [InlineKeyboardButton("⚙️ В админку", callback_data="admin:home")],
    ])


ADMIN_ORDER_STATUSES = (
    ("new", "🆕 Новые"),
    ("working", "🟡 В работе"),
    ("waiting", "🔴 В ожидании"),
    ("confirmed", "🟢 Подтверждённые"),
    ("executing", "📦 Выполняются"),
    ("completed", "✅ Выполненные"),
    ("cancelled", "❌ Отменённые"),
)


def admin_orders_keyboard() -> InlineKeyboardMarkup:
    rows = []
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        counts = dict(conn.execute(
            "SELECT status, COUNT(*) FROM orders GROUP BY status"
        ).fetchall())
    # Old 'calculated' records belong to the waiting bucket.
    counts["waiting"] = counts.get("waiting", 0) + counts.get("calculated", 0)
    for status, label in ADMIN_ORDER_STATUSES:
        rows.append([InlineKeyboardButton(
            f"{label} · {counts.get(status, 0)}",
            callback_data=f"admin:ordersstatus:{status}",
        )])
    rows.append([InlineKeyboardButton("🔎 Найти запрос", callback_data="admin:search")])
    rows.append([InlineKeyboardButton("⬅️ Назад в админку", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


def admin_orders_status_keyboard(
    status: str,
    page: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    """Show requests inside one status with pagination."""
    page = max(int(page), 0)
    page_size = max(int(page_size), 1)
    offset = page * page_size
    rows = []

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        if status == "waiting":
            total = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status IN ('waiting','calculated')"
            ).fetchone()[0]
            orders = conn.execute(
                """SELECT order_id, customer_name
                   FROM orders
                   WHERE status IN ('waiting','calculated')
                   ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
                   LIMIT ? OFFSET ?""",
                (page_size, offset),
            ).fetchall()
        else:
            total = conn.execute(
                "SELECT COUNT(*) FROM orders WHERE status = ?",
                (status,),
            ).fetchone()[0]
            orders = conn.execute(
                """SELECT order_id, customer_name
                   FROM orders
                   WHERE status = ?
                   ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
                   LIMIT ? OFFSET ?""",
                (status, page_size, offset),
            ).fetchall()

    total_pages = max((total + page_size - 1) // page_size, 1)
    if page >= total_pages:
        page = total_pages - 1
        offset = page * page_size
        with sqlite3.connect(ORDERS_DB_FILE) as conn:
            if status == "waiting":
                orders = conn.execute(
                    """SELECT order_id, customer_name
                       FROM orders
                       WHERE status IN ('waiting','calculated')
                       ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
                       LIMIT ? OFFSET ?""",
                    (page_size, offset),
                ).fetchall()
            else:
                orders = conn.execute(
                    """SELECT order_id, customer_name
                       FROM orders
                       WHERE status = ?
                       ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
                       LIMIT ? OFFSET ?""",
                    (status, page_size, offset),
                ).fetchall()

    for order_id, customer_name in orders:
        rows.append([InlineKeyboardButton(
            f"📦 {order_id} · {customer_name or 'Без имени'}",
            callback_data=f"admin:order:{order_id}",
        )])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                "⬅️",
                callback_data=f"admin:ordersstatus:{status}:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="noop",
        ))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(
                "➡️",
                callback_data=f"admin:ordersstatus:{status}:{page + 1}",
            ))
        rows.append(nav)

    rows.append([InlineKeyboardButton("⬅️ К статусам", callback_data="admin:orders")])
    return InlineKeyboardMarkup(rows)


def get_delivery_tariffs():
    """Read current delivery tariffs from SQLite."""
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        rows = conn.execute(
            """
            SELECT
                code,
                name,
                base_rub_per_kg,
                volume_rub_per_kg,
                calculation_type,
                enabled
            FROM delivery_tariffs
            ORDER BY
                CASE code
                    WHEN 'comfort' THEN 1
                    WHEN 'economy' THEN 2
                    WHEN 'mix' THEN 3
                    ELSE 99
                END
            """
        ).fetchall()

    return rows



DELIVERY_TEXT_SETTING_DEFAULTS = {
    "comfort_short_eta": "от 5 недель",
    "comfort_transit_eta": "3–4 недели",
    "comfort_dispatch_day": "четверг",
    "economy_eta": "от 12 недель с момента формирования партии",
    "mix_eta": "от 5 недель с момента формирования партии",
    "supplier_processing_standard": "1–2 недели",
    "supplier_processing_brp": "2–4 недели",
}

DELIVERY_TEXT_SETTING_ALIASES = {
    "cshort": "comfort_short_eta",
    "ctransit": "comfort_transit_eta",
    "cday": "comfort_dispatch_day",
    "eeta": "economy_eta",
    "meta": "mix_eta",
    "pstd": "supplier_processing_standard",
    "pbrp": "supplier_processing_brp",
}

DELIVERY_TEXT_SETTING_LABELS = {
    "comfort_short_eta": "🟢 Комфорт — краткий срок",
    "comfort_transit_eta": "🟢 Комфорт — срок в пути",
    "comfort_dispatch_day": "🟢 Комфорт — день отправки",
    "economy_eta": "🔵 Эконом — срок",
    "mix_eta": "🔴 MIX — срок",
    "supplier_processing_standard": "⏳ Обработка США — стандарт",
    "supplier_processing_brp": "⏳ Обработка BRP",
}


def get_delivery_text_settings() -> dict:
    """Read editable client-facing delivery terms from the shared settings table."""
    values = dict(DELIVERY_TEXT_SETTING_DEFAULTS)
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        placeholders = ",".join("?" for _ in DELIVERY_TEXT_SETTING_DEFAULTS)
        rows = conn.execute(
            f"""
            SELECT setting_key, setting_value
            FROM delivery_settings
            WHERE setting_key IN ({placeholders})
            """,
            tuple(DELIVERY_TEXT_SETTING_DEFAULTS),
        ).fetchall()
    for key, value in rows:
        if key in values and str(value or "").strip():
            values[key] = str(value).strip()
    return values


def set_delivery_text_setting(setting_key: str, value: str) -> bool:
    """Update one approved delivery term without duplicating tariff data."""
    if setting_key not in DELIVERY_TEXT_SETTING_DEFAULTS:
        return False
    clean = " ".join(str(value or "").strip().split())
    if not clean or len(clean) > 120:
        return False
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.execute(
            """
            INSERT INTO delivery_settings(setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (setting_key, clean, now),
        )
        conn.commit()
    return True


def _delivery_tariff_map() -> dict:
    return {
        code: {
            "name": str(name),
            "base": float(base),
            "volume": float(volume),
            "type": str(calculation_type),
            "enabled": bool(enabled),
        }
        for code, name, base, volume, calculation_type, enabled
        in get_delivery_tariffs()
    }


def _delivery_number(value) -> str:
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def _delivery_rate(value) -> str:
    return format_rub_whole(value) + "/кг"


def format_customer_delivery_intro() -> str:
    """Short dynamic client text shown before choosing a USA delivery preference."""
    tariffs = _delivery_tariff_map()
    settings = get_delivery_text_settings()
    comfort = tariffs.get("comfort")
    economy = tariffs.get("economy")
    mix = tariffs.get("mix")
    if not all((comfort, economy, mix)):
        return (
            "🚚 <b>Как доставить позиции из США?</b>\n\n"
            "Актуальные тарифы временно уточняются."
        )

    return "\n".join([
        "🚚 <b>Как доставить позиции из США?</b>",
        "",
        f"🟢 <b>КОМФОРТ</b> — {_delivery_rate(comfort['base'])} · "
        f"{escape(settings['comfort_short_eta'])}",
        f"🔵 <b>ЭКОНОМ</b> — {_delivery_rate(economy['base'])} · "
        f"{escape(settings['economy_eta'])}",
        f"🔴 <b>MIX</b> — {_delivery_rate(mix['base'])} · "
        f"{escape(settings['mix_eta'])}",
        "",
        "⏳ До отправки поставщикам требуется время на обработку заказов: "
        f"стандартно <b>{escape(settings['supplier_processing_standard'])}</b>, "
        f"для <b>BRP — {escape(settings['supplier_processing_brp'])}</b>.",
        "",
        "⚠️ Сроки ориентировочные.",
        "",
        "Стоимость доставки рассчитывается отдельно после прихода груза в Москву.",
    ])


def format_customer_delivery_details() -> str:
    """Detailed dynamic delivery explanation with examples recalculated from live tariffs."""
    tariffs = _delivery_tariff_map()
    settings = get_delivery_text_settings()
    comfort = tariffs.get("comfort")
    economy = tariffs.get("economy")
    mix = tariffs.get("mix")
    if not all((comfort, economy, mix)):
        return "🚚 Актуальные тарифы доставки временно уточняются."

    actual_kg = 2.0
    box_side_cm = 30.0
    divisor = float(get_volume_weight_divisor())
    volume_kg = float(rounded_group_volume_weight(
        box_side_cm, box_side_cm, box_side_cm, divisor
    ))
    excess_kg = max(volume_kg - actual_kg, 0.0)

    comfort_actual = actual_kg * comfort["base"]
    comfort_volume = excess_kg * comfort["volume"]
    comfort_total = comfort_actual + comfort_volume
    economy_total = actual_kg * economy["base"]
    mix_actual = actual_kg * mix["base"]
    mix_volume = volume_kg * mix["volume"]
    mix_total = mix_actual + mix_volume

    lines = [
        "🟢 <b>КОМФОРТ</b>",
        "",
        f"Отправка из США — <b>каждый {escape(settings['comfort_dispatch_day'])}</b>.",
        "",
        "Ориентировочный срок в пути до Москвы — "
        f"<b>{escape(settings['comfort_transit_eta'])}</b>.",
        "",
        "Стоимость считается по фактическому весу — "
        f"<b>{format_rub_whole(comfort['base'])} за каждый кг</b>.",
        "",
        "Если объёмный вес больше фактического, дополнительно оплачивается "
        "только разница между объёмным и фактическим весом — "
        f"<b>{format_rub_whole(comfort['volume'])} за каждый дополнительный кг</b>.",
        "",
        "<b>Пример:</b>",
        f"коробка весит {_delivery_number(actual_kg)} кг, а её объёмный вес — "
        f"{_delivery_number(volume_kg)} кг.",
        "",
        "За фактический вес:",
        "",
        f"<code>{_delivery_number(actual_kg)} кг × "
        f"{format_rub_whole(comfort['base'])} = "
        f"{format_rub_whole(comfort_actual)}</code>",
        "",
        "Разница между объёмным и фактическим весом:",
        "",
        f"<code>{_delivery_number(volume_kg)} − {_delivery_number(actual_kg)} = "
        f"{_delivery_number(excess_kg)} кг</code>",
        "",
        "Доплата за объём:",
        "",
        f"<code>{_delivery_number(excess_kg)} кг × "
        f"{format_rub_whole(comfort['volume'])} = "
        f"{format_rub_whole(comfort_volume)}</code>",
        "",
        f"<b>Доставка: {format_rub_whole(comfort_total)}</b>",
        "",
        "—",
        "",
        "🔵 <b>ЭКОНОМ</b>",
        "",
        "Ориентировочный срок — "
        f"<b>{escape(settings['economy_eta'])}</b>.",
        "",
        "Стоимость считается только по фактическому весу — "
        f"<b>{format_rub_whole(economy['base'])} за каждый кг</b>.",
        "",
        "Объёмный вес в <b>ЭКОНОМ</b> не учитывается.",
        "",
        "<b>Пример:</b>",
        f"груз весит {_delivery_number(actual_kg)} кг.",
        "",
        f"<code>{_delivery_number(actual_kg)} кг × "
        f"{format_rub_whole(economy['base'])} = "
        f"{format_rub_whole(economy_total)}</code>",
        "",
        f"<b>Доставка: {format_rub_whole(economy_total)}</b>",
        "",
        "—",
        "",
        "🔴 <b>MIX</b>",
        "",
        "Используется для <b>экипировки и аксессуаров</b>, "
        "которые отправляются отдельной сборкой.",
        "",
        "Ориентировочный срок — "
        f"<b>{escape(settings['mix_eta'])}</b>.",
        "",
        "Здесь стоимость складывается из двух частей:",
        "",
        f"<b>1. За фактический вес — {format_rub_whole(mix['base'])} за каждый кг.</b>",
        "",
        "<b>+</b>",
        "",
        f"<b>2. За объёмный вес — {format_rub_whole(mix['volume'])} за каждый кг.</b>",
        "",
        "<b>Пример:</b>",
        f"коробка весит {_delivery_number(actual_kg)} кг, объёмный вес — "
        f"{_delivery_number(volume_kg)} кг.",
        "",
        "За фактический вес:",
        "",
        f"<code>{_delivery_number(actual_kg)} кг × "
        f"{format_rub_whole(mix['base'])} = "
        f"{format_rub_whole(mix_actual)}</code>",
        "",
        "За объёмный вес:",
        "",
        f"<code>{_delivery_number(volume_kg)} кг × "
        f"{format_rub_whole(mix['volume'])} = "
        f"{format_rub_whole(mix_volume)}</code>",
        "",
        f"<b>Доставка: {format_rub_whole(mix_total)}</b>",
        "",
        "—",
        "",
        "📐 <b>Что такое объёмный вес</b>",
        "",
        "Это вес, который рассчитывается по размерам коробки в см:",
        "",
        f"<code>Длина × Ширина × Высота / {_delivery_number(divisor)}</code>",
        "",
        "Например:",
        "",
        f"<code>30 × 30 × 30 / {_delivery_number(divisor)} = "
        f"{_delivery_number(volume_kg)} кг</code>",
        "",
        "",
        "⏳ <b>Срок обработки заказов поставщиками</b>",
        "",
        f"Стандартно по штатам <b>{escape(settings['supplier_processing_standard'])}</b>, "
        f"для <b>BRP — {escape(settings['supplier_processing_brp'])}</b>.",
        "",
        "⚠️ Все сроки ориентировочные.",
        "",
        "Стоимость доставки не входит в стоимость товара и оплачивается "
        "отдельно после прихода груза в Москву.",
    ]
    return "\n".join(lines)


def format_admin_delivery_terms() -> str:
    settings = get_delivery_text_settings()
    return "\n".join([
        "⏱ <b>Сроки и условия доставки</b>",
        "",
        "🟢 <b>Комфорт</b>",
        f"Краткий срок: <b>{escape(settings['comfort_short_eta'])}</b>",
        f"Срок в пути: <b>{escape(settings['comfort_transit_eta'])}</b>",
        f"День отправки: <b>{escape(settings['comfort_dispatch_day'])}</b>",
        "",
        "🔵 <b>Эконом</b>",
        f"Срок: <b>{escape(settings['economy_eta'])}</b>",
        "",
        "🔴 <b>MIX</b>",
        f"Срок: <b>{escape(settings['mix_eta'])}</b>",
        "",
        "⏳ <b>Обработка заказов поставщиками</b>",
        f"Стандартно: <b>{escape(settings['supplier_processing_standard'])}</b>",
        f"BRP: <b>{escape(settings['supplier_processing_brp'])}</b>",
    ])


def admin_delivery_terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            "🟢 Комфорт — краткий срок",
            callback_data="admin:deliverysetting:cshort",
        )],
        [InlineKeyboardButton(
            "🟢 Комфорт — срок в пути",
            callback_data="admin:deliverysetting:ctransit",
        )],
        [InlineKeyboardButton(
            "🟢 Комфорт — день отправки",
            callback_data="admin:deliverysetting:cday",
        )],
        [InlineKeyboardButton(
            "🔵 Эконом — срок",
            callback_data="admin:deliverysetting:eeta",
        )],
        [InlineKeyboardButton(
            "🔴 MIX — срок",
            callback_data="admin:deliverysetting:meta",
        )],
        [InlineKeyboardButton(
            "⏳ Обработка США — стандарт",
            callback_data="admin:deliverysetting:pstd",
        )],
        [InlineKeyboardButton(
            "⏳ Обработка BRP",
            callback_data="admin:deliverysetting:pbrp",
        )],
        [InlineKeyboardButton(
            "⬅️ К доставке",
            callback_data="admin:delivery",
        )],
    ])


def parse_positive_delivery_number(value) -> Decimal:
    """Parse a positive finite measurement or divisor."""
    try:
        number = Decimal(str(value).strip().replace(",", "."))
    except (InvalidOperation, ValueError):
        raise ValueError("Введи положительное число.") from None
    if not number.is_finite() or number <= 0:
        raise ValueError("Значение должно быть конечным числом больше нуля.")
    try:
        converted = float(number)
    except (OverflowError, ValueError):
        raise ValueError("Число слишком велико.") from None
    if not math.isfinite(converted) or converted <= 0:
        raise ValueError("Число вне допустимого диапазона.")
    return number


def get_volume_weight_divisor(conn=None) -> Decimal:
    """Read the current global divisor; never silently replace bad data."""
    if conn is None:
        uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True)) as read_conn:
            return get_volume_weight_divisor(read_conn)
    row = conn.execute(
        "SELECT setting_value FROM delivery_settings "
        "WHERE setting_key = 'volume_weight_divisor'"
    ).fetchone()
    if row is None:
        raise ValueError("Делитель объёмного веса не настроен.")
    return parse_positive_delivery_number(row[0])


def set_volume_weight_divisor(value) -> None:
    divisor = parse_positive_delivery_number(value)
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.execute(
            """
            UPDATE delivery_settings
            SET setting_value = ?, updated_at = ?
            WHERE setting_key = 'volume_weight_divisor'
            """,
            (str(divisor), now),
        )
        conn.commit()


def rounded_group_volume_weight(length, width, height, divisor) -> float:
    length = parse_positive_delivery_number(length)
    width = parse_positive_delivery_number(width)
    height = parse_positive_delivery_number(height)
    divisor = parse_positive_delivery_number(divisor)
    raw = length * width * height / divisor
    try:
        rounded = raw.quantize(Decimal("0.1"), rounding=ROUND_CEILING)
        result = float(rounded)
    except (InvalidOperation, OverflowError, ValueError):
        raise ValueError("Не удалось рассчитать объёмный вес.") from None
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Объёмный вес вне допустимого диапазона.")
    return result


def format_admin_delivery_settings() -> str:
    """Format current delivery tariffs for manager admin panel."""
    tariffs = get_delivery_tariffs()

    tariff_map = {
        code: {
            "name": name,
            "base": float(base),
            "volume": float(volume),
            "type": calculation_type,
            "enabled": bool(enabled),
        }
        for code, name, base, volume, calculation_type, enabled in tariffs
    }

    lines = [
        "🚚 <b>Доставка</b>",
        "",
        "<b>Текущие тарифы:</b>",
        "",
    ]

    comfort = tariff_map.get("comfort")
    if comfort:
        lines.extend([
            "🟢 <b>Комфорт</b>",
            f"Фактический вес: <b>{comfort['base']:g} ₽/кг</b>",
            f"Объёмный вес: <b>{comfort['volume']:g} ₽/кг</b>",
            "",
            "Расчёт:",
            "фактический вес × тариф +",
            "превышение объёмного веса над фактическим × тариф объёма",
            "",
        ])

    economy = tariff_map.get("economy")
    if economy:
        lines.extend([
            "🔵 <b>Эконом</b>",
            f"Фактический вес: <b>{economy['base']:g} ₽/кг</b>",
            "Объёмный вес: <b>не учитывается</b>",
            "",
        ])

    mix = tariff_map.get("mix")
    if mix:
        lines.extend([
            "🔴 <b>MIX</b>",
            f"Фактический вес: <b>{mix['base']:g} ₽/кг</b>",
            f"Объёмный вес: <b>{mix['volume']:g} ₽/кг</b>",
            "",
            "Расчёт:",
            "фактический вес × тариф +",
            "весь объёмный вес × тариф объёма",
        ])

    divisor = get_volume_weight_divisor()
    settings = get_delivery_text_settings()
    lines.extend([
        "",
        f"📐 <b>Делитель объёмного веса:</b> {divisor:g}",
        "",
        "⏱ <b>Сроки и условия:</b>",
        f"🟢 Комфорт: <b>{escape(settings['comfort_short_eta'])}</b>; "
        f"в пути <b>{escape(settings['comfort_transit_eta'])}</b>; "
        f"отправка — <b>{escape(settings['comfort_dispatch_day'])}</b>",
        f"🔵 Эконом: <b>{escape(settings['economy_eta'])}</b>",
        f"🔴 MIX: <b>{escape(settings['mix_eta'])}</b>",
        f"⏳ Обработка: <b>{escape(settings['supplier_processing_standard'])}</b>; "
        f"BRP — <b>{escape(settings['supplier_processing_brp'])}</b>",
    ])
    return "\n".join(lines)


def admin_delivery_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🟢 Комфорт",
                callback_data="admin:deliverytariff:comfort",
            )
        ],
        [
            InlineKeyboardButton(
                "🔵 Эконом",
                callback_data="admin:deliverytariff:economy",
            )
        ],
        [
            InlineKeyboardButton(
                "🔴 MIX",
                callback_data="admin:deliverytariff:mix",
            )
        ],
        [
            InlineKeyboardButton(
                "⏱ Сроки и условия",
                callback_data="admin:deliveryterms",
            )
        ],
        [
            InlineKeyboardButton(
                "📐 Изменить делитель объёмного веса",
                callback_data="admin:deliverydivisor",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад в админку",
                callback_data="admin:home",
            )
        ],
    ])


def set_delivery_tariff_value(
    tariff_code: str,
    field: str,
    value: float,
) -> bool:
    """Update one editable delivery tariff value in SQLite."""
    allowed = {
        "comfort": {"base_rub_per_kg", "volume_rub_per_kg"},
        "economy": {"base_rub_per_kg"},
        "mix": {"base_rub_per_kg", "volume_rub_per_kg"},
    }

    if tariff_code not in allowed:
        return False

    if field not in allowed[tariff_code]:
        return False

    if value < 0:
        return False

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            f"""
            UPDATE delivery_tariffs
            SET {field} = ?, updated_at = ?
            WHERE code = ?
            """,
            (float(value), updated_at, tariff_code),
        )
        conn.commit()

    return cursor.rowcount == 1


def get_delivery_tariff(tariff_code: str):
    """Read one delivery tariff."""
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        return conn.execute(
            """
            SELECT
                code,
                name,
                base_rub_per_kg,
                volume_rub_per_kg,
                calculation_type,
                enabled
            FROM delivery_tariffs
            WHERE code = ?
            """,
            (tariff_code,),
        ).fetchone()


def format_admin_delivery_tariff(tariff_code: str):
    """Format one delivery tariff for editing."""
    row = get_delivery_tariff(tariff_code)

    if not row:
        return None

    code, name, base, volume, calculation_type, enabled = row

    lines = [
        f"🚚 <b>{escape(str(name))}</b>",
        "",
    ]

    if code == "comfort":
        lines.extend([
            f"⚖️ Фактический вес: <b>{float(base):g} ₽/кг</b>",
            f"📦 Объёмный вес: <b>{float(volume):g} ₽/кг</b>",
            "",
            "<b>Формула:</b>",
            "фактический вес × тариф +",
            "превышение объёмного веса над фактическим × тариф объёма",
        ])

    elif code == "economy":
        lines.extend([
            f"⚖️ Фактический вес: <b>{float(base):g} ₽/кг</b>",
            "📦 Объёмный вес: <b>не учитывается</b>",
            "",
            "<b>Формула:</b>",
            "фактический вес × тариф",
        ])

    elif code == "mix":
        lines.extend([
            f"⚖️ Фактический вес: <b>{float(base):g} ₽/кг</b>",
            f"📦 Объёмный вес: <b>{float(volume):g} ₽/кг</b>",
            "",
            "<b>Формула:</b>",
            "фактический вес × тариф +",
            "весь объёмный вес × тариф объёма",
        ])

    return "\n".join(lines)


def admin_delivery_tariff_keyboard(tariff_code: str) -> InlineKeyboardMarkup:
    rows = []

    rows.append([
        InlineKeyboardButton(
            "⚖️ Изменить цену за кг",
            callback_data=f"admin:deliveryedit:{tariff_code}:base",
        )
    ])

    if tariff_code == "comfort":
        rows.append([
            InlineKeyboardButton(
                "📦 Изменить цену объёмного веса",
                callback_data="admin:deliveryedit:comfort:volume",
            )
        ])

    elif tariff_code == "mix":
        rows.append([
            InlineKeyboardButton(
                "📦 Изменить цену объёма",
                callback_data="admin:deliveryedit:mix:volume",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ К тарифам",
            callback_data="admin:delivery",
        )
    ])

    rows.append([
        InlineKeyboardButton(
            "⚙️ В админку",
            callback_data="admin:home",
        )
    ])

    return InlineKeyboardMarkup(rows)


def _admin_search_status_icon(status: str) -> str:
    return {
        "new": "🆕",
        "working": "🟡",
        "waiting": "🔴",
        "calculated": "🔴",
        "confirmed": "🟢",
        "executing": "📦",
        "completed": "✅",
        "cancelled": "❌",
    }.get(str(status or ""), "•")


def _admin_search_status_label(status: str) -> str:
    return {
        "new": "Новый",
        "working": "В работе",
        "waiting": "В ожидании",
        "calculated": "В ожидании",
        "confirmed": "Подтверждён",
        "executing": "Выполняется",
        "completed": "Выполнен",
        "cancelled": "Отменён",
    }.get(str(status or ""), str(status or "—"))


def _admin_search_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "—"
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).strftime("%d.%m.%Y")
    except (TypeError, ValueError):
        return raw[:10]


def _admin_search_total_text(goods_rub, delivery_rub, delivery_pending) -> str:
    if goods_rub is None:
        return "—"
    try:
        goods = float(goods_rub)
        if not delivery_pending and delivery_rub is not None:
            return format_rub_whole(goods + float(delivery_rub))
        return format_rub_whole(goods) + " + дост."
    except (TypeError, ValueError):
        return "—"


def _cb_b64_encode(value: str) -> str:
    raw = str(value or "").encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _cb_b64_decode(value: str) -> str:
    value = str(value or "")
    padding = "=" * ((4 - len(value) % 4) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii")).decode("utf-8")


def _admin_part_result_callback(oem: str, search_term: str, page: int) -> str:
    encoded_oem = _cb_b64_encode(str(oem or ""))
    encoded_term = _cb_b64_encode(str(search_term or ""))
    callback = f"admin:searchpart:{page}:{encoded_oem}:{encoded_term}"
    if len(callback.encode("utf-8")) <= 64:
        return callback
    return f"admin:searchpart:{encoded_oem}"


def _admin_search_orders(conn, where_sql: str, params, limit: int):
    return conn.execute(
        f"""
        SELECT DISTINCT
            o.order_id,
            COALESCE(o.updated_at, o.created_at) AS activity_at,
            o.customer_name,
            o.username,
            o.telegram_user_id,
            o.status
        FROM orders o
        LEFT JOIN order_items i ON i.order_id = o.order_id
        WHERE {where_sql}
        ORDER BY activity_at DESC, o.created_at DESC
        LIMIT ?
        """,
        tuple(params) + (limit,),
    ).fetchall()


def search_admin_customers_by_name(search_text: str, limit: int = 200):
    term = (search_text or "").strip()
    if not term:
        return {"kind": "customer_name", "term": "", "items": [], "total": 0}

    like_name = f"%{term}%"
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        customer_total = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT telegram_user_id
                FROM orders
                WHERE customer_name LIKE ? COLLATE NOCASE
                GROUP BY telegram_user_id
            )
            """,
            (like_name,),
        ).fetchone()[0]

        customers = conn.execute(
            """
            SELECT
                telegram_user_id,
                MAX(customer_name) AS customer_name,
                MAX(COALESCE(username, '')) AS username,
                COUNT(*) AS order_count,
                MAX(COALESCE(updated_at, created_at)) AS activity_at
            FROM orders
            WHERE customer_name LIKE ? COLLATE NOCASE
            GROUP BY telegram_user_id
            ORDER BY activity_at DESC
            LIMIT ?
            """,
            (like_name, limit),
        ).fetchall()

    return {
        "kind": "customer_name",
        "term": term,
        "items": customers,
        "total": min(customer_total, limit),
        "truncated": customer_total > limit,
    }


def search_admin_clients(search_text: str, limit: int = 200):
    """One client entry point: name, @username or Telegram ID."""
    term = (search_text or "").strip()
    if not term:
        return {"kind": "customer_name", "term": "", "items": [], "total": 0}

    if term.isdigit() and len(term) >= 6:
        return search_admin_by_telegram_id(term, limit=limit)

    if term.startswith("@"):
        return search_admin_by_username(term, limit=limit)

    by_name = search_admin_customers_by_name(term, limit=limit)
    if by_name.get("items"):
        return by_name

    # Username without @ is accepted as a fallback.
    return search_admin_by_username(term, limit=limit)


def search_admin_parts_by_name(search_text: str, limit: int = 200):
    """Search saved order items by part name; all entered words must match."""
    term = (search_text or "").strip()
    if not term:
        return {"kind": "part_name", "term": "", "items": [], "total": 0}

    tokens = [x for x in re.findall(r"[\w-]+", term, flags=re.UNICODE) if x]
    if not tokens:
        tokens = [term]

    where = " AND ".join("i.name LIKE ? COLLATE NOCASE" for _ in tokens)
    params = tuple(f"%{token}%" for token in tokens)

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        total = conn.execute(
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT i.oem
                FROM order_items i
                WHERE {where}
                GROUP BY i.oem COLLATE NOCASE
            )
            """,
            params,
        ).fetchone()[0]

        rows = conn.execute(
            f"""
            SELECT
                i.oem,
                MAX(i.manufacturer) AS manufacturer,
                MAX(i.name) AS item_name,
                COUNT(DISTINCT o.order_id) AS requests_count,
                COUNT(DISTINCT o.telegram_user_id) AS clients_count,
                COALESCE(SUM(i.quantity), 0) AS total_qty,
                MAX(COALESCE(o.updated_at, o.created_at)) AS activity_at
            FROM order_items i
            JOIN orders o ON o.order_id = i.order_id
            WHERE {where}
            GROUP BY i.oem COLLATE NOCASE
            ORDER BY activity_at DESC
            LIMIT ?
            """,
            params + (limit,),
        ).fetchall()

    return {
        "kind": "part_name",
        "term": term,
        "items": rows,
        "total": min(int(total or 0), limit),
        "truncated": int(total or 0) > limit,
    }


def search_admin_by_username(search_text: str, limit: int = 200):
    term = (search_text or "").strip()
    username = term[1:].strip() if term.startswith("@") else term
    if not username:
        return {"kind": "username", "term": term, "items": [], "total": 0}

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        customers = conn.execute(
            """
            SELECT
                telegram_user_id,
                MAX(customer_name) AS customer_name,
                MAX(COALESCE(username, '')) AS username,
                COUNT(*) AS order_count,
                MAX(COALESCE(updated_at, created_at)) AS activity_at
            FROM orders
            WHERE username = ? COLLATE NOCASE
            GROUP BY telegram_user_id
            ORDER BY activity_at DESC
            LIMIT ?
            """,
            (username, limit),
        ).fetchall()

    if len(customers) == 1:
        return get_admin_customer_orders(customers[0][0], limit=limit)

    return {
        "kind": "customer_identity",
        "term": term,
        "items": customers,
        "total": len(customers),
        "identity_label": "username",
    }


def search_admin_by_telegram_id(search_text: str, limit: int = 200):
    term = (search_text or "").strip()
    if not term.isdigit():
        return {"kind": "telegram_id", "term": term, "items": [], "total": 0}

    telegram_user_id = int(term)
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        exists = conn.execute(
            "SELECT 1 FROM orders WHERE telegram_user_id = ? LIMIT 1",
            (telegram_user_id,),
        ).fetchone()

    if not exists:
        return {"kind": "telegram_id", "term": term, "items": [], "total": 0}
    return get_admin_customer_orders(telegram_user_id, limit=limit)


def search_admin_by_order_number(search_text: str, limit: int = 200):
    term = (search_text or "").strip()
    if not term:
        return {"kind": "order", "term": "", "items": [], "total": 0}

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        exact = _admin_search_orders(
            conn,
            "o.order_id = ? COLLATE NOCASE",
            (term,),
            limit,
        )
        if exact:
            return {
                "kind": "order",
                "term": term,
                "items": exact,
                "total": len(exact),
            }

        like_term = f"%{term}%"
        rows = _admin_search_orders(
            conn,
            "o.order_id LIKE ? COLLATE NOCASE",
            (like_term,),
            limit,
        )

    return {
        "kind": "order_partial",
        "term": term,
        "items": rows,
        "total": len(rows),
    }


def get_admin_oem_history(search_text: str, limit: int = 200):
    term = (search_text or "").strip()
    if not term:
        return {"kind": "oem_history", "term": "", "items": [], "total": 0}

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        rows = conn.execute(
            """
            SELECT
                o.order_id,
                o.created_at,
                o.status,
                o.customer_name,
                o.username,
                o.telegram_user_id,
                MAX(i.manufacturer) AS manufacturer,
                MAX(i.name) AS item_name,
                SUM(i.quantity) AS quantity,
                MAX(i.price_usd) AS price_usd,
                COALESCE(o.updated_at, o.created_at) AS activity_at
            FROM order_items i
            JOIN orders o ON o.order_id = i.order_id
            WHERE i.oem = ? COLLATE NOCASE
            GROUP BY
                o.order_id,
                o.created_at,
                o.status,
                o.customer_name,
                o.username,
                o.telegram_user_id,
                activity_at
            ORDER BY o.created_at DESC, activity_at DESC
            LIMIT ?
            """,
            (term, limit),
        ).fetchall()

        stats = conn.execute(
            """
            SELECT
                COUNT(DISTINCT o.order_id),
                COUNT(DISTINCT o.telegram_user_id),
                COALESCE(SUM(i.quantity), 0)
            FROM order_items i
            JOIN orders o ON o.order_id = i.order_id
            WHERE i.oem = ? COLLATE NOCASE
            """,
            (term,),
        ).fetchone()

        labels = conn.execute(
            """
            SELECT
                GROUP_CONCAT(DISTINCT i.manufacturer),
                GROUP_CONCAT(DISTINCT i.name)
            FROM order_items i
            WHERE i.oem = ? COLLATE NOCASE
            """,
            (term,),
        ).fetchone()

    requests_count = int(stats[0] or 0) if stats else 0
    clients_count = int(stats[1] or 0) if stats else 0
    total_qty = int(stats[2] or 0) if stats else 0

    return {
        "kind": "oem_history",
        "term": term,
        "items": rows,
        "total": min(requests_count, limit),
        "truncated": requests_count > limit,
        "requests_count": requests_count,
        "clients_count": clients_count,
        "total_qty": total_qty,
        "manufacturers": labels[0] if labels else None,
        "item_names": labels[1] if labels else None,
    }


def search_admin_by_mode(mode: str, search_text: str, limit: int = 200):
    mode = str(mode or "universal")
    if mode == "order":
        return search_admin_by_order_number(search_text, limit=limit)
    if mode == "oem":
        return get_admin_oem_history(search_text, limit=limit)
    if mode == "part_name":
        return search_admin_parts_by_name(search_text, limit=limit)
    if mode == "customer":
        return search_admin_clients(search_text, limit=limit)
    return search_admin_orders(search_text, limit=limit)


def search_admin_orders(search_text: str, limit: int = 200):
    """Smart admin search with exact identifiers before human-name search."""
    term = (search_text or "").strip()
    if not term:
        return {"kind": "empty", "term": "", "items": [], "total": 0}

    username_term = term[1:].strip() if term.startswith("@") else term

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        # 1. Exact order number always wins.
        exact_order = _admin_search_orders(
            conn,
            "o.order_id = ? COLLATE NOCASE",
            (term,),
            limit,
        )
        if exact_order:
            return {
                "kind": "order",
                "term": term,
                "items": exact_order,
                "total": len(exact_order),
            }

        # 2. @username is an explicit client identifier.
        if term.startswith("@") and username_term:
            user_ids = conn.execute(
                """
                SELECT DISTINCT telegram_user_id
                FROM orders
                WHERE username = ? COLLATE NOCASE
                LIMIT 2
                """,
                (username_term,),
            ).fetchall()
            if len(user_ids) == 1:
                return get_admin_customer_orders(user_ids[0][0], limit=limit)

            rows = _admin_search_orders(
                conn,
                "o.username = ? COLLATE NOCASE",
                (username_term,),
                limit,
            )
            return {
                "kind": "username",
                "term": term,
                "items": rows,
                "total": len(rows),
            }

        # 3. A long all-digit value is treated as Telegram ID only if it exists.
        if term.isdigit() and len(term) >= 6:
            exists = conn.execute(
                "SELECT 1 FROM orders WHERE telegram_user_id = ? LIMIT 1",
                (int(term),),
            ).fetchone()
            if exists:
                return get_admin_customer_orders(int(term), limit=limit)

        # 4. Exact OEM search opens the OEM history view.
        oem_exists = conn.execute(
            "SELECT 1 FROM order_items WHERE oem = ? COLLATE NOCASE LIMIT 1",
            (term,),
        ).fetchone()
        if oem_exists:
            return get_admin_oem_history(term, limit=limit)

        # 5. Username without @ still works when it is an exact username.
        username_ids = conn.execute(
            """
            SELECT DISTINCT telegram_user_id
            FROM orders
            WHERE username = ? COLLATE NOCASE
            LIMIT 2
            """,
            (username_term,),
        ).fetchall()
        if len(username_ids) == 1:
            return get_admin_customer_orders(username_ids[0][0], limit=limit)

        username_rows = _admin_search_orders(
            conn,
            "o.username = ? COLLATE NOCASE",
            (username_term,),
            limit,
        )
        if username_rows:
            return {
                "kind": "username",
                "term": term,
                "items": username_rows,
                "total": len(username_rows),
            }

        # 6. Human names are grouped by Telegram ID, not by order.
        # This prevents a common name such as "Дмитрий" from becoming a
        # misleading single-order result.
        like_name = f"%{term}%"
        customer_total = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
                SELECT o.telegram_user_id
                FROM orders o
                WHERE o.customer_name LIKE ? COLLATE NOCASE
                GROUP BY o.telegram_user_id
            )
            """,
            (like_name,),
        ).fetchone()[0]

        customers = conn.execute(
            """
            SELECT
                o.telegram_user_id,
                MAX(o.customer_name) AS customer_name,
                MAX(COALESCE(o.username, '')) AS username,
                COUNT(*) AS order_count,
                MAX(COALESCE(o.updated_at, o.created_at)) AS activity_at
            FROM orders o
            WHERE o.customer_name LIKE ? COLLATE NOCASE
            GROUP BY o.telegram_user_id
            ORDER BY activity_at DESC
            LIMIT ?
            """,
            (like_name, limit),
        ).fetchall()

        if customers:
            return {
                "kind": "customer_name",
                "term": term,
                "items": customers,
                "total": min(customer_total, limit),
                "truncated": customer_total > limit,
            }

        # 7. Saved part-name search. Token matching lets "Belt Drive"
        # find a stored name such as "Belt, Drive".
        part_matches = search_admin_parts_by_name(term, limit=limit)
        if part_matches.get("items"):
            return part_matches

        # 8. Last-resort partial technical search.
        like_term = f"%{term}%"
        rows = _admin_search_orders(
            conn,
            """
            o.order_id LIKE ? COLLATE NOCASE
            OR i.oem LIKE ? COLLATE NOCASE
            OR o.username LIKE ? COLLATE NOCASE
            """,
            (like_term, like_term, like_term),
            limit,
        )
        return {
            "kind": "partial",
            "term": term,
            "items": rows,
            "total": len(rows),
        }


def get_admin_customer_orders(telegram_user_id: int, limit: int = 200):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        rows = conn.execute(
            """
            SELECT
                order_id,
                created_at,
                status,
                customer_total_rub,
                delivery_rub,
                delivery_pending,
                COALESCE(updated_at, created_at) AS activity_at
            FROM orders
            WHERE telegram_user_id = ?
            ORDER BY created_at DESC, activity_at DESC
            LIMIT ?
            """,
            (telegram_user_id, limit),
        ).fetchall()

        identity = conn.execute(
            """
            SELECT customer_name, username
            FROM orders
            WHERE telegram_user_id = ?
            ORDER BY COALESCE(updated_at, created_at) DESC, created_at DESC
            LIMIT 1
            """,
            (telegram_user_id,),
        ).fetchone()

    return {
        "kind": "customer_orders",
        "term": str(telegram_user_id),
        "items": rows,
        "total": len(rows),
        "customer_name": identity[0] if identity else None,
        "username": identity[1] if identity else None,
    }


def format_admin_search_results(payload: dict, page: int = 0, page_size: int = 10) -> str:
    kind = payload.get("kind")
    term = payload.get("term", "")
    total = int(payload.get("total", 0))
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(max(int(page), 0), total_pages - 1)

    kind_labels = {
        "order": "номер запроса",
        "order_partial": "номер запроса",
        "oem": "OEM",
        "oem_history": "OEM",
        "part_name": "название детали",
        "username": "username",
        "telegram_id": "Telegram ID",
        "customer_name": "клиент",
        "customer_identity": "клиент",
        "customer_orders": "клиент",
        "partial": "частичное совпадение",
    }
    label = kind_labels.get(kind, "запрос")

    found_text = f"{total}+" if payload.get("truncated") else str(total)

    if kind == "customer_orders":
        lines = [
            "👤 <b>История запросов клиента</b>",
            "",
            f"Запросов: <b>{found_text}</b>",
        ]
    elif kind == "oem_history":
        manufacturers = str(payload.get("manufacturers") or "—")
        item_names = str(payload.get("item_names") or "—")
        if len(manufacturers) > 120:
            manufacturers = manufacturers[:117] + "..."
        if len(item_names) > 180:
            item_names = item_names[:177] + "..."

        lines = [
            "🧾 <b>История OEM</b>",
            "",
            f"OEM: <code>{escape(str(term))}</code>",
            f"Запросов: <b>{int(payload.get('requests_count', total))}</b>",
            f"Клиентов: <b>{int(payload.get('clients_count', 0))}</b>",
            f"Всего запрошено: <b>{int(payload.get('total_qty', 0))} шт.</b>",
            f"Производитель: <b>{escape(manufacturers)}</b>",
            f"Название: {escape(item_names)}",
        ]
    elif kind == "part_name":
        lines = [
            "🔤 <b>Поиск по названию детали</b>",
            "",
            f"Запрос: <code>{escape(str(term))}</code>",
            f"Найдено OEM: <b>{found_text}</b>",
            "",
            "Выбери нужную позицию — откроется её история OEM.",
        ]
    else:
        lines = [
            "🔎 <b>Результаты поиска</b>",
            "",
            f"Запрос: <code>{escape(str(term))}</code>",
            f"Тип: <b>{escape(label)}</b>",
            f"Найдено: <b>{found_text}</b>",
        ]

    if total_pages > 1:
        lines.append(f"Страница: <b>{page + 1}/{total_pages}</b>")

    if kind in {"customer_name", "customer_identity"}:
        lines.extend([
            "",
            "Совпадения сгруппированы по Telegram-клиентам.",
            "Выбери нужного клиента по username / Telegram ID.",
        ])
    elif kind == "customer_orders":
        customer = payload.get("customer_name") or "Без имени"
        username = payload.get("username")
        lines.extend([
            "",
            f"Клиент: <b>{escape(str(customer))}</b>",
            (
                f"Username: <code>@{escape(str(username))}</code>"
                if username
                else "Username: —"
            ),
            f"Telegram ID: <code>{escape(str(term))}</code>",
        ])
    elif kind == "oem_history" and total:
        lines.extend([
            "",
            "Ниже — запросы, в которых встречался этот OEM.",
        ])

    return "\n".join(lines)


def admin_search_results_keyboard(
    payload: dict,
    page: int = 0,
    page_size: int = 10,
) -> InlineKeyboardMarkup:
    items = payload.get("items") or []
    kind = payload.get("kind")
    total = int(payload.get("total", len(items)))
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(max(int(page), 0), total_pages - 1)
    start = page * page_size
    visible = items[start:start + page_size]
    rows = []

    if kind == "part_name":
        for (
            oem,
            manufacturer,
            item_name,
            requests_count,
            clients_count,
            total_qty,
            activity_at,
        ) in visible:
            manufacturer = str(manufacturer or "—")
            item_name = str(item_name or "—")
            text = (
                f"🧾 {oem} | {manufacturer} | {item_name} | "
                f"{int(requests_count or 0)} запр."
            )
            rows.append([
                InlineKeyboardButton(
                    text[:64],
                    callback_data=_admin_part_result_callback(
                        str(oem or ""),
                        str(payload.get("term") or ""),
                        page,
                    ),
                )
            ])
    elif kind in {"customer_name", "customer_identity"}:
        for telegram_id, customer_name, username, order_count, activity_at in visible:
            customer = (customer_name or "Без имени")[:24]
            identity = f"@{username}" if username else f"ID {telegram_id}"
            text = f"👤 {customer} · {identity} · {order_count} запрос."
            rows.append([
                InlineKeyboardButton(
                    text[:64],
                    callback_data=f"admin:searchcustomer:{telegram_id}",
                )
            ])
    elif kind == "customer_orders":
        customer_id = str(payload.get("term") or "")
        for (
            order_id,
            created_at,
            status,
            goods_rub,
            delivery_rub,
            delivery_pending,
            activity_at,
        ) in visible:
            icon = _admin_search_status_icon(status)
            status_label = _admin_search_status_label(status)
            order_date = _admin_search_date(created_at)
            total_text = _admin_search_total_text(
                goods_rub,
                delivery_rub,
                delivery_pending,
            )
            text = (
                f"{icon} {order_id} | {order_date} | "
                f"{total_text} | {status_label}"
            )
            rows.append([
                InlineKeyboardButton(
                    text[:64],
                    callback_data=(
                        f"admin:searchorder:c:{page}:{customer_id}:{order_id}"
                    ),
                )
            ])
    elif kind == "oem_history":
        encoded_oem = _cb_b64_encode(str(payload.get("term") or ""))
        for (
            order_id,
            created_at,
            status,
            customer_name,
            username,
            telegram_id,
            manufacturer,
            item_name,
            quantity,
            price_usd,
            activity_at,
        ) in visible:
            icon = _admin_search_status_icon(status)
            order_date = _admin_search_date(created_at)
            customer = (customer_name or "Без имени")[:18]
            qty = int(quantity or 0)
            text = f"{icon} {order_id} | {order_date} | {customer} | {qty} шт."
            rows.append([
                InlineKeyboardButton(
                    text[:64],
                    callback_data=(
                        f"admin:searchorder:o:{page}:{encoded_oem}:{order_id}"
                    ),
                )
            ])
    else:
        for order_id, activity_at, customer_name, username, telegram_id, status in visible:
            customer = (customer_name or "Без имени")[:24]
            icon = _admin_search_status_icon(status)
            rows.append([
                InlineKeyboardButton(
                    f"{icon} {order_id} · {customer}"[:60],
                    callback_data=f"admin:order:{order_id}",
                )
            ])

    if total_pages > 1:
        nav = []
        if page > 0:
            nav.append(InlineKeyboardButton(
                "⬅️",
                callback_data=f"admin:searchpage:{page - 1}",
            ))
        nav.append(InlineKeyboardButton(
            f"{page + 1}/{total_pages}",
            callback_data="noop",
        ))
        if page + 1 < total_pages:
            nav.append(InlineKeyboardButton(
                "➡️",
                callback_data=f"admin:searchpage:{page + 1}",
            ))
        rows.append(nav)

    if kind == "oem_history" and payload.get("parent_part_term"):
        parent_term = str(payload.get("parent_part_term") or "")
        parent_page = int(payload.get("parent_part_page", 0) or 0)
        encoded_parent = _cb_b64_encode(parent_term)
        callback = f"admin:returnpart:{parent_page}:{encoded_parent}"
        if len(callback.encode("utf-8")) <= 64:
            rows.append([
                InlineKeyboardButton(
                    f"⬅️ К результатам «{parent_term[:24]}»",
                    callback_data=callback,
                )
            ])

    rows.append([InlineKeyboardButton("🔎 Новый поиск", callback_data="admin:search")])
    rows.append([InlineKeyboardButton("📦 К запросам", callback_data="admin:orders")])
    rows.append([InlineKeyboardButton("⚙️ В админку", callback_data="admin:home")])
    return InlineKeyboardMarkup(rows)


ITEM_TYPE_INFO = {
    "part": {
        "label": "🔧 Запчасть",
        "delivery": "🟢 Комфорт / 🔵 Эконом",
    },
    "accessory": {
        "label": "🧳 Аксессуар",
        "delivery": "🟢 Комфорт / 🔵 Эконом",
    },
    "gear": {
        "label": "👕 Экипировка",
        "delivery": "🔵 Эконом / 🔴 Mix",
    },
}


def get_admin_order_item_types(order_id: str):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        return conn.execute(
            """
            SELECT
                id,
                position,
                manufacturer,
                oem,
                name,
                quantity,
                item_type
            FROM order_items
            WHERE order_id = ?
            ORDER BY position
            """,
            (order_id,),
        ).fetchall()


def apply_known_item_types(order_id: str) -> int:
    """Apply manual profile first, then OEM-БАЗА; also snapshot safe reference weights."""
    updated = 0
    now = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        items = conn.execute(
            """
            SELECT
                id,
                manufacturer,
                oem,
                requested_oem,
                item_type,
                item_type_source,
                reference_actual_weight_kg,
                reference_volume_weight_kg
            FROM order_items
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchall()

        for (
            item_id,
            manufacturer,
            oem,
            requested_oem,
            current_item_type,
            current_item_type_source,
            current_actual_weight,
            current_volume_weight,
        ) in items:
            manufacturer = str(manufacturer or "").strip()
            oem = str(oem or "").strip()
            requested_oem = str(requested_oem or "").strip()

            profile = None
            if manufacturer and oem:
                profile = conn.execute(
                    """
                    SELECT item_type
                    FROM oem_delivery_profiles
                    WHERE manufacturer = ? COLLATE NOCASE
                      AND oem = ? COLLATE NOCASE
                    LIMIT 1
                    """,
                    (manufacturer, oem),
                ).fetchone()

            reference = get_oem_reference(oem, requested_oem)
            resolved_type = current_item_type
            resolved_source = current_item_type_source

            if profile:
                resolved_type = profile[0]
                resolved_source = "manual_profile"
            elif not resolved_type and reference and reference.get("item_type"):
                resolved_type = reference["item_type"]
                resolved_source = "oem_reference"

            ref_actual = (
                reference.get("actual_weight_kg")
                if reference and current_actual_weight is None
                else None
            )
            ref_volume = (
                reference.get("volume_weight_kg")
                if reference and current_volume_weight is None
                else None
            )
            ref_state = (
                str(reference.get("weight_state") or "") or None
                if reference
                else None
            )
            ref_source = (
                "oem_reference"
                if reference
                and (
                    reference.get("actual_weight_kg") is not None
                    or reference.get("volume_weight_kg") is not None
                )
                else None
            )

            changed = (
                resolved_type != current_item_type
                or resolved_source != current_item_type_source
                or ref_actual is not None
                or ref_volume is not None
            )
            if not changed:
                continue

            conn.execute(
                """
                UPDATE order_items
                SET
                    item_type = ?,
                    item_type_source = ?,
                    reference_actual_weight_kg = COALESCE(reference_actual_weight_kg, ?),
                    reference_volume_weight_kg = COALESCE(reference_volume_weight_kg, ?),
                    reference_weight_state = COALESCE(reference_weight_state, ?),
                    reference_weight_source = COALESCE(reference_weight_source, ?)
                WHERE id = ?
                """,
                (
                    resolved_type,
                    resolved_source,
                    ref_actual,
                    ref_volume,
                    ref_state,
                    ref_source,
                    item_id,
                ),
            )
            updated += 1

        if updated:
            conn.execute(
                """
                UPDATE orders
                SET updated_at = ?
                WHERE order_id = ?
                """,
                (now, order_id),
            )

        conn.commit()

    return updated


def set_order_item_type(order_id: str, order_item_id: int, item_type: str) -> bool:
    if item_type not in ITEM_TYPE_INFO:
        return False

    now = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        item = conn.execute(
            """
            SELECT manufacturer, oem, item_type,
                   selected_delivery_tariff, delivery_selected_at
            FROM order_items
            WHERE id = ?
              AND order_id = ?
            """,
            (order_item_id, order_id),
        ).fetchone()

        if not item:
            return False

        selected_tariff = (
            item[3] if item[3] in ITEM_TYPE_ALLOWED_TARIFFS[item_type] else None
        )
        selected_at = item[4] if selected_tariff is not None else None

        manufacturer = str(item[0] or "").strip()
        oem = str(item[1] or "").strip()

        conn.execute(
            """
            UPDATE order_items
            SET item_type = ?,
                item_type_source = "manual_profile",
                selected_delivery_tariff = ?,
                delivery_selected_at = ?
            WHERE id = ?
              AND order_id = ?
            """,
            (item_type, selected_tariff, selected_at, order_item_id, order_id),
        )

        if manufacturer and oem:
            conn.execute(
                """
                INSERT INTO oem_delivery_profiles (
                    manufacturer,
                    oem,
                    item_type,
                    created_at,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(manufacturer, oem)
                DO UPDATE SET
                    item_type = excluded.item_type,
                    updated_at = excluded.updated_at
                """,
                (
                    manufacturer,
                    oem,
                    item_type,
                    now,
                    now,
                ),
            )

        conn.execute(
            """
            UPDATE orders
            SET updated_at = ?
            WHERE order_id = ?
            """,
            (now, order_id),
        )

        conn.commit()

    return True


def format_admin_item_types(order_id: str, classify: bool = True):
    if classify:
        apply_known_item_types(order_id)
    items = get_admin_order_item_types(order_id)

    if not items:
        return None

    lines = [
        "📦 <b>Типы позиций</b>",
        "",
        f"<b>Запрос:</b> <code>{escape(str(order_id))}</code>",
        "",
    ]

    missing = 0

    for (
        item_id,
        position,
        manufacturer,
        oem,
        name,
        quantity,
        item_type,
    ) in items:
        info = ITEM_TYPE_INFO.get(str(item_type or ""))

        if info:
            type_text = info["label"]
            delivery_text = info["delivery"]
        else:
            type_text = "⚠️ Не указан"
            delivery_text = "—"
            missing += 1

        lines.extend([
            f"<b>{position}. {escape(str(manufacturer or '—'))}</b>",
            f"OEM: <code>{escape(str(oem or '—'))}</code>",
            f"Название: {escape(str(name or '—'))}",
            f"Количество: <b>{quantity} шт.</b>",
            f"<b>Тип:</b> {type_text}",
            f"<b>Доступная доставка:</b> {delivery_text}",
            "",
        ])

    if missing:
        lines.extend([
            f"⚠️ Не определён тип позиций: <b>{missing}</b>",
            "",
            "Выбери тип каждой неопределённой позиции.",
        ])
    else:
        lines.append("✅ <b>Типы всех позиций определены.</b>")

    return "\n".join(lines)


def admin_item_types_keyboard(order_id: str) -> InlineKeyboardMarkup:
    items = get_admin_order_item_types(order_id)
    rows = []

    for (
        item_id,
        position,
        manufacturer,
        oem,
        name,
        quantity,
        item_type,
    ) in items:
        current = ITEM_TYPE_INFO.get(str(item_type or ""))
        current_label = current["label"] if current else "⚠️ Не указан"

        rows.append([
            InlineKeyboardButton(
                f"{position}. {oem or '—'} · {current_label}",
                callback_data="noop",
            )
        ])

        rows.append([
            InlineKeyboardButton(
                "🔧 Запчасть",
                callback_data=f"admin:setitemtype:{order_id}:{item_id}:part",
            ),
            InlineKeyboardButton(
                "🧳 Аксессуар",
                callback_data=f"admin:setitemtype:{order_id}:{item_id}:accessory",
            ),
        ])

        rows.append([
            InlineKeyboardButton(
                "👕 Экипировка",
                callback_data=f"admin:setitemtype:{order_id}:{item_id}:gear",
            )
        ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ К запросу",
            callback_data=f"admin:order:{order_id}",
        )
    ])

    return InlineKeyboardMarkup(rows)


def format_admin_order(order_id: str):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        order = conn.execute(
            """
            SELECT
                order_id,
                created_at,
                telegram_user_id,
                customer_name,
                username,
                total_usd,
                usd_rub_rate,
                status,
                customer_total_rub,
                auto_pricing_status,
                delivery_rub,
                delivery_pending,
                delivery_tariff,
                origin
            FROM orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        items = conn.execute(
            """
            SELECT
                position,
                manufacturer,
                oem,
                requested_oem,
                name,
                quantity,
                price_usd,
                offer_source,
                warehouse_public_name,
                customer_unit_rub,
                selected_delivery_tariff
            FROM order_items
            WHERE order_id = ?
            ORDER BY position
            """,
            (order_id,),
        ).fetchall()

    (
        saved_order_id,
        created_at,
        telegram_user_id,
        customer_name,
        username,
        total_usd,
        usd_rub_rate,
        status,
        customer_total_rub,
        auto_pricing_status,
        delivery_rub,
        delivery_pending,
        delivery_tariff,
        origin,
    ) = order

    stage2_snapshot = get_stage2_customer_total(order_id)
    effective_delivery_rub = (
        stage2_snapshot[2]
        if stage2_snapshot is not None
        else delivery_rub
    )

    try:
        dt = datetime.fromisoformat(created_at)
        created_text = dt.strftime("%d.%m.%Y %H:%M")
    except (TypeError, ValueError):
        created_text = str(created_at or "—")

    username_text = f"@{username}" if username else "—"

    status_labels = {
        "new": "🆕 Новый",
        "working": "🟡 В работе",
        "waiting": "🔴 В ожидании",
        "confirmed": "🟢 Подтверждён",
        "executing": "📦 Выполняется",
        "completed": "✅ Выполнен",
        "cancelled": "❌ Отменён",
        # Legacy value retained only for old records during migration.
        "calculated": "🔴 В ожидании",
    }

    status_text = status_labels.get(
        str(status or "new"),
        "🆕 Новый",
    )

    lines = [
        "📦 <b>Запрос</b>",
        "",
        f"<b>Номер:</b> <code>{escape(str(saved_order_id))}</code>",
        f"<b>Дата:</b> {escape(created_text)}",
        f"<b>Клиент:</b> {escape(str(customer_name or '—'))}",
        f"<b>Username:</b> {escape(username_text)}",
        f"<b>Telegram ID:</b> <code>{escape(str(telegram_user_id))}</code>",
        f"<b>Источник:</b> {'🌐 WEB' if str(origin or '').lower() == 'web' else '🤖 Telegram'}",
        f"<b>Статус:</b> {escape(status_text)}",
        "",
    ]

    has_usa = any(str(row[7] or "usa") == "usa" for row in items)
    for (
        position,
        manufacturer,
        oem,
        requested_oem,
        name,
        quantity,
        price_usd,
        offer_source,
        warehouse_public_name,
        customer_unit_rub,
        selected_delivery_tariff,
    ) in items:
        lines.extend([
            f"<b>{position}. {escape(str(manufacturer or '—'))}</b>",
            f"OEM: <code>{escape(str(oem or '—'))}</code>",
        ])
        if requested_oem and str(requested_oem) != str(oem):
            lines.append(
                f"Запрошен OEM: <code>{escape(str(requested_oem))}</code> → "
                f"актуальный: <code>{escape(str(oem or '—'))}</code>"
            )
        lines.extend([
            f"Название: {escape(str(name or '—'))}",
            f"Количество: <b>{quantity} шт.</b>",
        ])
        if str(offer_source or "usa") == "warehouse":
            lines.append(f"Источник: <b>🇷🇺 {escape(str(warehouse_public_name or 'склад'))}</b>")
            lines.append(
                f"Цена склада: <b>{format_rub_whole(customer_unit_rub)}</b> × {quantity}"
                if customer_unit_rub is not None
                else "Цена склада: —"
            )
        else:
            lines.append("Источник: <b>🇺🇸 США</b>")
            lines.append(
                "Цена в США: <b>$" + f"{price_usd:.2f}" + f"</b> × {quantity}"
                if isinstance(price_usd, (int, float))
                else "Цена в США: —"
            )
            selected_label = CUSTOMER_TARIFF_LABELS.get(
                str(selected_delivery_tariff or "").strip().lower()
            )
            if selected_label:
                lines.append(f"🚚 Доставка: <b>{selected_label}</b>")
        lines.append("")

    lines.extend([
        (f"<b>Итого по ценам США: ${float(total_usd):.2f}</b>" if has_usa else ""),
        (f"<b>Курс при оформлении: {float(usd_rub_rate):g} ₽/$</b>" if has_usa else ""),
        "",
        (
            "💰 <b>Стоимость товаров клиенту: "
            + format_rub_whole(customer_total_rub)
            + "</b>"
            if customer_total_rub is not None
            else "💰 <b>Стоимость товаров клиенту:</b> —"
        ),
    ])

    if str(auto_pricing_status or "").strip().lower() == "needs_review":
        lines.extend([
            "",
            "⚠️ <b>Автоцена не рассчитана.</b>",
            ("Нужен DP / ручная проверка перед отправкой итоговой цены клиенту." if has_usa else "Нужна ручная проверка складской цены перед отправкой итоговой цены клиенту."),
        ])

    if has_usa and delivery_pending:
        lines.append("🚚 <b>Доставка:</b> рассчитаем после получения")
    elif has_usa and effective_delivery_rub is not None:
        lines.append(
            "🚚 <b>Доставка:</b> "
            + format_rub_whole(effective_delivery_rub)
        )
        if customer_total_rub is not None:
            lines.append(
                "💳 <b>Итого к оплате: "
                + format_rub_whole(
                    Decimal(str(customer_total_rub))
                    + Decimal(str(effective_delivery_rub))
                )
                + "</b>"
            )
    elif has_usa and delivery_tariff:
        lines.append("🚚 <b>Доставка:</b> рассчитывается")

    stock_problems = warehouse_alerts.problems_for_order(
        order_id, ORDERS_DB_FILE
    )
    if stock_problems:
        problem_labels = {
            "confirmed_no_warehouse": "подтверждён, но склад не выбран",
            "executing_no_committed": "executing, но нет committed-резерва",
            "stock_unknown": "остаток неизвестен",
            "stock_stale": "остаток устарел",
            "stock_insufficient": "остатка недостаточно",
            "hanging_reservation": "зависший hold/reserved",
            "snapshot_mismatch": "несогласованность после snapshot",
        }
        lines.extend(["", "⚠️ <b>Складские предупреждения:</b>"])
        for problem in stock_problems[:8]:
            label = problem_labels.get(problem["kind"])
            if not label:
                continue
            suffix = ""
            if problem.get("oem"):
                suffix = (
                    f" · OEM <code>{escape(str(problem['oem']))}</code>"
                )
            lines.append(f"• {escape(label)}{suffix}")

    return "\n".join(lines)


def get_order_status(order_id: str):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        row = conn.execute(
            "SELECT status FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
    return row[0] if row else None


def _admin_order_search_return_button(context):
    if context is None:
        return None
    payload = context.user_data.get("admin_search_payload")
    if not isinstance(payload, dict) or not payload.get("items"):
        return None

    page = int(context.user_data.get("admin_search_page", 0) or 0)
    kind = payload.get("kind")

    if kind == "oem_history":
        term = str(payload.get("term") or "").strip()
        encoded = _cb_b64_encode(term)
        label = f"⬅️ К истории OEM {term}" if term else "⬅️ К истории OEM"
        return label, f"admin:returnsearch:o:{page}:{encoded}"

    if kind == "customer_orders":
        customer_id = str(payload.get("term") or "").strip()
        return (
            "⬅️ К запросам клиента",
            f"admin:returnsearch:c:{page}:{customer_id}",
        )

    if kind in {"customer_name", "customer_identity"}:
        return "⬅️ К найденным клиентам", "admin:returnsearch"

    return "⬅️ К результатам поиска", "admin:returnsearch"


def admin_order_keyboard(order_id: str, context=None) -> InlineKeyboardMarkup:
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        state = conn.execute(
            """SELECT status, quote_sent_at, final_quote_sent_at,
                      customer_total_rub, delivery_rub, delivery_pending
               FROM orders WHERE order_id = ?""",
            (order_id,),
        ).fetchone()

    status = state[0] if state else None
    preliminary_sent = bool(state and state[1])
    final_sent = bool(state and state[2])
    product_total = state[3] if state else None
    delivery_total = state[4] if state else None
    delivery_pending = bool(state and state[5])

    stage2_snapshot = get_stage2_customer_total(order_id)
    if stage2_snapshot is not None:
        product_total = stage2_snapshot[1]
        delivery_total = stage2_snapshot[2]

    final_ready = (
        product_total is not None
        and delivery_total is not None
        and not delivery_pending
    )

    stock_problem_count = len(
        warehouse_alerts.problems_for_order(order_id, ORDERS_DB_FILE)
    )
    stock_button_label = (
        f"⚠️ Склад / резерв · {stock_problem_count}"
        if stock_problem_count
        else "🏬 Склад / резерв"
    )

    rows = [
        [
            InlineKeyboardButton(
                "🔄 Изменить статус",
                callback_data=f"admin:status:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Стоимость товаров",
                callback_data=f"admin:customertotal:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Типы позиций",
                callback_data=f"admin:itemtypes:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                stock_button_label,
                callback_data=f"admin:stock:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🚚 Доставка",
                callback_data=f"admin:orderdelivery:{order_id}",
            )
        ],
    ]

    can_send_preliminary = (
        status in {"new", "working"}
        and not final_ready
        and not preliminary_sent
    )
    can_send_final = (
        status in {"new", "working", "waiting"}
        and final_ready
        and not final_sent
    )

    if can_send_preliminary or can_send_final:
        rows.append([
            InlineKeyboardButton(
                "📨 Отправить расчёт клиенту",
                callback_data=f"admin:quotepreview:{order_id}",
            )
        ])
    if status == "confirmed":
        rows.append([
            InlineKeyboardButton(
                "📦 Принять в исполнение",
                callback_data=f"admin:execute:{order_id}",
            )
        ])
    if status == "executing":
        rows.append([
            InlineKeyboardButton(
                "✅ Завершить запрос",
                callback_data=f"admin:complete:{order_id}",
            )
        ])
    back_status = "waiting" if status == "calculated" else status
    back_labels = {
        "new": "⬅️ К новым",
        "working": "⬅️ К «В работе»",
        "waiting": "⬅️ К «В ожидании»",
        "confirmed": "⬅️ К подтверждённым",
        "executing": "⬅️ К выполняемым",
        "completed": "⬅️ К выполненным",
        "cancelled": "⬅️ К отменённым",
    }
    search_return = _admin_order_search_return_button(context)
    if search_return:
        search_return_label, search_return_callback = search_return
        rows.append([
            InlineKeyboardButton(
                search_return_label,
                callback_data=search_return_callback,
            )
        ])

    if back_status in back_labels:
        rows.append([
            InlineKeyboardButton(
                back_labels[back_status],
                callback_data=f"admin:ordersstatus:{back_status}",
            )
        ])
    else:
        rows.append([
            InlineKeyboardButton("⬅️ К запросам", callback_data="admin:orders")
        ])

    rows.extend([
        [InlineKeyboardButton("📋 К статусам", callback_data="admin:orders")],
        [InlineKeyboardButton("⚙️ В админку", callback_data="admin:home")],
    ])
    return InlineKeyboardMarkup(rows)


def _legacy_admin_order_keyboard_unused(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Изменить статус",
                callback_data=f"admin:status:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "💰 Стоимость товаров",
                callback_data=f"admin:customertotal:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📦 Типы позиций",
                callback_data=f"admin:itemtypes:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🚚 Доставка",
                callback_data=f"admin:orderdelivery:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📨 Отправить расчёт клиенту",
                callback_data=f"admin:quotepreview:{order_id}",
            )
        ],
        [InlineKeyboardButton("⬅️ К запросам", callback_data="admin:orders")],
        [InlineKeyboardButton("⚙️ В админку", callback_data="admin:home")],
    ])


def get_stage2_customer_total(order_id: str, owner_id=None):
    """Read product total and delivery preference snapshot for Stage 2."""
    apply_known_item_types(order_id)
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        order = conn.execute(
            """SELECT telegram_user_id, customer_total_rub,
                      delivery_rub, delivery_pending, delivery_tariff
               FROM orders WHERE order_id = ?""",
            (order_id,),
        ).fetchone()
        if order is None:
            return None
        if owner_id is not None and int(order["telegram_user_id"]) != int(owner_id):
            return None
        items = conn.execute(
            """SELECT id, position, manufacturer, oem, name, quantity,
                      reference_actual_weight_kg,
                      reference_volume_weight_kg,
                      COALESCE(selected_delivery_tariff, ?) AS selected_delivery_tariff,
                      offer_source,
                      warehouse_public_name
               FROM order_items
               WHERE order_id = ?
               ORDER BY position""",
            (order["delivery_tariff"], order_id),
        ).fetchall()
        groups = conn.execute(
            "SELECT id, tariff_code, status, final_rub FROM delivery_groups WHERE order_id = ?",
            (order_id,),
        ).fetchall()
        links = conn.execute(
            "SELECT gi.delivery_group_id, gi.order_item_id FROM delivery_group_items gi "
            "LEFT JOIN delivery_groups g ON g.id = gi.delivery_group_id "
            "LEFT JOIN order_items i ON i.id = gi.order_item_id "
            "WHERE g.order_id = ? OR i.order_id = ?",
            (order_id, order_id),
        ).fetchall()

    item_ids = {row["id"] for row in items}
    group_ids = {row["id"] for row in groups}
    item_tariffs = {row["id"]: row["selected_delivery_tariff"] for row in items}
    group_tariffs = {row["id"]: row["tariff_code"] for row in groups}
    counts = {item_id: 0 for item_id in item_ids}
    group_counts = {group_id: 0 for group_id in group_ids}
    valid_links = True
    for link in links:
        group_id, item_id = link
        if group_id not in group_ids or item_id not in item_ids:
            valid_links = False
            continue
        counts[item_id] += 1
        group_counts[group_id] += 1
        if item_tariffs[item_id] != group_tariffs[group_id]:
            valid_links = False

    complete = (
        bool(items) and bool(groups) and valid_links
        and all(count == 1 for count in counts.values())
        and all(count > 0 for count in group_counts.values())
    )
    delivery = Decimal("0")
    for group in groups:
        value = group["final_rub"]
        if group["status"] != "calculated" or value is None:
            complete = False
            continue
        amount = Decimal(str(value))
        if not amount.is_finite() or amount < 0:
            complete = False
            continue
        delivery += amount

    product_raw = order["customer_total_rub"]
    product = Decimal(str(product_raw)) if product_raw is not None else None
    if product is not None and (not product.is_finite() or product < 0):
        return None

    delivery_result = delivery if complete else None

    # Compatibility path for the proven order-level delivery workflow.
    # Use it only when Stage 2 delivery groups do not exist at all.
    if not groups and not bool(order["delivery_pending"]):
        legacy_raw = order["delivery_rub"]
        if legacy_raw is not None:
            legacy_delivery = Decimal(str(legacy_raw))
            if legacy_delivery.is_finite() and legacy_delivery >= 0:
                delivery_result = legacy_delivery

    return items, product, delivery_result


def format_rub_whole(value) -> str:
    """Display RUB without kopecks; USD formatting is intentionally unchanged."""
    rounded = Decimal(str(value)).quantize(Decimal("1"), rounding="ROUND_HALF_UP")
    return f"{int(rounded):,}".replace(",", " ") + " ₽"


def format_stage2_customer_total(order_id: str, owner_id=None):
    snapshot = get_stage2_customer_total(order_id, owner_id)
    if snapshot is None:
        return None
    items, product, _delivery = snapshot
    labels = {
        "comfort": "🟢 Комфорт",
        "economy": "🔵 Эконом",
        "mix": "🔴 MIX",
    }
    lines = [
        "📦 <b>Итог по запросу</b>",
        f"<b>Номер:</b> <code>{escape(order_id)}</code>",
        "",
    ]
    has_reference_weight = False
    has_usa = any(str(item["offer_source"] or "usa") == "usa" for item in items)
    for item in items:
        actual = item["reference_actual_weight_kg"]
        volume = item["reference_volume_weight_kg"]
        if actual is not None or volume is not None:
            has_reference_weight = True

        lines.extend([
            f"<b>{item['position']}. {escape(str(item['manufacturer'] or '—'))}</b>",
            f"OEM: <code>{escape(str(item['oem'] or '—'))}</code>",
            f"Название: {escape(str(item['name'] or '—'))}",
            f"Количество: <b>{item['quantity']} шт.</b>",
        ])
        if str(item["offer_source"] or "usa") == "warehouse":
            lines.append(
                f"Источник: <b>🇷🇺 {escape(str(item['warehouse_public_name'] or 'склад'))}</b>"
            )
        else:
            lines.append("Источник: <b>🇺🇸 США</b>")
        lines.extend(
            reference_weight_lines(
                actual,
                volume,
                item["quantity"],
            )
        )
        preference = labels.get(item["selected_delivery_tariff"])
        if preference:
            lines.append("Предпочтение по доставке: " + preference)
        lines.append("")

    if has_reference_weight:
        lines.extend([REFERENCE_WEIGHT_NOTICE, ""])

    lines.append(
        "<b>Стоимость товаров:</b> "
        + (
            format_rub_whole(product)
            if product is not None
            else "уточняется менеджером"
        )
    )
    lines.extend(["", (DELIVERY_SEPARATE_NOTICE if has_usa else "")])
    return "\n".join(lines)


def format_customer_quote(order_id: str):
    """Build customer quote: goods price + reference weights; delivery is separate."""
    apply_known_item_types(order_id)

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        order = conn.execute(
            """
            SELECT
                order_id,
                customer_name,
                customer_total_rub,
                delivery_tariff
            FROM orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        items = conn.execute(
            """
            SELECT
                position,
                manufacturer,
                oem,
                name,
                quantity,
                reference_actual_weight_kg,
                reference_volume_weight_kg,
                selected_delivery_tariff,
                offer_source,
                warehouse_public_name
            FROM order_items
            WHERE order_id = ?
            ORDER BY position
            """,
            (order_id,),
        ).fetchall()

    saved_order_id, customer_name, customer_total_rub, delivery_tariff = order

    if customer_total_rub is None:
        return None

    tariff_labels = {
        "comfort": "🟢 Комфорт",
        "economy": "🔵 Эконом",
        "mix": "🔴 MIX",
    }

    lines = [
        "📦 <b>Расчёт по запросу</b>",
        "",
        f"<b>Номер:</b> <code>{escape(str(saved_order_id))}</code>",
        "",
    ]

    if customer_name:
        lines.extend([
            f"{escape(str(customer_name))}, расчёт готов.",
            "",
        ])

    order_preference = tariff_labels.get(str(delivery_tariff or ""))
    if order_preference:
        lines.extend([
            "🚚 <b>Предпочтение по доставке:</b> " + order_preference,
            "",
        ])

    has_reference_weight = False
    has_usa = any(str(row[8] or "usa") == "usa" for row in items)
    for (
        position,
        manufacturer,
        oem,
        name,
        quantity,
        reference_actual_weight_kg,
        reference_volume_weight_kg,
        selected_delivery_tariff,
        offer_source,
        warehouse_public_name,
    ) in items:
        if (
            reference_actual_weight_kg is not None
            or reference_volume_weight_kg is not None
        ):
            has_reference_weight = True

        lines.extend([
            f"<b>{position}. {escape(str(manufacturer or '—'))}</b>",
            f"OEM: <code>{escape(str(oem or '—'))}</code>",
            f"Название: {escape(str(name or '—'))}",
            f"Количество: <b>{quantity} шт.</b>",
        ])
        if str(offer_source or "usa") == "warehouse":
            lines.append(f"Источник: <b>🇷🇺 {escape(str(warehouse_public_name or 'склад'))}</b>")
        else:
            lines.append("Источник: <b>🇺🇸 США</b>")
        lines.extend(
            reference_weight_lines(
                reference_actual_weight_kg,
                reference_volume_weight_kg,
                quantity,
            )
        )
        preference = tariff_labels.get(str(selected_delivery_tariff or ""))
        if preference:
            lines.append("Предпочтение по доставке: " + preference)
        lines.append("")

    if has_reference_weight:
        lines.extend([REFERENCE_WEIGHT_NOTICE, ""])

    lines.extend([
        f"💰 <b>Стоимость товаров: {format_rub_whole(customer_total_rub)}</b>",
        "",
        (DELIVERY_SEPARATE_NOTICE if has_usa else ""),
    ])

    return "\n".join(lines)

def prepare_auto_quote_after_checkout(order_id: str, telegram_user_id: int):
    """Promote a fully auto-priced request directly to the customer quote stage."""
    apply_known_item_types(order_id)
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        row = conn.execute(
            """
            SELECT customer_total_rub, auto_pricing_status, telegram_user_id
            FROM orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()
        if not row or int(row[2]) != int(telegram_user_id):
            return None
        if row[0] is None or str(row[1] or "").lower() != "calculated":
            return None

        quote_text = format_customer_quote(order_id)
        if quote_text is None:
            return None

        now = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            """
            UPDATE orders
            SET status = 'waiting',
                quote_sent_at = COALESCE(quote_sent_at, ?),
                updated_at = ?
            WHERE order_id = ?
              AND telegram_user_id = ?
            """,
            (now, now, order_id, int(telegram_user_id)),
        )
        conn.commit()

    return quote_text


ITEM_TYPE_ALLOWED_TARIFFS = {
    "part": ("comfort", "economy"),
    "accessory": ("comfort", "economy"),
    "gear": ("economy", "mix"),
}

CUSTOMER_TARIFF_LABELS = {
    "comfort": "🟢 Комфорт",
    "economy": "🔵 Эконом",
    "mix": "🔴 MIX",
}


def resolve_cart_item_type(item: dict) -> str | None:
    """Resolve delivery item_type before order persistence."""
    if str(item.get("offer_source") or "usa").strip().lower() != "usa":
        return None

    manufacturer = str(item.get("manufacturer") or "").strip()
    current_oem = str(item.get("oem") or "").strip()
    requested_oem = str(item.get("requested_oem") or current_oem).strip()

    if manufacturer and current_oem:
        with sqlite3.connect(ORDERS_DB_FILE) as conn:
            profile = conn.execute(
                """
                SELECT item_type
                FROM oem_delivery_profiles
                WHERE manufacturer = ? COLLATE NOCASE
                  AND oem = ? COLLATE NOCASE
                LIMIT 1
                """,
                (manufacturer, current_oem),
            ).fetchone()
        if profile and profile[0] in ITEM_TYPE_ALLOWED_TARIFFS:
            return str(profile[0])

    reference = get_oem_reference(current_oem, requested_oem)
    reference_type = reference.get("item_type") if reference else None
    if reference_type in ITEM_TYPE_ALLOWED_TARIFFS:
        return str(reference_type)

    dcp_type = infer_item_type_from_dcp_catalog(item.get("catalog"))
    if dcp_type in ITEM_TYPE_ALLOWED_TARIFFS:
        return str(dcp_type)

    return None


def prepare_cart_delivery_choices(cart: dict) -> None:
    """Normalize per-item USA delivery selections in the live cart."""
    for item in cart.values():
        source = str(item.get("offer_source") or "usa").strip().lower()
        if source != "usa":
            item.pop("_checkout_item_type", None)
            item.pop("selected_delivery_tariff", None)
            item.pop("delivery_selected_at", None)
            continue

        item_type = resolve_cart_item_type(item)
        item["_checkout_item_type"] = item_type
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(str(item_type or ""), ())
        selected = str(item.get("selected_delivery_tariff") or "").strip().lower()
        if selected not in allowed:
            item.pop("selected_delivery_tariff", None)
            item.pop("delivery_selected_at", None)


def cart_delivery_choice_complete(cart: dict) -> bool:
    prepare_cart_delivery_choices(cart)
    has_usa = False
    for item in cart.values():
        if str(item.get("offer_source") or "usa").strip().lower() != "usa":
            continue
        has_usa = True
        item_type = str(item.get("_checkout_item_type") or "")
        selected = str(item.get("selected_delivery_tariff") or "")
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ())
        if not allowed or selected not in allowed:
            return False
    return has_usa


def common_cart_delivery_preference(cart: dict) -> str | None:
    """Return one shared USA tariff only when every USA item uses the same one."""
    selected = {
        str(item.get("selected_delivery_tariff") or "")
        for item in cart.values()
        if str(item.get("offer_source") or "usa").strip().lower() == "usa"
    }
    selected.discard("")
    return next(iter(selected)) if len(selected) == 1 else None


def format_checkout_delivery_choices(cart: dict) -> str:
    prepare_cart_delivery_choices(cart)
    # Checkout shows only delivery methods actually available for USA items in this cart.
    available_tariffs = []
    for item in cart.values():
        if str(item.get("offer_source") or "usa").strip().lower() != "usa":
            continue
        item_type = str(item.get("_checkout_item_type") or "")
        for tariff_code in ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ()):
            if tariff_code not in available_tariffs:
                available_tariffs.append(tariff_code)

    tariffs = _delivery_tariff_map()
    settings = get_delivery_text_settings()
    short_eta = {
        "comfort": settings.get("comfort_short_eta", "3–4 недели"),
        "economy": settings.get("economy_eta", "от 12 недель"),
        "mix": settings.get("mix_eta", "от 5 недель"),
    }
    lines = ["🚚 <b>Доставка из США</b>", ""]
    for tariff_code in available_tariffs:
        tariff = tariffs.get(tariff_code)
        if tariff:
            lines.append(
                f"{CUSTOMER_TARIFF_LABELS[tariff_code]} — {_delivery_rate(tariff['base'])} · "
                f"{escape(short_eta[tariff_code])}"
            )
    if available_tariffs:
        lines.extend(["", "Доставка оплачивается отдельно после прихода груза в Москву.", ""])
    lines.extend(["<b>Выберите способ доставки:</b>", ""])
    has_warehouse = False

    for position, item in enumerate(cart.values(), 1):
        source = str(item.get("offer_source") or "usa").strip().lower()
        if source != "usa":
            has_warehouse = True
            continue

        oem = str(item.get("oem") or "—")
        item_type = str(item.get("_checkout_item_type") or "")
        type_info = ITEM_TYPE_INFO.get(item_type)
        type_label = type_info["label"] if type_info else "⚠️ Тип товара не определён"
        selected = str(item.get("selected_delivery_tariff") or "")
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ())

        lines.extend([
            f"<b>{position}. OEM <code>{escape(oem)}</code></b>",
            f"Тип: {type_label}",
        ])
        if selected in allowed:
            lines.append(f"Выбрано: ✅ {CUSTOMER_TARIFF_LABELS[selected]}")
        elif allowed:
            lines.append(
                "Доступно: "
                + " / ".join(CUSTOMER_TARIFF_LABELS[code] for code in allowed)
            )
        else:
            lines.append("⚠️ Для позиции пока нельзя определить способ доставки.")
        lines.append("")

    if cart_delivery_choice_complete(cart):
        lines.append("✅ <b>Доставка выбрана.</b>")

    return "\n".join(lines)


def checkout_delivery_keyboard(cart: dict) -> InlineKeyboardMarkup:
    prepare_cart_delivery_choices(cart)
    rows = []
    for position, item in enumerate(cart.values(), 1):
        if str(item.get("offer_source") or "usa").strip().lower() != "usa":
            continue
        item_type = str(item.get("_checkout_item_type") or "")
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ())
        selected = str(item.get("selected_delivery_tariff") or "")
        if not allowed:
            continue
        buttons = []
        for tariff_code in allowed:
            label = CUSTOMER_TARIFF_LABELS[tariff_code]
            if selected == tariff_code:
                label = "✅ " + label
            buttons.append(InlineKeyboardButton(
                f"{position}. {label}",
                callback_data=f"checkout_item_delivery:{position}:{tariff_code}",
            ))
        rows.append(buttons)

    rows.append([InlineKeyboardButton(
        "ℹ️ Подробнее о доставке",
        callback_data="checkout_delivery_details",
    )])
    if cart_delivery_choice_complete(cart):
        rows.append([InlineKeyboardButton(
            "✅ Подтвердить запрос",
            callback_data="checkout_confirm",
        )])
    rows.append([InlineKeyboardButton(
        "⬅️ Вернуться в корзину",
        callback_data="cart",
    )])
    return InlineKeyboardMarkup(rows)


def get_customer_delivery_items(order_id: str):
    """Return order owner and all positions needed for customer delivery choice."""
    apply_known_item_types(order_id)

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        order = conn.execute(
            """
            SELECT telegram_user_id
            FROM orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()

        if not order:
            return None

        items = conn.execute(
            """
            SELECT
                id,
                position,
                manufacturer,
                oem,
                name,
                quantity,
                item_type,
                selected_delivery_tariff,
                delivery_selected_at,
                offer_source
            FROM order_items
            WHERE order_id = ?
            ORDER BY position
            """,
            (order_id,),
        ).fetchall()

    return int(order[0]), items


def set_customer_item_delivery_tariff(
    order_id: str,
    order_item_id: int,
    telegram_user_id: int,
    tariff_code: str,
) -> tuple[bool, str]:
    """Validate ownership and item compatibility, then save customer choice."""
    from contextlib import closing

    db_uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(db_uri, uri=True)) as conn:
        conn.execute("BEGIN")
        owner = conn.execute(
            "SELECT telegram_user_id FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()

        if owner is None:
            return False, "Запрос не найден."

        if int(telegram_user_id) != int(owner[0]):
            return False, "Этот запрос принадлежит другому пользователю."

        selected_item = conn.execute(
            """
            SELECT item_type, offer_source
            FROM order_items
            WHERE id = ? AND order_id = ?
            """,
            (order_item_id, order_id),
        ).fetchone()

    if selected_item is None:
        return False, "Позиция не найдена."

    item_type = str(selected_item[0] or "")
    offer_source = str(selected_item[1] or "usa").strip().lower()
    if offer_source != "usa":
        return False, "Для складской позиции доставка из США не выбирается."
    allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type)

    if not allowed:
        return False, "Для этой позиции сначала нужно определить тип товара."

    tariff_code = str(tariff_code or "").strip().lower()

    if tariff_code not in allowed:
        return False, "Этот способ доставки недоступен для данной позиции."

    tariff = get_delivery_tariff(tariff_code)

    if not tariff or not tariff[5]:
        return False, "Этот способ доставки сейчас недоступен."

    selected_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            """
            UPDATE order_items
            SET
                selected_delivery_tariff = ?,
                delivery_selected_at = ?
            WHERE id = ?
              AND order_id = ?
            """,
            (
                tariff_code,
                selected_at,
                order_item_id,
                order_id,
            ),
        )

        if cursor.rowcount != 1:
            conn.rollback()
            return False, "Не удалось сохранить способ доставки."

        conn.execute(
            """
            UPDATE orders
            SET updated_at = ?
            WHERE order_id = ?
            """,
            (selected_at, order_id),
        )

        conn.commit()

    return True, "Способ доставки сохранён."


def customer_delivery_choice_complete(order_id: str) -> bool:
    data = get_customer_delivery_items(order_id)

    if not data:
        return False

    _owner_user_id, items = data

    if not items:
        return False

    for item in items:
        if str(item[9] or "usa").strip().lower() != "usa":
            continue
        item_type = str(item[6] or "")
        selected_tariff = str(item[7] or "")

        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type)

        if not allowed or selected_tariff not in allowed:
            return False

    return True


def format_customer_delivery_choice(order_id: str):
    data = get_customer_delivery_items(order_id)

    if not data:
        return None

    _owner_user_id, items = data

    if not items:
        return None

    lines = [
        "🚚 <b>Выбор способа доставки</b>",
        "",
        f"<b>Запрос:</b> <code>{escape(str(order_id))}</code>",
        "",
        (
            "Способ доставки необходимо выбрать "
            "<b>до отправки позиций запроса из США</b>."
        ),
        "",
    ]

    for (
        item_id,
        position,
        manufacturer,
        oem,
        name,
        quantity,
        item_type,
        selected_tariff,
        delivery_selected_at,
        offer_source,
    ) in items:
        type_info = ITEM_TYPE_INFO.get(str(item_type or ""))
        type_label = (
            type_info["label"]
            if type_info
            else "⚠️ Тип товара не определён"
        )

        selected_label = CUSTOMER_TARIFF_LABELS.get(
            str(selected_tariff or "")
        )

        lines.extend([
            f"<b>{position}. {escape(str(manufacturer or '—'))}</b>",
            f"OEM: <code>{escape(str(oem or '—'))}</code>",
            f"Название: {escape(str(name or '—'))}",
            f"Количество: <b>{quantity} шт.</b>",
        ])
        if str(offer_source or "usa").strip().lower() != "usa":
            lines.extend([
                "Источник: <b>🇷🇺 склад</b>",
                "Доставка из США: <b>не требуется</b>",
                "",
            ])
            continue
        lines.append(f"<b>Тип:</b> {type_label}")

        if selected_label:
            lines.append(
                f"<b>Выбрано:</b> ✅ {selected_label}"
            )
        else:
            allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(
                str(item_type or ""),
                (),
            )

            if allowed:
                allowed_text = " / ".join(
                    CUSTOMER_TARIFF_LABELS[x]
                    for x in allowed
                )
                lines.append(
                    f"<b>Доступно:</b> {allowed_text}"
                )
                lines.append(
                    "<b>Выбери способ доставки этой позиции.</b>"
                )
            else:
                lines.append(
                    "⚠️ Способ доставки пока выбрать нельзя."
                )

        lines.append("")

    if customer_delivery_choice_complete(order_id):
        lines.extend([
            "✅ <b>Способ доставки выбран для всех позиций.</b>",
            "",
            (
                "Выбранный способ доставки будет использован "
                "для расчёта стоимости доставки после прихода "
                "груза в Москву."
            ),
        ])
    else:
        lines.append(
            "Выбери способ доставки для каждой позиции."
        )

    return "\n".join(lines)


def customer_delivery_choice_keyboard(
    order_id: str,
) -> InlineKeyboardMarkup:
    data = get_customer_delivery_items(order_id)

    if not data:
        return InlineKeyboardMarkup([])

    _owner_user_id, items = data
    rows = []

    for (
        item_id,
        position,
        manufacturer,
        oem,
        name,
        quantity,
        item_type,
        selected_tariff,
        delivery_selected_at,
        offer_source,
    ) in items:
        if str(offer_source or "usa").strip().lower() != "usa":
            continue
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(
            str(item_type or ""),
            (),
        )

        if not allowed:
            continue

        buttons = []

        for tariff_code in allowed:
            label = CUSTOMER_TARIFF_LABELS[tariff_code]

            if str(selected_tariff or "") == tariff_code:
                label = "✅ " + label

            buttons.append(
                InlineKeyboardButton(
                    f"{position}. {label}",
                    callback_data=(
                        f"deliverychoice:{order_id}:"
                        f"{item_id}:{tariff_code}"
                    ),
                )
            )

        rows.append(buttons)

    return InlineKeyboardMarkup(rows)


def admin_customer_quote_preview_keyboard(order_id: str) -> InlineKeyboardMarkup:
    """Allow one preliminary send and, later, one final send."""
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        row = conn.execute(
            """SELECT quote_sent_at, final_quote_sent_at,
                      customer_total_rub, delivery_rub
               FROM orders WHERE order_id = ?""",
            (order_id,),
        ).fetchone()

    rows = []
    if row:
        preliminary_sent, final_sent, product_total, delivery_total = row
        final_ready = product_total is not None and delivery_total is not None
        can_send = (
            (final_ready and not final_sent)
            or (not final_ready and not preliminary_sent)
        )
        if can_send:
            rows.append([
                InlineKeyboardButton(
                    "✅ Подтвердить отправку",
                    callback_data=f"admin:quotesend:{order_id}",
                )
            ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ К запросу",
            callback_data=f"admin:order:{order_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


def admin_status_keyboard(order_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🆕 Новый", callback_data=f"admin:setstatus:{order_id}:new"),
            InlineKeyboardButton("🟡 В работе", callback_data=f"admin:setstatus:{order_id}:working"),
        ],
        [
            InlineKeyboardButton("🔴 В ожидании", callback_data=f"admin:setstatus:{order_id}:waiting"),
            InlineKeyboardButton("🟢 Подтверждён", callback_data=f"admin:setstatus:{order_id}:confirmed"),
        ],
        [
            InlineKeyboardButton("📦 Выполняется", callback_data=f"admin:setstatus:{order_id}:executing"),
            InlineKeyboardButton("✅ Выполнен", callback_data=f"admin:setstatus:{order_id}:completed"),
        ],
        [
            InlineKeyboardButton("❌ Отменён", callback_data=f"admin:setstatus:{order_id}:cancelled"),
        ],
        [
            InlineKeyboardButton(
                "⬅️ К запросу",
                callback_data=f"admin:order:{order_id}",
            )
        ],
    ])


def get_admin_order_delivery(order_id: str):
    """Read delivery state for one order."""
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        return conn.execute(
            """
            SELECT
                order_id,
                delivery_tariff,
                actual_weight_kg,
                volume_weight_kg,
                delivery_calculated_rub,
                delivery_manual_rub,
                delivery_rub,
                delivery_pending
            FROM orders
            WHERE order_id = ?
            """,
            (order_id,),
        ).fetchone()


def format_admin_order_delivery(order_id: str):
    """Format delivery screen for one manager order."""
    row = get_admin_order_delivery(order_id)

    if not row:
        return None

    (
        saved_order_id,
        delivery_tariff,
        actual_weight_kg,
        volume_weight_kg,
        delivery_calculated_rub,
        delivery_manual_rub,
        delivery_rub,
        delivery_pending,
    ) = row

    tariff_labels = {
        "comfort": "🟢 Комфорт",
        "economy": "🔵 Эконом",
        "mix": "🔴 MIX",
    }

    lines = [
        "🚚 <b>Доставка запроса</b>",
        "",
        f"<b>Номер:</b> <code>{escape(str(saved_order_id))}</code>",
        "",
    ]

    if delivery_pending:
        lines.extend([
            "<b>Режим:</b> ❓ Рассчитать после получения",
            "",
            "Стоимость доставки сейчас не определена.",
        ])

    elif delivery_tariff:
        lines.append(
            f"<b>Тариф:</b> {tariff_labels.get(delivery_tariff, escape(str(delivery_tariff)))}"
        )

        if actual_weight_kg is not None:
            lines.extend([
                "",
                f"<b>Фактический вес:</b> {float(actual_weight_kg):g} кг",
            ])

            if delivery_tariff != "economy":
                volume_text = (
                    f"{float(volume_weight_kg):g} кг"
                    if volume_weight_kg is not None
                    else "—"
                )
                lines.append(
                    f"<b>Объёмный вес:</b> {volume_text}"
                )

        if delivery_calculated_rub is not None:
            lines.extend([
                "",
                (
                    f"🧮 <b>Расчётная доставка:</b> "
                    f"{format_rub_whole(delivery_calculated_rub)}"
                ),
            ])

        if delivery_manual_rub is not None:
            lines.append(
                (
                    f"✏️ <b>Установлено вручную:</b> "
                    f"{format_rub_whole(delivery_manual_rub)}"
                )
            )

        if delivery_rub is not None:
            lines.extend([
                "",
                (
                    f"🚚 <b>Стоимость доставки:</b> "
                    f"{format_rub_whole(delivery_rub)}"
                ),
            ])

        if delivery_calculated_rub is None:
            lines.extend([
                "",
                "Тариф выбран и сохранён.",
                "Выбери способ расчёта доставки.",
            ])

    else:
        lines.extend([
            "<b>Тариф:</b> —",
            "",
            "Выбери вариант доставки:",
        ])

    return "\n".join(lines)


def admin_order_delivery_keyboard(order_id: str, user_data) -> InlineKeyboardMarkup:
    row = get_admin_order_delivery(order_id)

    rows = [
        [
            InlineKeyboardButton(
                "🟢 Комфорт",
                callback_data=f"admin:setorderdelivery:{order_id}:comfort",
            )
        ],
        [
            InlineKeyboardButton(
                "🔵 Эконом",
                callback_data=f"admin:setorderdelivery:{order_id}:economy",
            )
        ],
        [
            InlineKeyboardButton(
                "🔴 Mix",
                callback_data=f"admin:setorderdelivery:{order_id}:mix",
            )
        ],
    ]

    if row:
        delivery_tariff = row[1]
        delivery_calculated_rub = row[4]
        delivery_pending = bool(row[7])

        if delivery_tariff and not delivery_pending:
            rows.append([
                InlineKeyboardButton(
                    "⚖️ Ввести фактический вес",
                    callback_data=f"admin:deliveryweight:{order_id}",
                )
            ])

            if delivery_tariff in {"comfort", "mix"}:
                rows.append([
                    InlineKeyboardButton(
                        "📦 Ввести объёмный вес",
                        callback_data=f"admin:deliveryvolume:{order_id}",
                    )
                ])

            if delivery_calculated_rub is not None:
                rows.append([
                    InlineKeyboardButton(
                        "✏️ Изменить стоимость доставки",
                        callback_data=f"admin:deliverymanual:{order_id}",
                    )
                ])

    token = new_admin_delivery_group_token(user_data, order_id)
    rows.append([InlineKeyboardButton(
        "📦 Грузы", callback_data=delivery_group_callback("view", token)
    )])

    rows.extend([
        [
            InlineKeyboardButton(
                "❓ Рассчитать после получения",
                callback_data=f"admin:setorderdelivery:{order_id}:pending",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К запросу",
                callback_data=f"admin:order:{order_id}",
            )
        ],
    ])

    return InlineKeyboardMarkup(rows)


def new_admin_delivery_group_token(
    user_data, order_id: str, selected_ids=None,
    measurement_group_id=None, measurement_stage=None,
    measurement_values=None,
) -> str:
    state = user_data.get("admin_delivery_group_flow")
    if state is None:
        state = {"nonce": secrets.token_hex(6), "seq": 0}
    if state["seq"] >= 0xFFFFFFFF:
        raise RuntimeError("Delivery-group screen token space exhausted")
    state["seq"] += 1
    token = f"{state['nonce']}{state['seq']:x}"
    state.update(
        token=token,
        order_id=order_id,
        selected_ids=selected_ids,
        measurement_group_id=measurement_group_id,
        measurement_stage=measurement_stage,
        measurement_values=measurement_values,
    )
    user_data["admin_delivery_group_flow"] = state
    return token


def delivery_group_callback(action: str, token: str, item_id=None) -> str:
    data = f"admin:dg:{action}:{token}"
    if item_id is not None:
        data += f":{item_id}"
    if len(data.encode("utf-8")) > 56:
        raise ValueError("Слишком длинные данные кнопки груза.")
    return data


def get_admin_delivery_groups(order_id: str):
    """Read the physical groups, their items, and unassigned items."""
    uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        order = conn.execute(
            "SELECT 1 FROM orders WHERE order_id = ?", (order_id,)
        ).fetchone()
        if order is None:
            return None
        groups = conn.execute(
            """
            SELECT g.id, g.group_number, g.tariff_code, g.status,
                   COUNT(gi.order_item_id)
            FROM delivery_groups AS g
            LEFT JOIN delivery_group_items AS gi ON gi.delivery_group_id = g.id
            WHERE g.order_id = ?
            GROUP BY g.id
            ORDER BY g.group_number
            """,
            (order_id,),
        ).fetchall()
        group_items = conn.execute(
            """
            SELECT g.group_number, i.position, i.oem
            FROM delivery_groups AS g
            JOIN delivery_group_items AS gi ON gi.delivery_group_id = g.id
            JOIN order_items AS i ON i.id = gi.order_item_id
            WHERE g.order_id = ? AND i.order_id = g.order_id
            ORDER BY g.group_number, i.position
            """,
            (order_id,),
        ).fetchall()
        unassigned = conn.execute(
            """
            SELECT i.id, i.position, i.oem, i.selected_delivery_tariff,
                   i.delivery_selected_at
            FROM order_items AS i
            WHERE i.order_id = ? AND NOT EXISTS (
                SELECT 1 FROM delivery_group_items AS gi
                WHERE gi.order_item_id = i.id
            )
            ORDER BY i.position
            """,
            (order_id,),
        ).fetchall()
    return groups, group_items, unassigned


def get_admin_delivery_group(order_id: str, group_id: int):
    """Read one group and its positions, scoped to the expected order."""
    uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as conn:
        group = conn.execute(
            """
            SELECT id, group_number, tariff_code, status,
                   actual_weight_kg, length_cm, width_cm, height_cm,
                   volume_weight_divisor, volume_weight_kg,
                   applied_calculation_type,
                   applied_base_rub_per_kg, applied_volume_rub_per_kg,
                   calculated_rub, manual_rub, final_rub
            FROM delivery_groups
            WHERE id = ? AND order_id = ?
            """,
            (group_id, order_id),
        ).fetchone()
        if group is None:
            return None
        items = conn.execute(
            """
            SELECT i.position, i.oem
            FROM delivery_group_items AS gi
            JOIN order_items AS i ON i.id = gi.order_item_id
            WHERE gi.delivery_group_id = ? AND i.order_id = ?
            ORDER BY i.position
            """,
            (group_id, order_id),
        ).fetchall()
    return group, items


def format_admin_delivery_group(
    order_id: str, group_id: int, token: str, preview=None, error=None
):
    snapshot = get_admin_delivery_group(order_id, group_id)
    if snapshot is None:
        return None
    group, items = snapshot
    status = group[3]
    status_label = (
        "Ожидает измерений" if status == "awaiting_measurements"
        else "Рассчитан" if status == "calculated"
        else str(status)
    )
    lines = [
        f"📦 <b>Груз №{group[1]} запроса №{escape(order_id)}</b>",
        f"Тариф: {escape(str(group[2]))}",
        f"Статус: {escape(status_label)}",
        "Позиции: " + ", ".join(
            f"{position}. {escape(str(oem or '—'))}"
            for position, oem in items
        ),
    ]
    if status == "calculated":
        lines.extend([
            f"Фактический вес: {group[4]:g} кг",
            f"Габариты Д×Ш×В: {group[5]:g}×{group[6]:g}×{group[7]:g} см",
            f"Применённый делитель: {group[8]:g}",
            f"Объёмный вес: {group[9]:g} кг",
            f"Применённый тип расчёта: {escape(str(group[10]))}",
            f"Применённые ставки: {group[11]:g} / {group[12]:g} ₽/кг",
            f"Расчётная стоимость: {format_rub_whole(group[13])}",
            f"Итог: {format_rub_whole(group[15])}",
        ])
    if preview is not None:
        actual, length, width, height, divisor, volume, calculated = preview
        lines.extend([
            "",
            "<b>Предварительный расчёт — ещё не сохранён</b>",
            f"Фактический вес: {actual:g} кг",
            f"Габариты Д×Ш×В: {length:g}×{width:g}×{height:g} см",
            f"Текущий делитель: {divisor:g}",
            f"Объёмный вес: {volume:g} кг",
            f"Стоимость: {format_rub_whole(calculated)}",
            "При сохранении ставки и делитель будут прочитаны заново.",
        ])
    if error:
        lines.append("⚠️ " + escape(str(error)))
    rows = []
    if status == "awaiting_measurements":
        if preview is None:
            rows.append([InlineKeyboardButton(
                "📐 Ввести измерения",
                callback_data=delivery_group_callback("m", token),
            )])
        else:
            rows.append([InlineKeyboardButton(
                "✅ Сохранить расчёт",
                callback_data=delivery_group_callback("c", token),
            )])
    rows.append([InlineKeyboardButton(
        "⬅️ К грузам", callback_data=delivery_group_callback("view", token),
    )])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def preview_admin_delivery_group(order_id: str, group_id: int, values):
    snapshot = get_admin_delivery_group(order_id, group_id)
    if snapshot is None or snapshot[0][3] != "awaiting_measurements":
        raise ValueError("Груз уже изменён или не найден.")
    tariff_code = snapshot[0][2]
    actual, length, width, height = (
        parse_positive_delivery_number(values[key])
        for key in ("actual", "length", "width", "height")
    )
    divisor = get_volume_weight_divisor()
    volume = rounded_group_volume_weight(length, width, height, divisor)
    calculated = calculate_admin_order_delivery(
        tariff_code, float(actual), volume
    )
    if calculated is None or not math.isfinite(calculated):
        raise ValueError("Тариф недоступен для расчёта.")
    return (
        float(actual), float(length), float(width), float(height),
        float(divisor), volume, calculated,
    )


def save_admin_delivery_group_measurements(
    order_id: str, group_id: int, values
):
    """Recheck and calculate one group inside a write transaction."""
    try:
        actual, length, width, height = (
            parse_positive_delivery_number(values[key])
            for key in ("actual", "length", "width", "height")
        )
    except (KeyError, ValueError) as exc:
        return False, str(exc)

    with closing(sqlite3.connect(ORDERS_DB_FILE)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            group = conn.execute(
                """
                SELECT tariff_code, status FROM delivery_groups
                WHERE id = ? AND order_id = ?
                """,
                (group_id, order_id),
            ).fetchone()
            if group is None:
                raise ValueError("Груз не найден в этом запросе.")
            tariff_code, status = group
            if status != "awaiting_measurements":
                raise ValueError("Груз уже рассчитан или изменён.")

            positions = conn.execute(
                """
                SELECT i.order_id, i.selected_delivery_tariff
                FROM delivery_group_items AS gi
                LEFT JOIN order_items AS i ON i.id = gi.order_item_id
                WHERE gi.delivery_group_id = ?
                """,
                (group_id,),
            ).fetchall()
            if not positions or any(
                item_order != order_id or selected != tariff_code
                for item_order, selected in positions
            ):
                raise ValueError("Состав или тариф позиций груза изменился.")

            tariff = conn.execute(
                """
                SELECT calculation_type, base_rub_per_kg,
                       volume_rub_per_kg, enabled
                FROM delivery_tariffs WHERE code = ?
                """,
                (tariff_code,),
            ).fetchone()
            if tariff is None or not tariff[3]:
                raise ValueError("Тариф груза отсутствует или отключён.")
            calculation_type, base, volume_rate, _enabled = tariff
            if calculation_type not in {
                "actual_only", "excess_volume", "all_volume"
            } or not all(
                math.isfinite(float(value))
                for value in (base, volume_rate)
            ):
                raise ValueError("Параметры тарифа недопустимы.")

            divisor = get_volume_weight_divisor(conn)
            volume = rounded_group_volume_weight(
                length, width, height, divisor
            )
            calculated = calculate_admin_order_delivery(
                tariff_code, float(actual), volume
            )
            if calculated is None or not math.isfinite(calculated):
                raise ValueError("Не удалось рассчитать доставку.")

            now = datetime.now().astimezone().isoformat(timespec="seconds")
            cursor = conn.execute(
                """
                UPDATE delivery_groups SET
                    actual_weight_kg = ?, length_cm = ?,
                    width_cm = ?, height_cm = ?,
                    volume_weight_divisor = ?, volume_weight_kg = ?,
                    applied_calculation_type = ?,
                    applied_base_rub_per_kg = ?,
                    applied_volume_rub_per_kg = ?,
                    calculated_rub = ?, manual_rub = NULL,
                    final_rub = ?, status = 'calculated',
                    updated_at = ?
                WHERE id = ? AND order_id = ?
                  AND status = 'awaiting_measurements'
                """,
                (
                    float(actual), float(length), float(width), float(height),
                    float(divisor), volume, calculation_type,
                    float(base), float(volume_rate), calculated, calculated,
                    now, group_id, order_id,
                ),
            )
            if cursor.rowcount != 1:
                raise ValueError("Груз уже изменён.")
            conn.commit()
            return True, calculated
        except ValueError as exc:
            conn.rollback()
            return False, str(exc)
        except Exception:
            conn.rollback()
            raise


def create_admin_delivery_group(order_id: str, item_ids):
    """Validate and attach whole order items in one write transaction."""
    ids = list(item_ids)
    if not ids or len(ids) != len(set(ids)) or any(type(i) is not int for i in ids):
        return False, "Выбери одну или несколько позиций запроса."

    with closing(sqlite3.connect(ORDERS_DB_FILE)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            if conn.execute(
                "SELECT 1 FROM orders WHERE order_id = ?", (order_id,)
            ).fetchone() is None:
                return False, "Запрос не найден."

            placeholders = ", ".join("?" for _ in ids)
            items = conn.execute(
                f"""SELECT id, order_id, position, selected_delivery_tariff,
                           delivery_selected_at
                    FROM order_items WHERE id IN ({placeholders})""",
                ids,
            ).fetchall()
            if len(items) != len(ids) or any(row[1] != order_id for row in items):
                return False, "Позиция не найдена в этом запросе."

            assigned = conn.execute(
                f"""SELECT 1 FROM delivery_group_items
                    WHERE order_item_id IN ({placeholders}) LIMIT 1""",
                ids,
            ).fetchone()
            if assigned:
                return False, "Одна из позиций уже входит в груз."

            if any(not row[3] or not row[4] for row in items):
                return False, "У каждой позиции должны быть выбраны тариф и время выбора."
            tariffs = {row[3] for row in items}
            if len(tariffs) != 1:
                details = ", ".join(
                    f"{row[2]} — {row[3]}" for row in sorted(items, key=lambda r: r[2])
                )
                return False, "Тарифы позиций различаются: " + details

            group_number = conn.execute(
                "SELECT COALESCE(MAX(group_number), 0) + 1 "
                "FROM delivery_groups WHERE order_id = ?", (order_id,)
            ).fetchone()[0]
            now = datetime.now().astimezone().isoformat(timespec="seconds")
            group = conn.execute(
                """INSERT INTO delivery_groups
                   (order_id, group_number, tariff_code, customer_selected_at,
                    status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'awaiting_measurements', ?, ?)""",
                (order_id, group_number, tariffs.pop(),
                 max(row[4] for row in items), now, now),
            )
            for item_id in ids:
                conn.execute(
                    "INSERT INTO delivery_group_items "
                    "(delivery_group_id, order_item_id) VALUES (?, ?)",
                    (group.lastrowid, item_id),
                )
            conn.commit()
            return True, group_number
        except Exception:
            conn.rollback()
            raise


def format_admin_delivery_groups(
    order_id: str, token: str, selected_ids=None, error=None
):
    snapshot = get_admin_delivery_groups(order_id)
    if snapshot is None:
        return None
    groups, group_items, unassigned = snapshot
    choosing = selected_ids is not None
    selected = set(selected_ids or ())
    lines = [f"📦 <b>Грузы запроса №{escape(order_id)}</b>"]
    for group_id, number, tariff, status, count in groups:
        lines.append(
            f"Груз №{number} — Тариф: {escape(str(tariff or '—'))}; "
            f"Позиций: {count}; Статус: "
            f"{escape('Ожидает измерений' if status == 'awaiting_measurements' else str(status))}"
        )
        positions = [
            f"{position}. {escape(str(oem or '—'))}"
            for group_number, position, oem in group_items
            if group_number == number
        ]
        if positions:
            lines.append("Позиции: " + ", ".join(positions))
    if not groups:
        lines.append("Грузов пока нет.")
    lines.append("\n<b>Позиции без груза:</b>")
    for item_id, position, oem, tariff, _ in unassigned:
        lines.append(
            f"{position}. {escape(str(oem or '—'))} — "
            f"{escape(str(tariff or 'тариф не выбран'))}"
        )
    if not unassigned:
        lines.append("Нет.")
    if error:
        lines.append("\n⚠️ " + escape(str(error)))
    rows = []
    if not choosing:
        for group_id, number, _tariff, _status, _count in groups:
            rows.append([InlineKeyboardButton(
                f"📦 Груз №{number}",
                callback_data=delivery_group_callback("o", token, group_id),
            )])
    if choosing:
        for item_id, position, _, tariff, _ in unassigned:
            rows.append([InlineKeyboardButton(
                f"{'✅ ' if item_id in selected else ''}{position}. "
                f"{tariff or 'без тарифа'}",
                callback_data=delivery_group_callback("toggle", token, item_id),
            )])
        rows.append([InlineKeyboardButton(
            "✅ Создать выбранный груз",
            callback_data=delivery_group_callback("save", token),
        )])
    elif unassigned:
        rows.append([InlineKeyboardButton(
            "➕ Создать груз", callback_data=delivery_group_callback("new", token),
        )])
    rows.append([InlineKeyboardButton(
        "⬅️ К доставке", callback_data=f"admin:orderdelivery:{order_id}",
    )])
    return "\n".join(lines), InlineKeyboardMarkup(rows)


def set_admin_order_delivery_mode(order_id: str, mode: str) -> bool:
    """Save selected delivery tariff or pending-delivery mode."""
    allowed = {"comfort", "economy", "mix", "pending"}

    if mode not in allowed:
        return False

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    if mode == "pending":
        delivery_tariff = None
        delivery_pending = 1
    else:
        delivery_tariff = mode
        delivery_pending = 0

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET
                delivery_tariff = ?,
                delivery_pending = ?,
                actual_weight_kg = NULL,
                volume_weight_kg = NULL,
                delivery_calculated_rub = NULL,
                delivery_manual_rub = NULL,
                delivery_rub = NULL,
                updated_at = ?
            WHERE order_id = ?
            """,
            (
                delivery_tariff,
                delivery_pending,
                updated_at,
                order_id,
            ),
        )
        conn.commit()

    return cursor.rowcount == 1


def calculate_admin_order_delivery(
    tariff_code: str,
    actual_weight_kg: float,
    volume_weight_kg=None,
):
    """Calculate delivery using current tariff values from SQLite."""
    tariff = get_delivery_tariff(tariff_code)

    if not tariff:
        return None

    (
        code,
        name,
        base_rub_per_kg,
        volume_rub_per_kg,
        calculation_type,
        enabled,
    ) = tariff

    if not enabled:
        return None

    actual = float(actual_weight_kg)
    volume = (
        float(volume_weight_kg)
        if volume_weight_kg is not None
        else 0.0
    )

    if actual < 0 or volume < 0:
        return None

    base = float(base_rub_per_kg)
    volume_rate = float(volume_rub_per_kg)

    if calculation_type == "actual_only":
        total = actual * base

    elif calculation_type == "excess_volume":
        excess_volume = max(volume - actual, 0.0)
        total = actual * base + excess_volume * volume_rate

    elif calculation_type == "all_volume":
        total = actual * base + volume * volume_rate

    else:
        return None

    return float(total)


def save_admin_order_delivery_weights(
    order_id: str,
    actual_weight_kg: float,
    volume_weight_kg=None,
) -> bool:
    """Save weights, calculate delivery and store automatic result."""
    row = get_admin_order_delivery(order_id)

    if not row:
        return False

    tariff_code = row[1]
    delivery_pending = row[7]

    if not tariff_code or delivery_pending:
        return False

    calculated = calculate_admin_order_delivery(
        tariff_code,
        actual_weight_kg,
        volume_weight_kg,
    )

    if calculated is None:
        return False

    actual = float(actual_weight_kg)
    volume = (
        float(volume_weight_kg)
        if volume_weight_kg is not None
        else None
    )

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET
                actual_weight_kg = ?,
                volume_weight_kg = ?,
                delivery_calculated_rub = ?,
                delivery_manual_rub = NULL,
                delivery_rub = ?,
                delivery_pending = 0,
                updated_at = ?
            WHERE order_id = ?
            """,
            (
                actual,
                volume,
                calculated,
                calculated,
                updated_at,
                order_id,
            ),
        )
        conn.commit()

    return cursor.rowcount == 1


def set_admin_order_delivery_manual(
    order_id: str,
    amount_rub: float,
) -> bool:
    """Override applied delivery price while keeping automatic calculation."""
    if amount_rub < 0:
        return False

    row = get_admin_order_delivery(order_id)

    if not row or not row[1] or row[7]:
        return False

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET
                delivery_manual_rub = ?,
                delivery_rub = ?,
                updated_at = ?
            WHERE order_id = ?
            """,
            (
                float(amount_rub),
                float(amount_rub),
                updated_at,
                order_id,
            ),
        )
        conn.commit()

    return cursor.rowcount == 1


def admin_order_delivery_selected_keyboard(
    order_id: str,
    tariff_code: str,
) -> InlineKeyboardMarkup:
    """Keyboard after a tariff has been selected."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "⚖️ Вес известен",
                callback_data=f"admin:deliveryweight:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "❓ Рассчитать после получения",
                callback_data=f"admin:setorderdelivery:{order_id}:pending",
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Изменить тариф",
                callback_data=f"admin:orderdelivery:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К запросу",
                callback_data=f"admin:order:{order_id}",
            )
        ],
    ])


def admin_order_delivery_result_keyboard(
    order_id: str,
) -> InlineKeyboardMarkup:
    """Keyboard after automatic delivery calculation."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✏️ Изменить стоимость вручную",
                callback_data=f"admin:deliverymanual:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⚖️ Изменить вес",
                callback_data=f"admin:deliveryweight:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Изменить тариф",
                callback_data=f"admin:orderdelivery:{order_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К запросу",
                callback_data=f"admin:order:{order_id}",
            )
        ],
    ])


def set_admin_customer_total(order_id: str, amount_rub: float) -> bool:
    """Save manager-entered customer total in RUB."""
    if amount_rub < 0:
        return False

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET customer_total_rub = ?,
                auto_pricing_status = 'manual_override',
                updated_at = ?
            WHERE order_id = ?
            """,
            (float(amount_rub), updated_at, order_id),
        )
        conn.commit()

    return cursor.rowcount == 1


def set_admin_order_status(
    order_id: str,
    status: str,
    expected_current: str | None = None,
) -> bool:
    allowed_statuses = {
        "new",
        "working",
        "waiting",
        "confirmed",
        "executing",
        "completed",
        "cancelled",
    }

    if status not in allowed_statuses:
        return False

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        row = conn.execute(
            "SELECT status FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
    if row is None:
        return False

    current_status = str(row[0] or "")
    if expected_current is not None and current_status != expected_current:
        return False

    # Warehouse safety gate: execution requires full reservation coverage.
    if status == "executing":
        reservations = stock_engine.list_order_reservations(order_id, db_file=ORDERS_DB_FILE)
        covered = {}
        for reservation in reservations:
            if reservation.get("status") not in {"hold", "reserved", "committed"}:
                continue
            item_id = reservation.get("order_item_id")
            if item_id is None:
                continue
            covered[int(item_id)] = covered.get(int(item_id), 0.0) + float(
                reservation.get("quantity") or reservation.get("qty") or 0
            )
        with sqlite3.connect(ORDERS_DB_FILE) as conn:
            # Only warehouse offers require reservation coverage. USA positions
            # deliberately have no warehouse/reservation and must not block
            # confirmed -> executing.
            required_items = conn.execute(
                """SELECT id, quantity
                   FROM order_items
                   WHERE order_id = ?
                     AND LOWER(COALESCE(offer_source, 'usa')) = 'warehouse'""",
                (order_id,),
            ).fetchall()
        if any(
            covered.get(int(item_id), 0.0) < float(quantity or 0)
            for item_id, quantity in required_items
        ):
            log.warning("Warehouse execution gate blocked: order=%s", order_id)
            return False

    # A fully reserved order transfers its existing reserves to the warehouse.
    if status in {"executing", "completed"}:
        sync = stock_engine.sync_order_reservations_for_status(
            order_id,
            status,
            db_file=ORDERS_DB_FILE,
        )
        if sync.get("failed"):
            log.warning(
                "Warehouse/order status sync failed: order=%s status=%s sync=%s",
                order_id,
                status,
                sync,
            )
            return False

    updated_at = datetime.now().astimezone().isoformat(timespec="seconds")

    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        cursor = conn.execute(
            """
            UPDATE orders
            SET status = ?, updated_at = ?
            WHERE order_id = ? AND status = ?
            """,
            (status, updated_at, order_id, current_status),
        )
        conn.commit()

    if cursor.rowcount != 1:
        return False

    # Confirmed never auto-selects a warehouse. It only performs reservation
    # housekeeping. Cancellation releases every active/absorbed assignment;
    # absorbed stock is not added back artificially.
    if status in {"confirmed", "cancelled"}:
        stock_engine.sync_order_reservations_for_status(
            order_id,
            status,
            db_file=ORDERS_DB_FILE,
        )

    return True


def prepare_customer_delivery_choice_readonly(order_id: str, telegram_user_id: int):
    """Prepare the initial screen from saved types without database writes."""
    from contextlib import closing

    db_uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(db_uri, uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        owner = conn.execute(
            "SELECT telegram_user_id FROM orders WHERE order_id = ?",
            (order_id,),
        ).fetchone()
        if owner is None or int(owner[0]) != int(telegram_user_id):
            raise ValueError(
                "Запрос не найден или принадлежит другому пользователю."
            )
        owner_user_id = int(owner[0])
        items = conn.execute(
            """
            SELECT id, position, manufacturer, oem, name, quantity,
                   item_type, selected_delivery_tariff
            FROM order_items WHERE order_id = ? ORDER BY position
            """,
            (order_id,),
        ).fetchall()

    if not items:
        raise ValueError("В запросе нет позиций.")

    lines = [
        "🚚 <b>Выбор способа доставки</b>",
        f"<b>Номер запроса:</b> <code>{escape(order_id)}</code>",
        "Выбери доставку отдельно для каждой позиции запроса.",
        "",
    ]
    rows = []
    for item in items:
        item_type = str(item["item_type"] or "")
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ())
        selected = str(item["selected_delivery_tariff"] or "")
        info = ITEM_TYPE_INFO.get(item_type)
        lines.extend([
            f"<b>{item['position']}. {escape(str(item['manufacturer'] or '—'))}</b>",
            f"OEM: <code>{escape(str(item['oem'] or '—'))}</code>",
            f"Название: {escape(str(item['name'] or '—'))}",
            f"Количество: <b>{item['quantity']} шт.</b>",
        ])
        if allowed:
            lines.append(f"<b>Тип:</b> {info['label'] if info else escape(item_type)}")
            if selected in allowed:
                lines.append(f"<b>Выбрано:</b> ✅ {CUSTOMER_TARIFF_LABELS[selected]}")
            rows.append([
                InlineKeyboardButton(
                    f"{item['position']}. "
                    + ("✅ " if selected == code else "")
                    + CUSTOMER_TARIFF_LABELS[code],
                    callback_data=f"deliverychoice:{order_id}:{item['id']}:{code}",
                )
                for code in allowed
            ])
        else:
            lines.append(
                "Тип этой позиции запроса ещё определяется менеджером. "
                "Выбор доставки станет доступен после определения типа."
            )
        lines.append("")

    lines.append(
        "Чтобы получить актуальный экран, повторно нажми "
        "«🚚 Выбрать доставку» в подтверждении запроса."
    )
    return owner_user_id, "\n".join(lines), InlineKeyboardMarkup(rows)


def _restore_web_handoff_cart(token: str) -> dict | None:
    payload = web_handoff.get_handoff(token, ORDERS_DB_FILE)
    if not payload:
        return None

    cart = {}
    for item in payload.get("items") or []:
        manufacturer = str(item.get("manufacturer") or "")
        oem = str(item.get("oem") or "")
        source = str(item.get("offer_source") or "usa").strip().lower()
        result = public_msrp_cache_result(manufacturer, oem) if manufacturer else None

        if source == "usa":
            if not result or str(result.get("status") or "").upper() != "FOUND":
                continue
            current_oem = str(result.get("item_sku") or result.get("oem") or oem)
        else:
            source = "warehouse"
            current_oem = oem

        warehouse_id = item.get("warehouse_id")
        warehouse_public_name = None
        price_snapshot_rub = None
        available_snapshot = None

        if source == "warehouse":
            try:
                warehouse_id = int(warehouse_id)
            except (TypeError, ValueError):
                continue
            current_offer = next(
                (
                    x for x in warehouse_stock_service.client_stock_summary(
                        current_oem, db_file=ORDERS_DB_FILE
                    )
                    if int(x.get("warehouse_id") or 0) == warehouse_id
                    and x.get("is_fresh")
                ),
                None,
            )
            if not current_offer:
                continue
            available_snapshot = current_offer.get("available_quantity")
            price_snapshot_rub = current_offer.get("price_rub")
            warehouse_public_name = current_offer.get("public_name")
            qty = int(item.get("qty") or 1)
            if (
                available_snapshot is None
                or float(available_snapshot) < qty
                or price_snapshot_rub is None
                or float(price_snapshot_rub) <= 0
            ):
                continue
            key = f"{manufacturer}|{current_oem}|warehouse:{warehouse_id}"
        else:
            source = "usa"
            warehouse_id = None
            key = f"{manufacturer}|{current_oem}|usa"

        dp = get_dealer_price_cache(manufacturer, current_oem, mark_used=True)
        if source == "usa" and not (dp and dp.get("fresh")):
            continue

        cart[key] = {
            "manufacturer": manufacturer,
            "oem": current_oem,
            "requested_oem": str(item.get("requested_oem") or oem),
            "name": result.get("name") if result else None,
            "price": result.get("price") if result else None,
            "catalog": result.get("catalog") if result else None,
            "offer_source": source,
            "warehouse_id": warehouse_id,
            "warehouse_public_name": warehouse_public_name,
            "price_snapshot_rub": (
                float(price_snapshot_rub) if price_snapshot_rub is not None else None
            ),
            "available_snapshot": available_snapshot,
            "_dealer_price_usd": (
                dp.get("dealer_price_usd") if dp and dp.get("fresh") else None
            ),
            "_dealer_price_source": (
                "dp_cache:" + str(dp.get("source") or "verified_live_dp")
                if dp and dp.get("fresh") else None
            ),
            "_dealer_price_checked_at": (
                dp.get("last_verified_at") if dp and dp.get("fresh") else None
            ),
            "_dealer_price_status": (
                "CACHE_FALLBACK" if dp and dp.get("fresh") else "CACHE_MISS"
            ),
            "_origin": "web",
            "_web_handoff_token": token,
            "qty": int(item.get("qty") or 1),
        }

    if not cart or not web_handoff.mark_handoff_used(token, ORDERS_DB_FILE):
        return None
    return cart


def _find_client_warehouse_offer(oem: str, warehouse_id: int) -> dict | None:
    for offer in warehouse_stock_service.client_stock_summary(
        str(oem or ""),
        db_file=ORDERS_DB_FILE,
    ):
        if int(offer.get("warehouse_id") or 0) == int(warehouse_id):
            return offer
    return None


async def _revalidate_warehouse_cart_item(item: dict) -> dict:
    """Refresh a doubtful warehouse snapshot once before checkout rejection."""
    oem = str(item.get("oem") or "").strip()
    warehouse_id = int(item.get("warehouse_id") or 0)
    qty = int(item.get("qty", 1))
    old_price = item.get("price_snapshot_rub")

    current_offer = _find_client_warehouse_offer(oem, warehouse_id)
    needs_refresh = (
        current_offer is None
        or not current_offer.get("is_fresh")
        or current_offer.get("available_quantity") is None
        or current_offer.get("price_rub") is None
    )

    refreshed = False
    if needs_refresh and warehouse_id and oem:
        refreshed = True
        result = await asyncio.to_thread(
            warehouse_stock_service.refresh_warehouse_oem,
            warehouse_id,
            oem,
            ORDERS_DB_FILE,
        )
        if getattr(result, "status", None) == "check_failed":
            log.info(
                "Checkout warehouse refresh failed: warehouse=%s oem=%s details=%s",
                warehouse_id,
                oem,
                getattr(result, "details", None),
            )
        current_offer = _find_client_warehouse_offer(oem, warehouse_id)

    if (
        current_offer is None
        or not current_offer.get("is_fresh")
        or current_offer.get("available_quantity") is None
        or current_offer.get("price_rub") is None
        or old_price is None
    ):
        return {
            "status": "unverified",
            "refreshed": refreshed,
            "offer": current_offer,
        }

    current_available = float(current_offer["available_quantity"])
    current_price = float(current_offer["price_rub"])
    if current_available < qty or current_price != float(old_price):
        return {
            "status": "changed",
            "refreshed": refreshed,
            "offer": current_offer,
        }

    item["available_snapshot"] = current_available
    item["warehouse_public_name"] = (
        current_offer.get("public_name")
        or item.get("warehouse_public_name")
    )
    return {
        "status": "ok",
        "refreshed": refreshed,
        "offer": current_offer,
    }


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("manufacturer", None)
    _activate_group_session(update, context)

    user = update.effective_user

    if (
        context.args
        and len(context.args) == 1
        and context.args[0].startswith("web_")
        and not _is_group_chat(update)
    ):
        token = context.args[0][4:]
        cart = _restore_web_handoff_cart(token)
        if cart:
            context.user_data["cart"] = cart
            if user:
                web_handoff.attach_handoff_user(
                    token, int(user.id), ORDERS_DB_FILE
                )
            await safe_reply_text(
                update.effective_message,
                "🌐 <b>Корзина с сайта загружена</b>\n\n" + format_cart(cart),
                parse_mode=ParseMode.HTML,
                reply_markup=cart_keyboard(cart),
            )
        else:
            await safe_reply_text(
                update.effective_message,
                "Ссылка WEB-корзины уже использована, устарела или недоступна. "
                "Вернись на сайт и нажми «Продолжить в Telegram» ещё раз.",
            )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and not _is_group_chat(update)
    ):
        await safe_reply_text(
            update.effective_message,
            "⚙️ <b>Админка Extremizer_bot</b>\n\n"
            "Выбери раздел:",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_main_keyboard(),
        )
        return

    group_note = (
        "\n\n💬 <b>В группе я не вмешиваюсь в обычную переписку.</b> "
        "Для запроса используй /start, упомяни меня через @Extremizer_bot или ответь на моё сообщение.\n"
        if _is_group_chat(update) else ""
    )

    await safe_reply_text(update.effective_message, 
        "👋 <b>Extremizer_bot</b>\n\n"
        "Помогу сориентироваться в ценах."
        + group_note + "\n\n"
        "<b>Можно отправить запрос двумя способами:</b>\n\n"
        "<b>1. Один производитель</b>\n"
        "Выбери производителя кнопкой ниже и отправь один или несколько OEM-номеров.\n\n"
        "<b>2. Разные производители</b>\n"
        "Укажи производителя перед каждым номером, например:\n"
        "<code>Ski-Doo 417224332\nArctic Cat 0746-933</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=manufacturer_keyboard(),
    )


async def manufacturer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""
    if not data.startswith("admin:dg:"):
        await query.answer()
    _activate_group_session(update, context)

    if data.startswith("shortageusa:"):
        parts=data.split(":")
        if len(parts)!=3: await query.answer("Некорректное решение.",show_alert=True); return
        _,action,sid_text=parts
        try:
            sid=int(sid_text); x=supplier_shortages.get(ORDERS_DB_FILE,sid)
            owner=supplier_customer_notify.client_chat_id(ORDERS_DB_FILE,x["client_order_id"])
            if not update.effective_user or owner!=update.effective_user.id:
                await query.answer("Это решение относится к другому заказу.",show_alert=True); return
            x=supplier_shortages.decide_usa_quote(ORDERS_DB_FILE,sid,action=="accept")
        except Exception:
            log.exception("Could not apply USA shortage quote decision"); await query.answer("Не удалось сохранить решение.",show_alert=True); return
        await query.answer(); await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text("✅ Заказ недостающего количества из США подтверждён." if action=="accept" else "❌ Заказ недостающего количества из США отклонён.")
        return

    if data.startswith("shortage:"):
        parts=data.split(":")
        if len(parts)!=3:
            await query.answer("Некорректное решение.",show_alert=True); return
        _,decision,sid_text=parts
        try:
            sid=int(sid_text)
            from supplier_order_service import SupplierOrderService
            svc=SupplierOrderService(ORDERS_DB_FILE)
            x=supplier_shortages.get(ORDERS_DB_FILE,sid)
            owner=supplier_customer_notify.client_chat_id(ORDERS_DB_FILE,x["client_order_id"])
            if not update.effective_user or owner!=update.effective_user.id:
                await query.answer("Это решение относится к другому заказу.",show_alert=True); return
            x=svc.decide_shortage(sid,decision)
        except Exception:
            log.exception("Could not apply shortage decision")
            await query.answer("Не удалось сохранить решение. Попробуй позже.",show_alert=True); return
        if decision=="partial_refund":
            msg=f"✅ Решение сохранено. Отгружаем {_short_q(x['confirmed_qty'])} шт.; возврат за {_short_q(x['shortage_qty'])} шт. зафиксирован."
        elif decision=="usa_quote":
            msg=f"↗️ Решение сохранено. Отгружаем {_short_q(x['confirmed_qty'])} шт.; запрашиваем цену из США на {_short_q(x['shortage_qty'])} шт."
            # Reuse the verified DP architecture: fresh production cache first,
            # then the signed live-DP bridge. MSRP is never substituted for DP.
            with sqlite3.connect(ORDERS_DB_FILE) as _c:
                _row=_c.execute("""SELECT soi.manufacturer FROM supplier_order_items soi
                                  JOIN supplier_shortages ss ON ss.supplier_order_item_id=soi.id
                                  WHERE ss.id=?""",(sid,)).fetchone()
            _m=str(_row[0] or "").strip() if _row else ""
            _cache=get_dealer_price_cache(_m,x["oem"],mark_used=True) if _m else None
            _dp=float(_cache["dealer_price_usd"]) if _cache and _cache.get("fresh") else None
            _source=("dp_cache:"+str(_cache.get("source") or "verified_live_dp")) if _dp is not None else None
            if _dp is None and _m:
                _live=await asyncio.to_thread(dp_live_bridge.request_live_dp,ORDERS_DB_FILE,_m,x["oem"],wait_seconds=10.0)
                if str(_live.get("status") or "").upper()=="FOUND" and _live.get("dealer_price_usd") is not None:
                    _dp=float(_live["dealer_price_usd"]); _source=str(_live.get("source") or "verified_live_dp")
                    upsert_dealer_price_cache(_m,x["oem"],_dp,_source)
            if _dp is not None:
                _unit=customer_rub_price_from_dp(_dp,coefficient=load_price_coefficient(),rate=load_usd_rub_rate())
                if _unit is not None:
                    _total=int(_unit*float(x["shortage_qty"]))
                    x=supplier_shortages.set_usa_quote(ORDERS_DB_FILE,sid,status="quoted",unit_price_rub=_unit,total_price_rub=_total,dp_usd=_dp,source=_source)
                    await query.answer()
                    await query.edit_message_reply_markup(reply_markup=None)
                    await query.message.reply_text(msg)
                    await query.message.reply_text(
                        f"🇺🇸 Цена из США для {x['oem']}: <b>{_total:,} ₽</b> за {_short_q(x['shortage_qty'])} шт.\n\n"
                        "Стоимость доставки из США не входит в стоимость товаров и оплачивается отдельно.",
                        parse_mode=ParseMode.HTML,
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Заказать по этой цене",callback_data=f"shortageusa:accept:{sid}")],[InlineKeyboardButton("❌ Не заказывать",callback_data=f"shortageusa:decline:{sid}")]])
                    ); return
            x=supplier_shortages.set_usa_quote(ORDERS_DB_FILE,sid,status="price_pending")
            msg+="\n\nЦена пока не подтверждена. Запрос сохранён; MSRP вместо DP не используется."
        else:
            msg=f"❌ Решение сохранено. Отказ от {_short_q(x['ordered_qty'])} шт. зафиксирован."
        await query.answer()
        await query.edit_message_reply_markup(reply_markup=None)
        await query.message.reply_text(msg)
        return

    if data.startswith("deliveryopen:"):
        from telegram.error import TelegramError

        async def open_notice(text: str) -> None:
            try:
                if query.message is not None:
                    await query.message.reply_text(text)
                else:
                    await query.answer(text, show_alert=True)
            except TelegramError:
                log.exception("Could not send delivery-screen notice")

        parts = data.split(":")
        if len(parts) != 2 or not parts[1]:
            await open_notice("Некорректная команда выбора доставки.")
            return
        user = update.effective_user
        if user is None:
            await open_notice("Не удалось определить пользователя.")
            return

        try:
            owner_user_id, choice_text, keyboard = (
                prepare_customer_delivery_choice_readonly(parts[1], user.id)
            )
        except ValueError as exc:
            await open_notice(str(exc))
            return
        except Exception:
            log.exception("Could not prepare initial delivery screen")
            await open_notice(
                "Не удалось подготовить экран доставки. "
                "Запрос сохранён. Попробуй позже."
            )
            return

        try:
            await context.bot.send_message(
                chat_id=owner_user_id,
                text=choice_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except TelegramError:
            log.exception("Could not send initial delivery screen")
            await open_notice(
                "Не удалось подтвердить отправку экрана доставки в личный чат. "
                "Запрос сохранён, выбор не изменён. "
                "Открой личный чат с ботом, нажми /start "
                "и повтори «🚚 Выбрать доставку»."
            )
            return
        if _is_group_chat(update):
            await open_notice("Экран доставки отправлен в личный чат.")
        return

    if data.startswith("deliverychoice:"):
        from contextlib import closing
        from telegram.error import BadRequest, TelegramError

        async def delivery_notice(text: str) -> None:
            try:
                if query.message is not None:
                    await query.message.reply_text(text)
                else:
                    await query.answer(text, show_alert=True)
            except TelegramError:
                log.exception("Could not send delivery-choice notice")

        parts = data.split(":")
        if len(parts) != 4:
            await delivery_notice("Некорректные данные кнопки доставки.")
            return

        _, order_id, item_id_text, tariff_code = parts
        tariff_code = tariff_code.strip().lower()

        try:
            order_item_id = int(item_id_text)
        except (TypeError, ValueError):
            await delivery_notice("Некорректная позиция запроса.")
            return

        if not order_id or order_item_id <= 0 or not tariff_code:
            await delivery_notice("Некорректные данные кнопки доставки.")
            return

        user = update.effective_user
        if user is None:
            await delivery_notice("Не удалось определить пользователя.")
            return

        # Проверяем владельца, позицию и текущий выбор без права записи.
        # Снимок остальных позиций нужен для обновления экрана при повторе.
        try:
            db_uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
            with closing(sqlite3.connect(db_uri, uri=True)) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("BEGIN")

                owner = conn.execute(
                    """
                    SELECT telegram_user_id
                    FROM orders
                    WHERE order_id = ?
                    """,
                    (order_id,),
                ).fetchone()

                owned = (
                    owner is not None
                    and int(owner["telegram_user_id"]) == user.id
                )

                item = None
                snapshot = []

                if owned:
                    item = conn.execute(
                        """
                        SELECT id, selected_delivery_tariff
                        FROM order_items
                        WHERE id = ? AND order_id = ?
                        """,
                        (order_item_id, order_id),
                    ).fetchone()

                    if (
                        item is not None
                        and item["selected_delivery_tariff"] == tariff_code
                    ):
                        snapshot = conn.execute(
                            """
                            SELECT
                                id, position, manufacturer, oem, name,
                                quantity, item_type, selected_delivery_tariff
                            FROM order_items
                            WHERE order_id = ?
                            ORDER BY position
                            """,
                            (order_id,),
                        ).fetchall()

        except (sqlite3.Error, OSError, ValueError):
            log.exception("Delivery-choice read-only check failed")
            await delivery_notice(
                "Не удалось проверить запрос. Попробуй позже."
            )
            return

        if not owned:
            await delivery_notice(
                "Запрос не найден или принадлежит другому пользователю."
            )
            return

        if item is None:
            await delivery_notice("Эта позиция не принадлежит запросу.")
            return

        same_choice = item["selected_delivery_tariff"] == tariff_code

        if not same_choice:
            # Проверяет тип товара, допустимость, наличие и активность
            # нового тарифа; сохраняет выбор существующим способом.
            try:
                success, message = set_customer_item_delivery_tariff(
                    order_id,
                    order_item_id,
                    user.id,
                    tariff_code,
                )
            except sqlite3.Error:
                log.exception("Delivery-choice database operation failed")
                await delivery_notice(
                    "Не удалось подтвердить сохранение способа доставки. "
                    "Попробуй позже."
                )
                return

            if not success:
                await delivery_notice(message)
                return

        try:
            if same_choice:
                # Не вызываем существующие функции формирования экрана:
                # через get_customer_delivery_items() они могут менять БД.
                # Используем исключительно прочитанный выше снимок.
                lines = [
                    "🚚 <b>Выбор способа доставки</b>",
                    "",
                    f"<b>Запрос:</b> <code>{escape(order_id)}</code>",
                    "",
                    "Способ доставки необходимо выбрать "
                    "<b>до отправки позиций запроса из США</b>.",
                    "",
                ]
                button_rows = []
                complete = bool(snapshot)

                for row in snapshot:
                    item_type = str(row["item_type"] or "")
                    selected = str(row["selected_delivery_tariff"] or "")
                    allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ())
                    info = ITEM_TYPE_INFO.get(item_type)
                    type_label = (
                        info["label"]
                        if info
                        else "⚠️ Тип товара не определён"
                    )
                    valid_selection = selected in allowed
                    complete = complete and valid_selection

                    lines.extend([
                        f"<b>{row['position']}. "
                        f"{escape(str(row['manufacturer'] or '—'))}</b>",
                        f"OEM: <code>{escape(str(row['oem'] or '—'))}</code>",
                        f"Название: {escape(str(row['name'] or '—'))}",
                        f"Количество: <b>{row['quantity']} шт.</b>",
                        f"<b>Тип:</b> {type_label}",
                    ])

                    if valid_selection:
                        lines.append(
                            "<b>Выбрано:</b> ✅ "
                            + CUSTOMER_TARIFF_LABELS[selected]
                        )
                    elif allowed:
                        lines.extend([
                            "<b>Доступно:</b> "
                            + " / ".join(
                                CUSTOMER_TARIFF_LABELS[code]
                                for code in allowed
                            ),
                            "<b>Выбери способ доставки этой позиции.</b>",
                        ])
                    else:
                        lines.append("⚠️ Способ доставки пока выбрать нельзя.")

                    lines.append("")

                    if allowed:
                        button_rows.append([
                            InlineKeyboardButton(
                                f"{row['position']}. "
                                + ("✅ " if selected == code else "")
                                + CUSTOMER_TARIFF_LABELS[code],
                                callback_data=(
                                    f"deliverychoice:{order_id}:"
                                    f"{row['id']}:{code}"
                                ),
                            )
                            for code in allowed
                        ])

                if complete:
                    lines.extend([
                        "✅ <b>Способ доставки выбран для всех позиций.</b>",
                        "",
                        "Выбранный способ доставки будет использован "
                        "для расчёта стоимости доставки после прихода "
                        "груза в Москву.",
                    ])
                else:
                    lines.append("Выбери способ доставки для каждой позиции.")

                choice_text = "\n".join(lines)
                keyboard = InlineKeyboardMarkup(button_rows)
            else:
                _owner_user_id, choice_text, keyboard = (
                    prepare_customer_delivery_choice_readonly(
                        order_id, user.id
                    )
                )

        except Exception:
            log.exception("Delivery choice exists, but view preparation failed")
            await delivery_notice(
                "Выбор доставки сохранён, но не удалось подготовить экран."
            )
            return

        try:
            await query.edit_message_text(
                choice_text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return

            log.exception("Delivery choice exists, but Telegram edit failed")
            await delivery_notice(
                "Выбор доставки сохранён, но сообщение не обновилось. "
                "Повтори нажатие позже, чтобы обновить экран."
            )
        except TelegramError:
            log.exception("Delivery choice exists, but Telegram is unavailable")
            await delivery_notice(
                "Выбор доставки сохранён, но сообщение не обновилось. "
                "Повтори нажатие позже, чтобы обновить экран."
            )

        return

    if data.startswith("admin:"):
        user = update.effective_user

        if not user or user.id != RATE_ADMIN_USER_ID:
            await query.answer("Недоступно.", show_alert=True)
            return

        if data == "admin:suppliers" or data.startswith("admin:supplier:") or data.startswith("admin:supplierstatus:") or data.startswith("admin:suppliertrack:") or data.startswith("admin:supplierbatch:") or data.startswith("admin:supplierproblem:"):
            try:
                await supplier_telegram_handlers.handle_callback(update, context, supplier_order_service, data)
            except (ValueError, KeyError, PermissionError) as exc:
                await query.answer(str(exc), show_alert=True)
            return

        if (
            data == "admin:warehouses"
            or data.startswith("admin:warehouse:")
            or data.startswith("admin:stock")
        ):
            await warehouse_admin.handle_callback(update, context, data)
            return

        if data == "admin:home":
            warehouse_admin.clear_states(context)
            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_search_payload", None)
            context.user_data.pop("admin_search_page", None)
            context.user_data.pop("admin_search_last_mode", None)
            context.user_data.pop("admin_delivery_edit", None)
            context.user_data.pop("admin_delivery_setting_edit", None)
            context.user_data.pop("admin_delivery_divisor_edit", None)
            context.user_data.pop("admin_customer_total_order_id", None)

            await query.edit_message_text(
                "⚙️ <b>Админка Extremizer_bot</b>\n\n"
                "Выбери раздел:",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_main_keyboard(),
            )
            return

        if data == "admin:delivery":
            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_customer_total_order_id", None)
            context.user_data.pop("admin_delivery_edit", None)
            context.user_data.pop("admin_delivery_setting_edit", None)
            context.user_data.pop("admin_delivery_divisor_edit", None)

            try:
                delivery_text = format_admin_delivery_settings()
            except Exception:
                log.exception("Failed to load delivery settings")
                await query.answer(
                    "Не удалось загрузить тарифы доставки.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                delivery_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_delivery_keyboard(),
                disable_web_page_preview=True,
            )
            return

        if data == "admin:deliveryterms":
            context.user_data.pop("admin_delivery_edit", None)
            context.user_data.pop("admin_delivery_setting_edit", None)
            context.user_data.pop("admin_delivery_divisor_edit", None)
            await query.edit_message_text(
                format_admin_delivery_terms(),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_delivery_terms_keyboard(),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:deliverysetting:"):
            alias = data.rsplit(":", 1)[1].strip()
            setting_key = DELIVERY_TEXT_SETTING_ALIASES.get(alias)
            if setting_key is None:
                await query.answer("Неизвестная настройка.", show_alert=True)
                return

            context.user_data.pop("admin_delivery_edit", None)
            context.user_data.pop("admin_delivery_divisor_edit", None)
            context.user_data["admin_delivery_setting_edit"] = setting_key
            current = get_delivery_text_settings()[setting_key]
            label = DELIVERY_TEXT_SETTING_LABELS[setting_key]
            await query.edit_message_text(
                f"{escape(label)}\n\n"
                f"Текущее значение: <b>{escape(current)}</b>\n\n"
                "Отправь новое значение одним сообщением.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬅️ К срокам и условиям",
                        callback_data="admin:deliveryterms",
                    )
                ]]),
                disable_web_page_preview=True,
            )
            return

        if data == "admin:deliverydivisor":
            context.user_data.pop("admin_delivery_edit", None)
            context.user_data.pop("admin_delivery_setting_edit", None)
            context.user_data["admin_delivery_divisor_edit"] = True
            await query.edit_message_text(
                "📐 <b>Введи новый делитель объёмного веса.</b>\n"
                "Допустимо только конечное число больше нуля.\n"
                f"Текущее значение: {get_volume_weight_divisor():g}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_delivery_keyboard(),
            )
            return

        if data == "admin:rates":
            await query.edit_message_text(
                "💱 <b>Курсы и коэффициент</b>\n\n"
                f"Текущий курс: <b>{USD_RUB_RATE:g} ₽/$</b>\n"
                f"Коэффициент DP: <b>{PRICE_COEFFICIENT:g}</b>\n\n"
                "Изменить курс:\n"
                "<code>/rate 107.5</code>\n\n"
                "Изменить коэффициент:\n"
                "<code>/coef 1.34</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_back_keyboard(),
            )
            return

        if data == "admin:search":
            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_search_payload", None)
            context.user_data.pop("admin_search_page", None)

            await query.edit_message_text(
                "🔎 <b>Поиск</b>\n\n"
                "Выбери, что именно нужно найти:",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_menu_keyboard(),
            )
            return

        if data.startswith("admin:searchtype:"):
            mode = data.split(":", 2)[2]
            prompts = {
                "oem": (
                    "🧾 <b>Поиск по OEM</b>\n\n"
                    "Отправь OEM номер. Покажу историю запросов по этой позиции: "
                    "клиентов, количество и связанные запросы.\n"
                    "Например: <code>0627-111</code>"
                ),
                "part_name": (
                    "🔤 <b>Поиск по названию детали</b>\n\n"
                    "Отправь название или несколько слов из названия. "
                    "Порядок слов и запятые не важны.\n"
                    "Например: <code>Belt Drive</code>"
                ),
                "customer": (
                    "👤 <b>Поиск по клиенту</b>\n\n"
                    "Можно отправить имя или часть имени, @username "
                    "или Telegram ID.\n"
                    "Например:\n"
                    "<code>Дмитрий</code>\n"
                    "<code>@dimych_msk</code>\n"
                    "<code>7005635854</code>"
                ),
                "order": (
                    "📋 <b>Поиск по номеру запроса</b>\n\n"
                    "Отправь полный номер или его часть.\n"
                    "Например: <code>E01J-2973</code>"
                ),
                "universal": (
                    "🔎 <b>Универсальный поиск</b>\n\n"
                    "Отправь номер запроса, OEM, название детали, имя клиента, "
                    "@username или Telegram ID. Бот сам определит тип поиска."
                ),
            }
            if mode not in prompts:
                await query.answer("Неизвестный тип поиска.", show_alert=True)
                return

            context.user_data["admin_search_mode"] = mode
            context.user_data.pop("admin_search_payload", None)
            context.user_data.pop("admin_search_page", None)

            await query.edit_message_text(
                prompts[mode],
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_prompt_keyboard(),
            )
            return

        if data.startswith("admin:returnpart:"):
            parts = data.split(":", 3)
            try:
                page = int(parts[2])
                term = _cb_b64_decode(parts[3])
            except (IndexError, TypeError, ValueError):
                await query.answer(
                    "Некорректный возврат к поиску по названию.",
                    show_alert=True,
                )
                return

            payload = search_admin_parts_by_name(term)
            if not payload.get("items"):
                await query.answer(
                    "Результаты поиска больше не найдены.",
                    show_alert=True,
                )
                return

            context.user_data["admin_search_payload"] = payload
            context.user_data["admin_search_page"] = page
            await query.edit_message_text(
                format_admin_search_results(payload, page=page),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_results_keyboard(payload, page=page),
            )
            return

        if data.startswith("admin:returnsearch"):
            payload = None
            page = 0

            if data.startswith("admin:returnsearch:o:"):
                parts = data.split(":", 4)
                try:
                    page = int(parts[3])
                    oem = _cb_b64_decode(parts[4])
                except (IndexError, TypeError, ValueError):
                    await query.answer("Некорректный возврат к OEM.", show_alert=True)
                    return
                payload = get_admin_oem_history(oem)
            elif data.startswith("admin:returnsearch:c:"):
                parts = data.split(":", 4)
                try:
                    page = int(parts[3])
                    telegram_user_id = int(parts[4])
                except (IndexError, TypeError, ValueError):
                    await query.answer("Некорректный возврат к клиенту.", show_alert=True)
                    return
                payload = get_admin_customer_orders(telegram_user_id)
            else:
                payload = context.user_data.get("admin_search_payload")
                page = int(context.user_data.get("admin_search_page", 0) or 0)

            if not isinstance(payload, dict) or not payload.get("items"):
                await query.answer(
                    "Результаты поиска больше не найдены. Запусти поиск заново.",
                    show_alert=True,
                )
                return

            context.user_data["admin_search_payload"] = payload
            context.user_data["admin_search_page"] = page

            await query.edit_message_text(
                format_admin_search_results(payload, page=page),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_results_keyboard(payload, page=page),
            )
            return

        if data.startswith("admin:searchpage:"):
            payload = context.user_data.get("admin_search_payload")
            if not payload:
                await query.answer("Поиск устарел. Запусти новый поиск.", show_alert=True)
                return
            try:
                page = int(data.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                page = 0
            context.user_data["admin_search_page"] = page
            await query.edit_message_text(
                format_admin_search_results(payload, page=page),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_results_keyboard(payload, page=page),
            )
            return

        if data.startswith("admin:searchpart:"):
            parts = data.split(":")
            parent_term = None
            parent_page = 0

            try:
                if len(parts) >= 5:
                    parent_page = int(parts[2])
                    oem = _cb_b64_decode(parts[3])
                    parent_term = _cb_b64_decode(parts[4])
                else:
                    oem = _cb_b64_decode(parts[-1])
            except (TypeError, ValueError):
                await query.answer("Некорректный OEM.", show_alert=True)
                return

            payload = get_admin_oem_history(oem)
            if not payload.get("items"):
                await query.answer("История этого OEM не найдена.", show_alert=True)
                return

            if parent_term:
                payload["parent_part_term"] = parent_term
                payload["parent_part_page"] = parent_page

            context.user_data["admin_search_payload"] = payload
            context.user_data["admin_search_page"] = 0
            await query.edit_message_text(
                format_admin_search_results(payload, page=0),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_results_keyboard(payload, page=0),
            )
            return

        if data.startswith("admin:searchcustomer:"):
            try:
                telegram_user_id = int(data.rsplit(":", 1)[1])
            except (TypeError, ValueError):
                await query.answer("Некорректный Telegram ID.", show_alert=True)
                return

            payload = get_admin_customer_orders(telegram_user_id)
            if not payload.get("items"):
                await query.answer("Запросы клиента не найдены.", show_alert=True)
                return

            context.user_data["admin_search_payload"] = payload
            context.user_data["admin_search_page"] = 0
            await query.edit_message_text(
                format_admin_search_results(payload, page=0),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_search_results_keyboard(payload, page=0),
            )
            return

        if data.startswith("admin:deliverytariff:"):
            context.user_data.pop("admin_delivery_edit", None)
            context.user_data.pop("admin_delivery_setting_edit", None)
            context.user_data.pop("admin_delivery_divisor_edit", None)

            tariff_code = data.split(":", 2)[2]

            if tariff_code not in {"comfort", "economy", "mix"}:
                await query.answer(
                    "Неизвестный тариф.",
                    show_alert=True,
                )
                return

            tariff_text = format_admin_delivery_tariff(tariff_code)

            if tariff_text is None:
                await query.answer(
                    "Тариф не найден.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                tariff_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_delivery_tariff_keyboard(tariff_code),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:deliveryedit:"):
            parts = data.split(":", 3)

            if len(parts) != 4:
                await query.answer(
                    "Некорректная команда.",
                    show_alert=True,
                )
                return

            tariff_code = parts[2]
            parameter = parts[3]

            field_map = {
                "base": "base_rub_per_kg",
                "volume": "volume_rub_per_kg",
            }

            field = field_map.get(parameter)

            allowed = {
                "comfort": {"base", "volume"},
                "economy": {"base"},
                "mix": {"base", "volume"},
            }

            if (
                tariff_code not in allowed
                or parameter not in allowed[tariff_code]
                or field is None
            ):
                await query.answer(
                    "Этот параметр нельзя изменить.",
                    show_alert=True,
                )
                return

            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_customer_total_order_id", None)

            context.user_data["admin_delivery_edit"] = {
                "tariff_code": tariff_code,
                "field": field,
                "parameter": parameter,
            }

            tariff_text = format_admin_delivery_tariff(tariff_code)

            if parameter == "base":
                prompt = (
                    "⚖️ <b>Введи новую стоимость за 1 кг "
                    "фактического веса.</b>"
                )
            elif tariff_code == "comfort":
                prompt = (
                    "📦 <b>Введи новую стоимость за 1 кг "
                    "объёмного веса.</b>"
                )
            else:
                prompt = (
                    "📦 <b>Введи новую стоимость за 1 кг "
                    "объёмного веса.</b>"
                )

            await query.edit_message_text(
                tariff_text
                + "\n\n"
                + prompt
                + "\n\nНапример: <code>2700</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ К тарифу",
                            callback_data=(
                                f"admin:deliverytariff:{tariff_code}"
                            ),
                        )
                    ]
                ]),
                disable_web_page_preview=True,
            )
            return

        if data == "admin:orders":
            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_search_payload", None)
            context.user_data.pop("admin_search_page", None)
            context.user_data.pop("admin_search_last_mode", None)
            context.user_data.pop("admin_customer_total_order_id", None)

            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                count = conn.execute(
                    "SELECT COUNT(*) FROM orders"
                ).fetchone()[0]

            text = (
                "📦 <b>Запросы</b>\n\n"
                f"Всего сохранено: <b>{count}</b>\n\n"
            )

            if count:
                text += "Выбери статус:"
            else:
                text += "Сохранённых запросов пока нет."

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_orders_keyboard(),
            )
            return

        if data.startswith("admin:execute:"):
            order_id = data.split(":", 2)[2]
            if not set_admin_order_status(
                order_id,
                "executing",
                expected_current="confirmed",
            ):
                reservations = stock_engine.list_order_reservations(
                    order_id, db_file=ORDERS_DB_FILE
                )
                covered = {}
                for reservation in reservations:
                    if reservation.get("status") not in {"hold", "reserved", "committed"}:
                        continue
                    item_id = reservation.get("order_item_id")
                    if item_id is not None:
                        covered[int(item_id)] = covered.get(int(item_id), 0.0) + float(
                            reservation.get("quantity") or 0
                        )
                with sqlite3.connect(ORDERS_DB_FILE) as conn:
                    required_items = conn.execute(
                        """
                        SELECT id, oem, quantity
                        FROM order_items
                        WHERE order_id = ?
                          AND COALESCE(NULLIF(LOWER(TRIM(offer_source)), ''), 'usa') = 'warehouse'
                        ORDER BY position
                        """,
                        (order_id,),
                    ).fetchall()
                missing = [
                    f"• {oem or '—'} × {int(quantity or 0)} — склад не выбран"
                    for item_id, oem, quantity in required_items
                    if covered.get(int(item_id), 0.0) < float(quantity or 0)
                ]
                message = "⚠️ Нельзя принять запрос в исполнение."
                if missing:
                    message += "\n\nНе готовы складские резервы:\n" + "\n".join(missing)
                else:
                    message += (
                        "\n\nСкладские резервы есть, но не удалось синхронизировать "
                        "их со статусом запроса."
                    )
                await query.answer()
                await context.bot.send_message(chat_id=query.message.chat_id, text=message)
                return
            await query.answer(
                "Запрос принят в исполнение. Складские резервы переданы складу.",
                show_alert=True,
            )
            await query.edit_message_text(
                format_admin_order(order_id),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_order_keyboard(order_id, context=context),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:complete:"):
            order_id = data.split(":", 2)[2]
            if not set_admin_order_status(
                order_id,
                "completed",
                expected_current="executing",
            ):
                await query.answer(
                    "Запрос нельзя завершить из текущего статуса "
                    "или не удалось синхронизировать складской резерв.",
                    show_alert=True,
                )
                return
            await query.answer(
                "Запрос выполнен. Незавершённые складские резервы проверены.",
                show_alert=True,
            )
            await query.edit_message_text(
                format_admin_order(order_id),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_order_keyboard(order_id, context=context),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:ordersstatus:"):
            context.user_data.pop("admin_search_payload", None)
            context.user_data.pop("admin_search_page", None)
            context.user_data.pop("admin_search_last_mode", None)

            parts = data.split(":")
            status = parts[2] if len(parts) > 2 else ""
            try:
                page = int(parts[3]) if len(parts) > 3 else 0
            except ValueError:
                page = 0

            labels = dict(ADMIN_ORDER_STATUSES)
            if status not in labels:
                await query.answer("Неизвестный статус.", show_alert=True)
                return

            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                if status == "waiting":
                    count = conn.execute(
                        "SELECT COUNT(*) FROM orders WHERE status IN ('waiting','calculated')"
                    ).fetchone()[0]
                else:
                    count = conn.execute(
                        "SELECT COUNT(*) FROM orders WHERE status = ?",
                        (status,),
                    ).fetchone()[0]

            total_pages = max((count + 9) // 10, 1)
            page = min(max(page, 0), total_pages - 1)
            page_line = (
                f"\nСтраница: <b>{page + 1}/{total_pages}</b>"
                if total_pages > 1
                else ""
            )

            await query.edit_message_text(
                f"{labels[status]}\n\nЗапросов: <b>{count}</b>{page_line}",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_orders_status_keyboard(status, page=page),
            )
            return

        if data.startswith("admin:dg:"):
            from telegram.error import BadRequest

            async def edit_group_screen(screen):
                try:
                    await query.edit_message_text(
                        screen[0], parse_mode=ParseMode.HTML,
                        reply_markup=screen[1],
                    )
                except BadRequest as exc:
                    if "message is not modified" not in str(exc).lower():
                        raise

            parts = data.split(":")
            if len(parts) not in (4, 5) or parts[2] not in {
                "view", "new", "toggle", "save", "o", "m", "c"
            } or not parts[3] or (
                parts[2] in {"toggle", "o"}
            ) != (len(parts) == 5):
                await query.answer(
                    "Некорректная команда груза.", show_alert=True
                )
                return
            action, token = parts[2], parts[3]
            state = context.user_data.get("admin_delivery_group_flow")
            if not state or token != state["token"]:
                await query.answer(
                    "Экран устарел. Открой доставку из карточки запроса.",
                    show_alert=True,
                )
                return
            order_id = state["order_id"]
            if action == "o":
                try:
                    group_id = int(parts[4])
                except ValueError:
                    await query.answer(
                        "Некорректный груз.", show_alert=True
                    )
                    return
                if not 1 <= group_id <= 9223372036854775807:
                    await query.answer(
                        "Некорректный груз.", show_alert=True
                    )
                    return
                if get_admin_delivery_group(
                    order_id, group_id
                ) is None:
                    await query.answer(
                        "Груз не найден в этом запросе.", show_alert=True
                    )
                    return
                await query.answer()
                next_token = new_admin_delivery_group_token(
                    context.user_data, order_id,
                    measurement_group_id=group_id,
                )
                await edit_group_screen(format_admin_delivery_group(
                    order_id, group_id, next_token
                ))
                return
            if action in {"m", "c"}:
                group_id = state.get("measurement_group_id")
                if not group_id or get_admin_delivery_group(
                    order_id, group_id
                ) is None:
                    await query.answer(
                        "Экран груза устарел.", show_alert=True
                    )
                    return
                if action == "c" and (
                    state.get("measurement_stage") != "preview"
                    or set(state.get("measurement_values") or {}) != {
                        "actual", "length", "width", "height"
                    }
                ):
                    await query.answer(
                        "Измерения не готовы.", show_alert=True
                    )
                    return
                await query.answer()
                if action == "m":
                    new_admin_delivery_group_token(
                        context.user_data, order_id,
                        measurement_group_id=group_id,
                        measurement_stage="actual",
                        measurement_values={},
                    )
                    await query.edit_message_text(
                        "⚖️ Введи фактический вес груза в кг "
                        "(конечное число больше нуля)."
                    )
                    return
                saved, result = save_admin_delivery_group_measurements(
                    order_id, group_id, state["measurement_values"]
                )
                next_token = new_admin_delivery_group_token(
                    context.user_data, order_id,
                    measurement_group_id=group_id,
                )
                screen = format_admin_delivery_group(
                    order_id, group_id, next_token,
                    error=None if saved else result,
                )
                await edit_group_screen(screen)
                return
            snapshot = get_admin_delivery_groups(order_id)
            if snapshot is None:
                await query.answer("Запрос не найден.", show_alert=True)
                return

            item_id = None
            if action == "toggle":
                try:
                    item_id = int(parts[4])
                except ValueError:
                    await query.answer("Некорректная позиция.", show_alert=True)
                    return

            await query.answer()
            error = None
            if action == "view":
                selected_ids = None
            elif action == "new":
                selected_ids = set()
            else:
                previous = state["selected_ids"]
                selected_ids = None if previous is None else set(previous)
                if selected_ids is None:
                    error = "Выбор позиций устарел. Открой создание груза заново."
                if action == "toggle" and selected_ids is not None:
                    available = {row[0] for row in snapshot[2]}
                    if item_id not in available:
                        selected_ids.discard(item_id)
                        error = "Позиция уже недоступна; список обновлён."
                    elif item_id in selected_ids:
                        selected_ids.remove(item_id)
                    else:
                        selected_ids.add(item_id)
                elif action == "save" and selected_ids is not None:
                    try:
                        created, result = create_admin_delivery_group(
                            order_id, selected_ids
                        )
                    except sqlite3.Error:
                        log.exception("Could not create delivery group")
                        created, result = False, "Не удалось сохранить груз."
                    if created:
                        selected_ids = None
                    else:
                        error = result
                        latest = get_admin_delivery_groups(order_id)
                        if latest is not None:
                            selected_ids.intersection_update(
                                row[0] for row in latest[2]
                            )

            next_token = new_admin_delivery_group_token(
                context.user_data, order_id, selected_ids
            )
            screen = format_admin_delivery_groups(
                order_id, next_token, selected_ids, error
            )
            if screen is None:
                return
            await edit_group_screen(screen)
            return

        if data.startswith("admin:orderdelivery:"):
            order_id = data.split(":", 2)[2]

            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_customer_total_order_id", None)

            delivery_text = format_admin_order_delivery(order_id)

            if delivery_text is None:
                await query.answer(
                    "Запрос не найден в истории.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                delivery_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_order_delivery_keyboard(order_id, context.user_data),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:setorderdelivery:"):
            parts = data.split(":", 3)

            if len(parts) != 4:
                await query.answer(
                    "Некорректная команда доставки.",
                    show_alert=True,
                )
                return

            order_id = parts[2]
            mode = parts[3]

            if not set_admin_order_delivery_mode(order_id, mode):
                await query.answer(
                    "Не удалось сохранить вариант доставки.",
                    show_alert=True,
                )
                return

            delivery_text = format_admin_order_delivery(order_id)

            if delivery_text is None:
                await query.answer(
                    "Запрос не найден в истории.",
                    show_alert=True,
                )
                return

            if mode == "pending":
                reply_markup = admin_order_delivery_keyboard(order_id, context.user_data)
            else:
                reply_markup = admin_order_delivery_selected_keyboard(
                    order_id,
                    mode,
                )

            await query.edit_message_text(
                delivery_text,
                parse_mode=ParseMode.HTML,
                reply_markup=reply_markup,
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:deliveryweight:"):
            order_id = data.split(":", 2)[2]
            row = get_admin_order_delivery(order_id)

            if not row or not row[1]:
                await query.answer(
                    "Сначала выбери тариф доставки.",
                    show_alert=True,
                )
                return

            tariff_code = row[1]

            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_customer_total_order_id", None)
            context.user_data.pop("admin_delivery_manual_order_id", None)
            context.user_data.pop("admin_delivery_volume_order_id", None)

            context.user_data["admin_delivery_actual_order_id"] = order_id

            tariff_labels = {
                "comfort": "🟢 Комфорт",
                "economy": "🔵 Эконом",
                "mix": "🔴 MIX",
            }

            await query.edit_message_text(
                "🚚 <b>Расчёт доставки</b>\n\n"
                f"<b>Запрос:</b> <code>{escape(order_id)}</code>\n"
                f"<b>Тариф:</b> {tariff_labels.get(tariff_code, escape(tariff_code))}\n\n"
                "⚖️ <b>Введи фактический вес в кг.</b>\n\n"
                "Например: <code>1</code> или <code>1.5</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ К доставке",
                            callback_data=f"admin:orderdelivery:{order_id}",
                        )
                    ]
                ]),
            )
            return

        if data.startswith("admin:deliverymanual:"):
            order_id = data.split(":", 2)[2]
            row = get_admin_order_delivery(order_id)

            if not row or row[4] is None:
                await query.answer(
                    "Сначала рассчитай доставку по весу.",
                    show_alert=True,
                )
                return

            context.user_data.pop("admin_search_mode", None)
            context.user_data.pop("admin_customer_total_order_id", None)
            context.user_data.pop("admin_delivery_actual_order_id", None)
            context.user_data.pop("admin_delivery_volume_order_id", None)

            context.user_data["admin_delivery_manual_order_id"] = order_id

            await query.edit_message_text(
                (
                    "✏️ <b>Ручная стоимость доставки</b>\n\n"
                    + f"<b>Запрос:</b> <code>{escape(order_id)}</code>\n"
                    + (
                        f"<b>Расчётная стоимость:</b> "
                        f"{format_rub_whole(row[4])}\n\n"
                    ).replace(",", " ")
                    + "Введи стоимость доставки, которую нужно применить.\n\n"
                    + "Например: <code>3500</code>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ К доставке",
                            callback_data=f"admin:orderdelivery:{order_id}",
                        )
                    ]
                ]),
            )
            return

        if data.startswith("admin:quotepreview:"):
            order_id = data.split(":", 2)[2]

            quote_text = format_stage2_customer_total(order_id)
            if quote_text is None:
                quote_text = format_customer_quote(order_id)

            if quote_text is None:
                await query.answer(
                    "Сначала укажи стоимость товаров для клиента.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                "👁 <b>Предпросмотр сообщения клиенту</b>\n\n"
                + quote_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_customer_quote_preview_keyboard(order_id),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:quotesend:"):
            order_id = data.split(":", 2)[2]
            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                owner = conn.execute(
                    "SELECT telegram_user_id FROM orders WHERE order_id = ?",
                    (order_id,),
                ).fetchone()
            if owner is None:
                await query.answer("Запрос не найден.", show_alert=True)
                return
            quote_text = format_stage2_customer_total(order_id, int(owner[0]))
            if quote_text is None:
                await query.answer(
                    "Итог Stage 2 ещё не готов к отправке.", show_alert=True
                )
                return

            # Only a complete final quote may enter the confirmation flow.
            snapshot = get_stage2_customer_total(order_id, int(owner[0]))
            final_ready = bool(
                snapshot is not None
                and snapshot[1] is not None
                and snapshot[2] is not None
            )

            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                sent_state = conn.execute(
                    """SELECT quote_sent_at, final_quote_sent_at
                       FROM orders WHERE order_id = ?""",
                    (order_id,),
                ).fetchone()
            already_sent = bool(
                sent_state
                and (
                    sent_state[1] if final_ready
                    else sent_state[0]
                )
            )
            if already_sent:
                await query.answer(
                    "Этот расчёт уже отправлен клиенту.", show_alert=True
                )
                return

            customer_markup = None
            if final_ready:
                customer_markup = InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "✅ Подтвердить запрос",
                        callback_data=f"clientconfirm:{order_id}",
                    )
                ]])

            try:
                sent_message = await context.bot.send_message(
                    chat_id=int(owner[0]),
                    text=quote_text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=customer_markup,
                    disable_web_page_preview=True,
                )
            except Exception:
                log.exception("Could not send Stage 2 customer total")
                await query.answer("Не удалось отправить расчёт клиенту.", show_alert=True)
                return

            sent_at = datetime.now().astimezone().isoformat(timespec="seconds")
            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                if final_ready:
                    changed = conn.execute(
                        """UPDATE orders
                           SET final_quote_sent_at = ?,
                               final_quote_message_id = ?,
                               updated_at = ?,
                               status = 'waiting'
                           WHERE order_id = ?
                             AND final_quote_sent_at IS NULL""",
                        (
                            sent_at,
                            sent_message.message_id,
                            sent_at,
                            order_id,
                        ),
                    ).rowcount
                else:
                    changed = conn.execute(
                        """UPDATE orders
                           SET quote_sent_at = ?,
                               quote_message_id = ?,
                               updated_at = ?
                           WHERE order_id = ?
                             AND quote_sent_at IS NULL""",
                        (
                            sent_at,
                            sent_message.message_id,
                            sent_at,
                            order_id,
                        ),
                    ).rowcount
                conn.commit()

            # Telegram delivery succeeded but another callback won the DB race.
            # Never send again; just refresh the admin preview state.
            if changed != 1:
                await query.answer(
                    "Расчёт уже был отправлен клиенту.", show_alert=True
                )
            else:
                await query.answer(
                    "Окончательный расчёт отправлен клиенту."
                    if final_ready
                    else "Предварительный расчёт отправлен клиенту.",
                    show_alert=True,
                )

            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "⬅️ К запросу",
                        callback_data=f"admin:order:{order_id}",
                    )
                ]])
            )
            return

        if data.startswith("admin:customertotal:"):
            order_id = data.split(":", 2)[2]
            text = format_admin_order(order_id)

            if text is None:
                await query.answer(
                    "Запрос не найден в истории.",
                    show_alert=True,
                )
                return

            context.user_data.pop("admin_search_mode", None)
            context.user_data["admin_customer_total_order_id"] = order_id

            await query.edit_message_text(
                text
                + "\n\n💰 <b>Введи стоимость товаров для клиента в рублях.</b>"
                + "\n\nНапример: <code>35000</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ К запросу",
                            callback_data=f"admin:order:{order_id}",
                        )
                    ]
                ]),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:status:"):
            order_id = data.split(":", 2)[2]
            text = format_admin_order(order_id)

            if text is None:
                await query.answer(
                    "Запрос не найден в истории.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                text + "\n\n<b>Выбери новый статус:</b>",
                parse_mode=ParseMode.HTML,
                reply_markup=admin_status_keyboard(order_id),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:setstatus:"):
            parts = data.split(":", 3)

            if len(parts) != 4:
                await query.answer(
                    "Некорректная команда статуса.",
                    show_alert=True,
                )
                return

            order_id = parts[2]
            new_status = parts[3]

            if not set_admin_order_status(order_id, new_status):
                await query.answer(
                    "Не удалось изменить статус.",
                    show_alert=True,
                )
                return

            text = format_admin_order(order_id)

            if text is None:
                await query.answer(
                    "Запрос не найден в истории.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_order_keyboard(order_id, context=context),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:itemtypes:"):
            order_id = data.split(":", 2)[2]
            item_types_text = format_admin_item_types(order_id)

            if item_types_text is None:
                await query.answer(
                    "В запросе нет позиций.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                item_types_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_item_types_keyboard(order_id),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:setitemtype:"):
            parts = data.split(":", 4)

            if len(parts) != 5:
                await query.answer(
                    "Некорректная команда.",
                    show_alert=True,
                )
                return

            order_id = parts[2]

            try:
                order_item_id = int(parts[3])
            except ValueError:
                await query.answer(
                    "Некорректная позиция.",
                    show_alert=True,
                )
                return

            item_type = parts[4]
            previous_type = next(
                (row[6] for row in get_admin_order_item_types(order_id)
                 if row[0] == order_item_id),
                None,
            )

            if not set_order_item_type(
                order_id,
                order_item_id,
                item_type,
            ):
                await query.answer(
                    "Не удалось сохранить тип позиции.",
                    show_alert=True,
                )
                return

            item_types_text = format_admin_item_types(order_id, classify=False)
            current_type = next(
                (row[6] for row in get_admin_order_item_types(order_id)
                 if row[0] == order_item_id),
                None,
            )
            if current_type == item_type and current_type != previous_type:
                from contextlib import closing

                try:
                    db_uri = ORDERS_DB_FILE.resolve().as_uri() + "?mode=ro"
                    with closing(sqlite3.connect(db_uri, uri=True)) as conn:
                        owner = conn.execute(
                            "SELECT telegram_user_id FROM orders WHERE order_id = ?",
                            (order_id,),
                        ).fetchone()
                    if owner is None:
                        raise ValueError("Request owner not found")
                    owner_id, choice_text, keyboard = (
                        prepare_customer_delivery_choice_readonly(
                            order_id, int(owner[0])
                        )
                    )
                    await context.bot.send_message(
                        chat_id=owner_id,
                        text=choice_text,
                        parse_mode=ParseMode.HTML,
                        reply_markup=keyboard,
                        disable_web_page_preview=True,
                    )
                except Exception:
                    log.exception("Could not send updated delivery screen")
                    try:
                        await context.bot.send_message(
                            chat_id=user.id,
                            text=(
                                "Тип позиции сохранён, но актуальный экран выбора "
                                "доставки не удалось отправить клиенту."
                            ),
                        )
                    except Exception:
                        log.exception("Could not notify manager about delivery screen")

            await query.edit_message_text(
                item_types_text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_item_types_keyboard(order_id),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:searchorder:"):
            context.user_data.pop("admin_customer_total_order_id", None)
            parts = data.split(":", 5)
            if len(parts) != 6:
                await query.answer("Некорректная ссылка на запрос.", show_alert=True)
                return

            origin_type = parts[2]
            try:
                page = int(parts[3])
            except (TypeError, ValueError):
                page = 0
            origin_value = parts[4]
            order_id = parts[5]

            if origin_type == "o":
                try:
                    oem = _cb_b64_decode(origin_value)
                except (TypeError, ValueError):
                    await query.answer("Некорректный OEM.", show_alert=True)
                    return
                payload = get_admin_oem_history(oem)
            elif origin_type == "c":
                try:
                    telegram_user_id = int(origin_value)
                except (TypeError, ValueError):
                    await query.answer("Некорректный Telegram ID.", show_alert=True)
                    return
                payload = get_admin_customer_orders(telegram_user_id)
            else:
                await query.answer("Неизвестный источник поиска.", show_alert=True)
                return

            context.user_data["admin_search_payload"] = payload
            context.user_data["admin_search_page"] = page

            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE orders SET status = 'working', updated_at = ? WHERE order_id = ? AND status = 'new'",
                    (now, order_id),
                )
                conn.commit()

            text = format_admin_order(order_id)
            if text is None:
                await query.answer("Запрос не найден в истории.", show_alert=True)
                return

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_order_keyboard(order_id, context=context),
                disable_web_page_preview=True,
            )
            return

        if data.startswith("admin:order:"):
            context.user_data.pop("admin_customer_total_order_id", None)
            order_id = data.split(":", 2)[2]
            # Opening a fresh request means the manager has started working on it.
            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                now = datetime.now().astimezone().isoformat(timespec="seconds")
                conn.execute(
                    "UPDATE orders SET status = 'working', updated_at = ? WHERE order_id = ? AND status = 'new'",
                    (now, order_id),
                )
                conn.commit()
            text = format_admin_order(order_id)

            if text is None:
                await query.answer(
                    "Запрос не найден в истории.",
                    show_alert=True,
                )
                return

            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=admin_order_keyboard(order_id, context=context),
                disable_web_page_preview=True,
            )
            return

        return

    if data == "noop":
        return

    if data.startswith("cartadd:"):
        key = data.split(":", 1)[1]
        found = context.user_data.setdefault("found_items", {})
        result = found.get(key)
        if not result:
            result = await restore_found_item_from_cache(query, key)
            if result:
                found[key] = result

        if not result:
            if query.message is not None:
                await query.message.reply_text(
                    "Позиция больше недоступна в сохранённом кэше. "
                    "Найди её ещё раз."
                )
            return

        cart = context.user_data.setdefault("cart", {})
        source_token = key.rsplit("|", 1)[-1]
        offer_source = "usa"
        warehouse_id = None
        warehouse_public_name = None
        price_snapshot_rub = None
        available_snapshot = None

        if source_token.startswith("warehouse:"):
            offer_source = "warehouse"
            try:
                warehouse_id = int(source_token.split(":", 1)[1])
            except (TypeError, ValueError):
                warehouse_id = None
            if warehouse_id is not None:
                for offer in warehouse_stock_service.client_stock_summary(
                    _client_stock_oem(result),
                    db_file=ORDERS_DB_FILE,
                ):
                    if int(offer.get("warehouse_id") or 0) != warehouse_id:
                        continue
                    if not offer.get("is_fresh"):
                        break
                    available_snapshot = offer.get("available_quantity")
                    price_snapshot_rub = offer.get("price_rub")
                    warehouse_public_name = offer.get("public_name")
                    break
            if (
                warehouse_id is None
                or available_snapshot is None
                or float(available_snapshot) <= 0
                or price_snapshot_rub is None
            ):
                await query.answer(
                    "Предложение склада изменилось. Обнови поиск.",
                    show_alert=True,
                )
                return
        else:
            price_snapshot_rub = customer_rub_price_from_dp(
                result.get("_dealer_price_usd"),
                rate=load_usd_rub_rate(),
            )

        if key in cart:
            if (
                offer_source == "warehouse"
                and float(cart[key].get("qty", 1)) + 1 > float(available_snapshot)
            ):
                await query.answer("На складе недостаточно свободного остатка.", show_alert=True)
                return
            cart[key]["qty"] += 1
        else:
            cart[key] = {
                "manufacturer": result.get("manufacturer"),
                "oem": result.get("item_sku") or result.get("oem"),
                "requested_oem": (
                    result.get("query_oem")
                    or result.get("item_sku")
                    or result.get("oem")
                ),
                "name": result.get("name"),
                "price": result.get("price"),
                "catalog": result.get("catalog"),
                "offer_source": offer_source,
                "warehouse_id": warehouse_id,
                "warehouse_public_name": warehouse_public_name,
                "price_snapshot_rub": price_snapshot_rub,
                "available_snapshot": available_snapshot,
                "_dealer_price_usd": result.get("_dealer_price_usd"),
                "_dealer_price_source": result.get("_dealer_price_source"),
                "_dealer_price_checked_at": result.get("_dealer_price_checked_at"),
                "_dealer_price_status": result.get("_dealer_price_status"),
                "qty": 1,
            }
        analytics_user = update.effective_user
        analytics_oem = str(
            result.get("query_oem")
            or result.get("item_sku")
            or result.get("oem")
            or ""
        ).strip()
        if analytics_user is not None and analytics_oem:
            pricing_analytics.mark_latest_matching_cart(
                ORDERS_DB_FILE,
                source_bot="pricing",
                telegram_user_id=int(analytics_user.id),
                oem=analytics_oem,
            )

        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ В корзине", callback_data="cart")],
            [InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again"), InlineKeyboardButton("🛒 Моя корзина", callback_data="cart")],
        ]))
        return

    if data.startswith("clientconfirm:"):
        order_id = data.split(":", 1)[1]
        user = update.effective_user
        if user is None or not order_id:
            await query.answer("Не удалось определить запрос.", show_alert=True)
            return
        confirmed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        with sqlite3.connect(ORDERS_DB_FILE) as conn:
            changed = conn.execute(
                """UPDATE orders
                   SET status = 'confirmed',
                       customer_confirmation_status = 'confirmed',
                       customer_confirmed_at = ?, updated_at = ?
                   WHERE order_id = ? AND telegram_user_id = ?
                     AND status = 'waiting'
                     AND customer_confirmation_status IS NULL""",
                (confirmed_at, confirmed_at, order_id, user.id),
            ).rowcount
            conn.commit()
            state = conn.execute(
                "SELECT status, customer_confirmation_status FROM orders WHERE order_id = ? AND telegram_user_id = ?",
                (order_id, user.id),
            ).fetchone()
        if changed == 0:
            if state == ("confirmed", "confirmed"):
                await query.answer("Запрос уже подтверждён.", show_alert=True)
            else:
                await query.answer("Запрос сейчас нельзя подтвердить.", show_alert=True)
            return
        await query.answer("Запрос подтверждён.")

        # Remove the confirmation button from the final quote so the client
        # immediately sees that no further action is required here.
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            log.exception("Could not remove customer confirmation button")

        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "✅ <b>Запрос подтверждён</b>\n\n"
                    f"<b>Номер:</b> <code>{escape(order_id)}</code>\n"
                    "Мы получили ваше подтверждение. Запрос передан менеджеру в работу."
                ),
                parse_mode=ParseMode.HTML,
            )
        except Exception:
            log.exception("Could not send customer confirmation message")

        try:
            await context.bot.send_message(
                chat_id=RATE_ADMIN_USER_ID,
                text=(
                    "✅ <b>Клиент подтвердил запрос</b>\n\n"
                    f"<b>Номер:</b> <code>{escape(order_id)}</code>\n"
                    f"<b>Telegram ID:</b> <code>{user.id}</code>"
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(
                        "📦 Открыть запрос",
                        callback_data=f"admin:order:{order_id}",
                    )
                ]]),
            )
        except Exception:
            log.exception("Could not notify admin about customer confirmation")
        return

    if data == "cart":
        cart = context.user_data.setdefault("cart", {})
        await query.edit_message_text(format_cart(cart), parse_mode=ParseMode.HTML, reply_markup=cart_keyboard(cart))
        return

    if data.startswith(("cartplus:", "cartminus:", "cartremove:")):
        action, key = data.split(":", 1)
        cart = context.user_data.setdefault("cart", {})
        if key in cart:
            if action == "cartplus":
                item = cart[key]
                if str(item.get("offer_source") or "usa") == "warehouse":
                    warehouse_id = int(item.get("warehouse_id") or 0)
                    current_offer = None
                    for offer in warehouse_stock_service.client_stock_summary(
                        str(item.get("oem") or ""),
                        db_file=ORDERS_DB_FILE,
                    ):
                        if int(offer.get("warehouse_id") or 0) == warehouse_id:
                            current_offer = offer
                            break
                    available = (
                        current_offer.get("available_quantity")
                        if current_offer and current_offer.get("is_fresh")
                        else None
                    )
                    if available is None or int(item.get("qty", 1)) + 1 > float(available):
                        await query.answer(
                            "На складе недостаточно актуального свободного остатка.",
                            show_alert=True,
                        )
                        return
                cart[key]["qty"] += 1
            elif action == "cartminus":
                cart[key]["qty"] -= 1
                if cart[key]["qty"] <= 0: cart.pop(key, None)
            else: cart.pop(key, None)
        await query.edit_message_text(format_cart(cart), parse_mode=ParseMode.HTML, reply_markup=cart_keyboard(cart))
        return

    if data == "cartclear":
        context.user_data["cart"] = {}
        await query.edit_message_text(format_cart({}), parse_mode=ParseMode.HTML, reply_markup=cart_keyboard({}))
        return

    if data == "checkout":
        cart = context.user_data.setdefault("cart", {})
        if not cart:
            await query.edit_message_text(
                format_cart({}),
                parse_mode=ParseMode.HTML,
                reply_markup=cart_keyboard({}),
            )
            return

        context.user_data.pop("checkout_delivery_preference", None)
        has_usa = any(
            str(item.get("offer_source") or "usa").strip().lower() == "usa"
            for item in cart.values()
        )
        if not has_usa:
            context.user_data["checkout_delivery_preference"] = "local_only"
            await query.edit_message_text(
                format_cart(cart, checkout=True),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✅ Подтвердить запрос",
                        callback_data="checkout_confirm",
                    )],
                    [InlineKeyboardButton(
                        "⬅️ Вернуться в корзину",
                        callback_data="cart",
                    )],
                ]),
            )
            return

        prepare_cart_delivery_choices(cart)
        await query.edit_message_text(
            format_cart(cart, checkout=True)
            + "\n\n"
            + format_checkout_delivery_choices(cart),
            parse_mode=ParseMode.HTML,
            reply_markup=checkout_delivery_keyboard(cart),
            disable_web_page_preview=True,
        )
        return

    if data == "checkout_delivery_details":
        cart = context.user_data.setdefault("cart", {})
        if not cart:
            await query.edit_message_text(
                "Корзина пуста.",
                reply_markup=cart_keyboard({}),
            )
            return
        await query.edit_message_text(
            format_customer_delivery_details(),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(
                    "⬅️ К выбору доставки",
                    callback_data="checkout",
                )],
                [InlineKeyboardButton(
                    "🛒 Вернуться в корзину",
                    callback_data="cart",
                )],
            ]),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("checkout_item_delivery:"):
        cart = context.user_data.setdefault("cart", {})
        if not cart:
            await query.edit_message_text(
                "Корзина пуста.",
                reply_markup=cart_keyboard({}),
            )
            return

        parts = data.split(":")
        if len(parts) != 3:
            await query.answer(
                "Некорректные данные кнопки доставки.",
                show_alert=True,
            )
            return

        try:
            position = int(parts[1])
        except (TypeError, ValueError):
            await query.answer("Некорректная позиция.", show_alert=True)
            return
        tariff_code = str(parts[2] or "").strip().lower()

        cart_items = list(cart.values())
        if position <= 0 or position > len(cart_items):
            await query.answer("Позиция не найдена.", show_alert=True)
            return

        item = cart_items[position - 1]
        if str(item.get("offer_source") or "usa").strip().lower() != "usa":
            await query.answer(
                "Для складской позиции доставка из США не выбирается.",
                show_alert=True,
            )
            return

        item_type = resolve_cart_item_type(item)
        allowed = ITEM_TYPE_ALLOWED_TARIFFS.get(str(item_type or ""), ())
        if not allowed:
            await query.answer(
                "Не удалось определить тип этой позиции.",
                show_alert=True,
            )
            return
        if tariff_code not in allowed:
            await query.answer(
                "Этот способ доставки недоступен для данной позиции.",
                show_alert=True,
            )
            return

        tariff = get_delivery_tariff(tariff_code)
        if not tariff or not tariff[5]:
            await query.answer(
                "Этот способ доставки сейчас недоступен.",
                show_alert=True,
            )
            return

        item["_checkout_item_type"] = item_type
        item["selected_delivery_tariff"] = tariff_code
        item["delivery_selected_at"] = (
            datetime.now().astimezone().isoformat(timespec="seconds")
        )

        await query.edit_message_text(
            format_cart(cart, checkout=True)
            + "\n\n"
            + format_checkout_delivery_choices(cart),
            parse_mode=ParseMode.HTML,
            reply_markup=checkout_delivery_keyboard(cart),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("checkout_delivery:"):
        # Backward compatibility for already rendered old checkout buttons.
        cart = context.user_data.setdefault("cart", {})
        if not cart:
            await query.edit_message_text(
                "Корзина пуста.",
                reply_markup=cart_keyboard({}),
            )
            return
        tariff_code = data.split(":", 1)[1].strip().lower()
        if tariff_code not in CUSTOMER_TARIFF_LABELS:
            await query.answer("Неизвестный способ доставки.", show_alert=True)
            return

        prepare_cart_delivery_choices(cart)
        changed = False
        now = datetime.now().astimezone().isoformat(timespec="seconds")
        for item in cart.values():
            if str(item.get("offer_source") or "usa").strip().lower() != "usa":
                continue
            item_type = str(item.get("_checkout_item_type") or "")
            if tariff_code in ITEM_TYPE_ALLOWED_TARIFFS.get(item_type, ()):
                item["selected_delivery_tariff"] = tariff_code
                item["delivery_selected_at"] = now
                changed = True

        await query.edit_message_text(
            format_cart(cart, checkout=True)
            + "\n\n"
            + format_checkout_delivery_choices(cart),
            parse_mode=ParseMode.HTML,
            reply_markup=checkout_delivery_keyboard(cart),
            disable_web_page_preview=True,
        )
        if not changed:
            await query.answer(
                "Выбери доставку отдельно для каждой позиции.",
                show_alert=True,
            )
        return

    if data == "checkout_confirm":
        cart = context.user_data.setdefault("cart", {})
        if not cart:
            await query.edit_message_text("Корзина пуста.", reply_markup=cart_keyboard({}))
            return
        # Warehouse positions require one recipient profile before order creation.
        # USA-only carts never enter this flow.
        if warehouse_recipient.has_warehouse(cart):
            recipient = context.user_data.get("warehouse_recipient") or {}
            if not warehouse_recipient.complete(recipient):
                field = warehouse_recipient.next_field(recipient)
                context.user_data["warehouse_recipient_field"] = field
                await query.edit_message_text(
                    "🇷🇺 <b>Доставка позиции со склада РФ</b>\n\n"
                    + warehouse_recipient.PROMPTS[field],
                    parse_mode=ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🛒 Вернуться в корзину", callback_data="cart")
                    ]]),
                )
                return

        changed_offers = []
        unverified_offers = []
        for key, item in list(cart.items()):
            if str(item.get("offer_source") or "usa") != "warehouse":
                continue

            validation = await _revalidate_warehouse_cart_item(item)
            target = (
                key,
                str(item.get("warehouse_public_name") or "склад"),
                str(item.get("oem") or "—"),
            )
            if validation["status"] == "changed":
                changed_offers.append(target)
            elif validation["status"] == "unverified":
                unverified_offers.append(target)

        if changed_offers or unverified_offers:
            if changed_offers:
                problem_lines = [
                    f"• {escape(name)} — <code>{escape(oem)}</code>"
                    for _, name, oem in changed_offers
                ]
                text = (
                    "⚠️ <b>Условия по части позиций изменились.</b>\n\n"
                    + "\n".join(problem_lines)
                    + "\n\nОбнови эти предложения через поиск."
                )
            else:
                problem_lines = [
                    f"• {escape(name)} — <code>{escape(oem)}</code>"
                    for _, name, oem in unverified_offers
                ]
                text = (
                    "⚠️ <b>Сейчас не удалось перепроверить склад.</b>\n\n"
                    + "\n".join(problem_lines)
                    + "\n\nКорзина сохранена. Попробуй подтвердить запрос чуть позже."
                )
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Вернуться в корзину", callback_data="cart")],
                    [InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again")],
                ]),
            )
            return

        has_usa = any(
            str(item.get("offer_source") or "usa").strip().lower() == "usa"
            for item in cart.values()
        )
        if has_usa:
            prepare_cart_delivery_choices(cart)
            if not cart_delivery_choice_complete(cart):
                await query.edit_message_text(
                    format_cart(cart, checkout=True)
                    + "\n\n"
                    + format_checkout_delivery_choices(cart),
                    parse_mode=ParseMode.HTML,
                    reply_markup=checkout_delivery_keyboard(cart),
                    disable_web_page_preview=True,
                )
                await query.answer(
                    "Сначала выбери доставку для каждой позиции из США.",
                    show_alert=True,
                )
                return
            delivery_preference = common_cart_delivery_preference(cart)
        else:
            delivery_preference = "local_only"

        if not MANAGER_CHAT_ID:
            await query.answer(
                "Не настроен чат менеджера. Запрос остался в корзине.",
                show_alert=True,
            )
            log.error("TELEGRAM_MANAGER_CHAT_ID is not set; checkout was not completed")
            return

        user = update.effective_user
        order_origin = (
            "web"
            if any(
                str(item.get("_origin") or "").lower() == "web"
                for item in cart.values()
            )
            else "telegram"
        )
        web_handoff_token = next(
            (
                str(item.get("_web_handoff_token") or "").strip()
                for item in cart.values()
                if item.get("_web_handoff_token")
            ),
            "",
        )

        try:
            order_id = generate_order_id()
        except Exception:
            log.exception("Failed to generate order ID")
            await query.answer(
                "Не удалось создать номер запроса. Корзина сохранена — попробуй ещё раз.",
                show_alert=True,
            )
            return

        try:
            await context.bot.send_message(
                chat_id=MANAGER_CHAT_ID,
                text=format_manager_order(
                    order_id,
                    user,
                    cart,
                    delivery_preference,
                    order_origin,
                ),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        except Exception:
            log.exception("Failed to send order %s to manager chat %s", order_id, MANAGER_CHAT_ID)
            await query.answer(
                "Не удалось передать запрос менеджеру. Корзина сохранена — попробуй ещё раз.",
                show_alert=True,
            )
            return

        try:
            save_order_to_history(
                order_id,
                user,
                cart,
                delivery_preference,
                origin=order_origin,
            )
        except Exception:
            log.exception("Failed to save order %s to history database", order_id)
            await query.answer(
                "Запрос передан менеджеру, но не удалось сохранить его в истории.",
                show_alert=True,
            )
            return

        if user is not None:
            for analytics_item in cart.values():
                analytics_oem = str(
                    analytics_item.get("requested_oem")
                    or analytics_item.get("oem")
                    or ""
                ).strip()
                if not analytics_oem:
                    continue
                pricing_analytics.mark_latest_matching_order(
                    ORDERS_DB_FILE,
                    source_bot="pricing",
                    telegram_user_id=int(user.id),
                    oem=analytics_oem,
                    order_id=order_id,
                )

        # Create Supplier Orders only after the client order exists.
        # One recipient is copied to each warehouse shipment; USA items are ignored by service.
        if warehouse_recipient.has_warehouse(cart):
            try:
                supplier_order_service.create_from_client_order(
                    order_id,
                    warehouse_recipient.normalize(
                        context.user_data.get("warehouse_recipient") or {}
                    ),
                )
            except Exception:
                log.exception("Failed to create Supplier Orders for %s", order_id)
                await query.answer(
                    "Не удалось подготовить складскую отправку. Корзина сохранена.",
                    show_alert=True,
                )
                return

        reservation_result = reserve_local_order_items(order_id)
        if not reservation_result.get("ok"):
            log.warning(
                "Warehouse reservation failed for order %s: %s",
                order_id,
                reservation_result,
            )
            await query.edit_message_text(
                "⚠️ <b>Не удалось зафиксировать складской остаток.</b>\n\n"
                "Корзина сохранена. Обнови проблемную складскую позицию "
                "через поиск и повтори подтверждение.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🛒 Вернуться в корзину", callback_data="cart")],
                    [InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again")],
                ]),
            )
            return

        if order_origin == "web" and web_handoff_token:
            if not web_handoff.link_handoff_order(
                web_handoff_token,
                order_id,
                int(user.id),
                ORDERS_DB_FILE,
            ):
                log.warning(
                    "WEB handoff was not linked to order %s",
                    order_id,
                )

        context.user_data["last_order"] = [dict(x) for x in cart.values()]
        context.user_data["last_order_id"] = order_id
        context.user_data["cart"] = {}
        context.user_data.pop("checkout_delivery_preference", None)
        context.user_data.pop("warehouse_recipient", None)
        context.user_data.pop("warehouse_recipient_field", None)

        auto_quote = prepare_auto_quote_after_checkout(
            order_id,
            int(user.id),
        )
        if auto_quote is not None:
            await query.edit_message_text(
                auto_quote,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(
                        "✅ Подтвердить запрос",
                        callback_data=f"clientconfirm:{order_id}",
                    )],
                    [InlineKeyboardButton(
                        "🔎 Искать ещё",
                        callback_data="search_again",
                    )],
                ]),
            )
            return

        await query.edit_message_text(
            "✅ <b>Запрос отправлен менеджеру</b>\n\n"
            f"Номер запроса: <code>{escape(order_id)}</code>\n\n"
            "Автоматический расчёт по одной или нескольким позициям требует проверки менеджера.\n\n"
            + DELIVERY_SEPARATE_NOTICE,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔎 Искать ещё", callback_data="search_again")]]
            ),
        )
        return

    if data == "change_mfg":
        context.user_data.pop("manufacturer", None)
        context.user_data.pop("last_oem", None)
        await query.edit_message_text(
            "🏭 <b>Выбери производителя:</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=manufacturer_keyboard(),
        )
        return

    if data == "search_again":
        manufacturer = context.user_data.get("manufacturer")
        last_search_was_mixed = bool(context.user_data.get("last_search_was_mixed"))

        if last_search_was_mixed or not manufacturer:
            await query.edit_message_text(
                "Отправь новый запрос.\n\n"
                "Для разных производителей укажи производителя перед OEM, например:\n"
                "<code>Ski-Doo 417300571\nArctic Cat 0746-933</code>\n\n"
                "Или выбери одного производителя кнопкой ниже и скинь несколько номеров по нему.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🏭 Выбрать производителя", callback_data="change_mfg")]]
                ),
            )
            return

        await query.edit_message_text(
            f"Производитель: <b>{escape(manufacturer)}</b>\n\n"
            "Отправь один или несколько <b>OEM / каталожных номеров</b>.\n"
            "Можно каждый номер с новой строки, через пробел или запятую.",
            parse_mode=ParseMode.HTML,
            reply_markup=change_manufacturer_keyboard(),
        )
        return

    if data == "retry_last":
        manufacturer = context.user_data.get("manufacturer")
        last_oem = context.user_data.get("last_oem")
        if not manufacturer or not last_oem:
            await query.edit_message_text(
                "Не удалось восстановить последний запрос. Выбери производителя заново:",
                reply_markup=manufacturer_keyboard(),
            )
            return

        await query.edit_message_text(
            f"🔎 Повторно ищу <code>{escape(last_oem)}</code>\n"
            f"Производитель: <b>{escape(manufacturer)}</b>",
            parse_mode=ParseMode.HTML,
        )
        try:
            result = await finder_service.search(manufacturer, last_oem)
            result = await enrich_found_result_with_dealer_price(result)
            if str(result.get("status") or "").upper() == "FOUND":
                context.user_data.setdefault("found_items", {})[_cart_item_key(result)] = result
            await query.edit_message_text(
                format_result(result),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=keyboard_for_result(result),
            )
            _schedule_client_stock_update(
                context,
                query.message,
                result,
            )
        except Exception as exc:
            log.exception("Retry failed for %s / %s", manufacturer, last_oem)
            await query.edit_message_text(
                "⚠️ <b>Каталог временно недоступен</b>\n\n"
                "Не удалось надёжно проверить номер. Можно повторить запрос ещё раз.\n\n"
                f"<code>{escape(type(exc).__name__)}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=result_keyboard(allow_retry=True),
            )
        return

    manufacturer = CALLBACK_TO_MANUFACTURER.get(data)
    if not manufacturer:
        await query.edit_message_text("Неизвестный производитель. Отправь /start.")
        return

    context.user_data["manufacturer"] = manufacturer
    context.user_data["last_search_was_mixed"] = False

    await query.edit_message_text(
        f"Выбран производитель: <b>{escape(manufacturer)}</b>\n\n"
        "Теперь отправь <b>OEM / каталожный номер</b>.\n\n"
        "Например:\n"
        "<code>417224332</code>",
        parse_mode=ParseMode.HTML,
        reply_markup=change_manufacturer_keyboard(),
    )


async def text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    raw_text = (message.text or "").strip()

    # V2.7.2 DIAGNOSTIC: log every non-command text update that reaches this handler.
    chat = update.effective_chat
    user = update.effective_user
    print("\nV2.7.2 RAW TEXT UPDATE")
    print(f"chat_type={getattr(chat, 'type', None)!r}")
    print(f"chat_id={getattr(chat, 'id', None)!r}")
    print(f"user_id={getattr(user, 'id', None)!r}")
    print(f"text={raw_text!r}")
    if _is_group_chat(update):
        print(f"group_session_chat_id={context.user_data.get('group_session_chat_id')!r}")
        print(f"group_session_until={context.user_data.get('group_session_until')!r}")
        print(f"group_session_active={_group_session_is_active(update, context)!r}")
        print(f"selected_manufacturer={context.user_data.get('manufacturer')!r}")

    # V2.7: in a group, silently ignore ordinary participant conversation.
    # A text request is accepted via explicit @mention/reply-to-bot OR while
    # this user has an active short-lived bot session in this exact group.
    if _is_group_chat(update):
        explicit, raw_text = _group_text_is_explicit_request(update, context)
        print(f"group_gate_allowed={explicit!r}; cleaned_text={raw_text!r}")
        if not explicit:
            print("V2.7.2 GROUP GATE: IGNORED")
            return
        print("V2.7.2 GROUP GATE: ACCEPTED")
        if not raw_text:
            await safe_reply_text(message, 
                "Отправь OEM-запрос или нажми /start."
            )
            return

    recipient_field = context.user_data.get("warehouse_recipient_field")
    if recipient_field:
        recipient = dict(context.user_data.get("warehouse_recipient") or {})
        value = raw_text.strip()
        if not value:
            await safe_reply_text(message, "⚠️ Поле не может быть пустым.")
            return
        if recipient_field == "phone":
            try:
                value = warehouse_recipient.normalize_phone(value)
            except ValueError:
                await safe_reply_text(
                    message,
                    "⚠️ <b>Некорректный номер телефона.</b>\n\n"
                    "Введи номер получателя в формате: <code>+79991234567</code>.",
                    parse_mode=ParseMode.HTML,
                )
                return
        recipient[recipient_field] = value
        context.user_data["warehouse_recipient"] = recipient
        field = warehouse_recipient.next_field(recipient)
        if field:
            context.user_data["warehouse_recipient_field"] = field
            await safe_reply_text(
                message,
                "🇷🇺 <b>Доставка позиции со склада РФ</b>\n\n"
                + warehouse_recipient.PROMPTS[field],
                parse_mode=ParseMode.HTML,
            )
            return
        context.user_data.pop("warehouse_recipient_field", None)
        await safe_reply_text(
            message,
            "✅ <b>Данные доставки сохранены.</b>\n\n"
            "Нажми кнопку ниже, чтобы продолжить оформление.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Продолжить оформление", callback_data="checkout_confirm")
            ]]),
        )
        return

    if user and user.id == RATE_ADMIN_USER_ID and (context.user_data.get("supplier_tracking_order_id") or context.user_data.get("supplier_problem")):
        try:
            if await supplier_telegram_handlers.handle_tracking_text(update, context, supplier_order_service, raw_text):
                return
        except (ValueError, KeyError, PermissionError) as exc:
            await safe_reply_text(message, "⚠️ " + str(exc))
            return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and warehouse_admin.is_text_mode_active(context)
    ):
        await warehouse_admin.handle_text(update, context, raw_text)
        return

    if user and user.id == RATE_ADMIN_USER_ID and context.user_data.get(
        "admin_delivery_setting_edit"
    ):
        setting_key = str(
            context.user_data.get("admin_delivery_setting_edit") or ""
        )
        if not set_delivery_text_setting(setting_key, raw_text):
            await safe_reply_text(
                message,
                "⚠️ Значение не сохранено. Отправь непустой текст до 120 символов.",
                reply_markup=admin_delivery_terms_keyboard(),
            )
            return
        context.user_data.pop("admin_delivery_setting_edit", None)
        await safe_reply_text(
            message,
            format_admin_delivery_terms()
            + "\n\n✅ <b>Сроки и условия сохранены.</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_delivery_terms_keyboard(),
        )
        return

    if user and user.id == RATE_ADMIN_USER_ID and context.user_data.get(
        "admin_delivery_divisor_edit"
    ):
        try:
            set_volume_weight_divisor(raw_text)
        except ValueError as exc:
            await safe_reply_text(message, "⚠️ " + str(exc))
            return
        context.user_data.pop("admin_delivery_divisor_edit", None)
        await safe_reply_text(
            message,
            format_admin_delivery_settings(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_delivery_keyboard(),
        )
        return

    state = context.user_data.get("admin_delivery_group_flow")
    if (
        user and user.id == RATE_ADMIN_USER_ID
        and state and state.get("measurement_stage") in {
            "actual", "length", "width", "height"
        }
    ):
        stage = state["measurement_stage"]
        try:
            value = parse_positive_delivery_number(raw_text)
        except ValueError as exc:
            await safe_reply_text(message, "⚠️ " + str(exc))
            return
        values = dict(state.get("measurement_values") or {})
        values[stage] = str(value)
        stages = ("actual", "length", "width", "height")
        index = stages.index(stage)
        if index < 3:
            state["measurement_values"] = values
            state["measurement_stage"] = stages[index + 1]
            prompts = {
                "length": "Введи длину груза в см.",
                "width": "Введи ширину груза в см.",
                "height": "Введи высоту груза в см.",
            }
            await safe_reply_text(message, prompts[stages[index + 1]])
            return
        try:
            preview = preview_admin_delivery_group(
                state["order_id"], state["measurement_group_id"], values
            )
        except ValueError as exc:
            await safe_reply_text(message, "⚠️ " + str(exc))
            return
        token = new_admin_delivery_group_token(
            context.user_data, state["order_id"],
            measurement_group_id=state["measurement_group_id"],
            measurement_stage="preview",
            measurement_values=values,
        )
        screen = format_admin_delivery_group(
            state["order_id"], state["measurement_group_id"],
            token, preview=preview,
        )
        await safe_reply_text(
            message, screen[0],
            parse_mode=ParseMode.HTML,
            reply_markup=screen[1],
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_delivery_edit")
    ):
        edit_data = context.user_data.get("admin_delivery_edit") or {}

        tariff_code = edit_data.get("tariff_code")
        field = edit_data.get("field")
        parameter = edit_data.get("parameter")

        amount_text = raw_text.strip()
        amount_text = amount_text.replace("\u00a0", "")
        amount_text = amount_text.replace(" ", "")
        amount_text = amount_text.replace("₽", "")
        amount_text = amount_text.replace("руб.", "")
        amount_text = amount_text.replace("руб", "")
        amount_text = amount_text.replace(",", ".")

        try:
            amount_rub = float(amount_text)
        except ValueError:
            await safe_reply_text(
                message,
                "⚠️ <b>Не удалось распознать тариф.</b>\n\n"
                "Отправь только стоимость в рублях за 1 кг.\n"
                "Например: <code>2700</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ К тарифу",
                            callback_data=(
                                f"admin:deliverytariff:{tariff_code}"
                            ),
                        )
                    ]
                ]),
            )
            return

        if amount_rub < 0:
            await safe_reply_text(
                message,
                "⚠️ Тариф не может быть отрицательным.\n\n"
                "Например: <code>2700</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not set_delivery_tariff_value(
            tariff_code,
            field,
            amount_rub,
        ):
            await safe_reply_text(
                message,
                "⚠️ Не удалось сохранить тариф доставки.",
            )
            return

        context.user_data.pop("admin_delivery_edit", None)

        tariff_text = format_admin_delivery_tariff(tariff_code)

        await safe_reply_text(
            message,
            (
                tariff_text
                + "\n\n✅ <b>Тариф сохранён.</b>"
                if tariff_text is not None
                else "Тариф сохранён."
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=(
                admin_delivery_tariff_keyboard(tariff_code)
                if tariff_text is not None
                else admin_delivery_keyboard()
            ),
            disable_web_page_preview=True,
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_delivery_actual_order_id")
    ):
        order_id = context.user_data.get("admin_delivery_actual_order_id")
        weight_text = raw_text.strip().replace(",", ".")

        try:
            actual_weight = float(weight_text)
        except ValueError:
            await safe_reply_text(
                message,
                "⚠️ <b>Не удалось распознать вес.</b>\n\n"
                "Отправь вес в килограммах.\n"
                "Например: <code>1</code> или <code>1.5</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if actual_weight < 0:
            await safe_reply_text(
                message,
                "⚠️ Вес не может быть отрицательным.",
            )
            return

        row = get_admin_order_delivery(order_id)

        if not row or not row[1]:
            context.user_data.pop("admin_delivery_actual_order_id", None)
            await safe_reply_text(
                message,
                "⚠️ Тариф доставки не выбран.",
            )
            return

        tariff_code = row[1]

        if tariff_code == "economy":
            if not save_admin_order_delivery_weights(
                order_id,
                actual_weight,
                None,
            ):
                await safe_reply_text(
                    message,
                    "⚠️ Не удалось рассчитать доставку.",
                )
                return

            context.user_data.pop("admin_delivery_actual_order_id", None)

            card_text = format_admin_order_delivery(order_id)

            await safe_reply_text(
                message,
                card_text or "Запрос не найден.",
                parse_mode=ParseMode.HTML,
                reply_markup=(
                    admin_order_delivery_result_keyboard(order_id)
                    if card_text is not None
                    else None
                ),
            )
            return

        context.user_data.pop("admin_delivery_actual_order_id", None)
        context.user_data["admin_delivery_volume_order_id"] = order_id
        context.user_data["admin_delivery_actual_weight"] = actual_weight

        await safe_reply_text(
            message,
            "📦 <b>Введи объёмный вес в кг.</b>\n\n"
            f"Фактический вес: <b>{actual_weight:g} кг</b>\n\n"
            "Например: <code>3</code> или <code>3.5</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К доставке",
                        callback_data=f"admin:orderdelivery:{order_id}",
                    )
                ]
            ]),
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_delivery_volume_order_id")
    ):
        order_id = context.user_data.get("admin_delivery_volume_order_id")
        weight_text = raw_text.strip().replace(",", ".")

        try:
            volume_weight = float(weight_text)
        except ValueError:
            await safe_reply_text(
                message,
                "⚠️ <b>Не удалось распознать вес.</b>\n\n"
                "Отправь объёмный вес в килограммах.\n"
                "Например: <code>3</code> или <code>3.5</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if volume_weight < 0:
            await safe_reply_text(
                message,
                "⚠️ Вес не может быть отрицательным.",
            )
            return

        actual_weight = context.user_data.get(
            "admin_delivery_actual_weight"
        )

        if actual_weight is None:
            context.user_data.pop("admin_delivery_volume_order_id", None)
            await safe_reply_text(
                message,
                "⚠️ Фактический вес не найден. Начни расчёт заново.",
            )
            return

        if not save_admin_order_delivery_weights(
            order_id,
            float(actual_weight),
            volume_weight,
        ):
            await safe_reply_text(
                message,
                "⚠️ Не удалось рассчитать доставку.",
            )
            return

        context.user_data.pop("admin_delivery_volume_order_id", None)
        context.user_data.pop("admin_delivery_actual_weight", None)

        card_text = format_admin_order_delivery(order_id)

        await safe_reply_text(
            message,
            card_text or "Запрос не найден.",
            parse_mode=ParseMode.HTML,
            reply_markup=(
                admin_order_delivery_result_keyboard(order_id)
                if card_text is not None
                else None
            ),
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_delivery_manual_order_id")
    ):
        order_id = context.user_data.get("admin_delivery_manual_order_id")

        amount_text = raw_text.strip()
        amount_text = amount_text.replace("\u00a0", "")
        amount_text = amount_text.replace(" ", "")
        amount_text = amount_text.replace("₽", "")
        amount_text = amount_text.replace("руб.", "")
        amount_text = amount_text.replace("руб", "")
        amount_text = amount_text.replace(",", ".")

        try:
            amount_rub = float(amount_text)
        except ValueError:
            await safe_reply_text(
                message,
                "⚠️ <b>Не удалось распознать сумму.</b>\n\n"
                "Отправь стоимость доставки в рублях.\n"
                "Например: <code>3500</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if amount_rub < 0:
            await safe_reply_text(
                message,
                "⚠️ Стоимость не может быть отрицательной.",
            )
            return

        if not set_admin_order_delivery_manual(
            order_id,
            amount_rub,
        ):
            await safe_reply_text(
                message,
                "⚠️ Не удалось сохранить стоимость доставки.",
            )
            return

        context.user_data.pop("admin_delivery_manual_order_id", None)

        card_text = format_admin_order_delivery(order_id)

        await safe_reply_text(
            message,
            card_text or "Запрос не найден.",
            parse_mode=ParseMode.HTML,
            reply_markup=(
                admin_order_delivery_result_keyboard(order_id)
                if card_text is not None
                else None
            ),
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_customer_total_order_id")
    ):
        order_id = context.user_data.get("admin_customer_total_order_id")

        amount_text = raw_text.strip()
        amount_text = amount_text.replace("\u00a0", "")
        amount_text = amount_text.replace(" ", "")
        amount_text = amount_text.replace("₽", "")
        amount_text = amount_text.replace("руб.", "")
        amount_text = amount_text.replace("руб", "")
        amount_text = amount_text.replace(",", ".")

        try:
            amount_rub = float(amount_text)
        except ValueError:
            await safe_reply_text(
                message,
                "⚠️ <b>Не удалось распознать сумму.</b>\n\n"
                "Отправь только сумму в рублях.\n"
                "Например: <code>35000</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "⬅️ К запросу",
                            callback_data=f"admin:order:{order_id}",
                        )
                    ]
                ]),
            )
            return

        if amount_rub < 0:
            await safe_reply_text(
                message,
                "⚠️ Сумма не может быть отрицательной.\n\n"
                "Например: <code>35000</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if not set_admin_customer_total(order_id, amount_rub):
            await safe_reply_text(
                message,
                "⚠️ Не удалось сохранить стоимость товаров.",
            )
            return

        context.user_data.pop("admin_customer_total_order_id", None)

        card_text = format_admin_order(order_id)

        await safe_reply_text(
            message,
            card_text or "Запрос не найден в истории.",
            parse_mode=ParseMode.HTML,
            reply_markup=(
                admin_order_keyboard(order_id, context=context)
                if card_text is not None
                else None
            ),
            disable_web_page_preview=True,
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_search_mode")
    ):
        search_mode = str(context.user_data.pop("admin_search_mode", "universal"))
        payload = search_admin_by_mode(search_mode, raw_text)

        if not payload.get("items"):
            # Keep the admin search route active so the next plain-text message
            # cannot fall through into the client OEM/manufacturer search.
            context.user_data["admin_search_mode"] = search_mode

            retry_labels = {
                "oem": "🧾 Повторить поиск OEM",
                "part_name": "🔤 Повторить поиск по названию",
                "customer": "👤 Повторить поиск клиента",
                "order": "📋 Повторить поиск запроса",
                "universal": "🔎 Повторить поиск",
            }
            await safe_reply_text(
                message,
                "🔎 <b>Ничего не найдено</b>\n\n"
                f"Запрос: <code>{escape(raw_text)}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            retry_labels.get(search_mode, "🔎 Повторить поиск"),
                            callback_data=f"admin:searchtype:{search_mode}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ К поиску",
                            callback_data="admin:search",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⚙️ В админку",
                            callback_data="admin:home",
                        )
                    ],
                ]),
            )
            return

        context.user_data["admin_search_payload"] = payload
        context.user_data["admin_search_page"] = 0
        context.user_data["admin_search_last_mode"] = search_mode

        # After the first typed search, keep the admin search session alive in
        # universal mode. This lets the admin paste an OEM, customer, order
        # number or part name immediately from any search results screen.
        context.user_data["admin_search_mode"] = "universal"

        await safe_reply_text(
            message,
            format_admin_search_results(payload, page=0),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_search_results_keyboard(payload, page=0),
        )
        return

    if user and user.id == RATE_ADMIN_USER_ID:
        admin_order_match = re.match(
            r"^\s*(?:📦\s*)?([A-Za-z0-9][A-Za-z0-9-]{2,63})"
            r"(?:\s*[•·]\s*.*)?\s*$",
            raw_text,
        )
        if admin_order_match:
            candidate_order_id = admin_order_match.group(1)
            with sqlite3.connect(ORDERS_DB_FILE) as conn:
                exists = conn.execute(
                    "SELECT 1 FROM orders WHERE order_id = ? LIMIT 1",
                    (candidate_order_id,),
                ).fetchone()
                if exists:
                    now = datetime.now().astimezone().isoformat(timespec="seconds")
                    conn.execute(
                        "UPDATE orders SET status = 'working', updated_at = ? "
                        "WHERE order_id = ? AND status = 'new'",
                        (now, candidate_order_id),
                    )
                    conn.commit()

            if exists:
                context.user_data.pop("admin_search_mode", None)
                context.user_data.pop("admin_search_payload", None)
                context.user_data.pop("admin_search_page", None)
                context.user_data.pop("admin_customer_total_order_id", None)

                card_text = format_admin_order(candidate_order_id)
                await safe_reply_text(
                    message,
                    card_text or "Запрос не найден в истории.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=(
                        admin_order_keyboard(candidate_order_id, context=context)
                        if card_text is not None
                        else None
                    ),
                    disable_web_page_preview=True,
                )
                return

    selected_manufacturer = context.user_data.get("manufacturer")

    # V2.4 first checks only the Telegram input for explicit manufacturer names.
    # If none are present, V2.3 behavior remains unchanged and the selected
    # manufacturer is used for every OEM.
    try:
        mixed_jobs = parse_mixed_manufacturer_batch(raw_text)
    except OverflowError:
        await safe_reply_text(message, 
            f"Слишком много номеров в одном сообщении.\n\n"
            f"Отправь не больше <b>{MAX_BATCH_OEMS}</b> OEM за один раз.",
            parse_mode=ParseMode.HTML,
        )
        return
    except ValueError:
        await safe_reply_text(message, 
            "Не удалось разобрать смешанный запрос.\n\n"
            "Укажи производителя перед номером, например:\n"
            "<code>Ski-Doo 417300571\nArctic Cat 0746-933</code>\n\n"
            "Или используй блоки:\n"
            "<code>Ski-Doo:\n417300571\n417300574\n\nArctic Cat:\n0746-933</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    mixed_mode = mixed_jobs is not None

    context.user_data["last_search_was_mixed"] = mixed_mode

    if mixed_mode:
        jobs = mixed_jobs
    else:
        try:
            inferred_oems = parse_oem_batch(raw_text)
        except (OverflowError, ValueError):
            inferred_oems = []
        if len(inferred_oems) == 1:
            identity = resolve_oem_identity(inferred_oems[0])
            inferred_manufacturer = identity.get("manufacturer")
            if inferred_manufacturer:
                selected_manufacturer = inferred_manufacturer
                context.user_data["manufacturer"] = inferred_manufacturer
        if not selected_manufacturer:
                await safe_reply_text(message, 
                    "Сначала выбери производителя или укажи производителя прямо в сообщении:\n\n"
                    "<code>Ski-Doo 417300571\nArctic Cat 0746-933</code>",
                    parse_mode=ParseMode.HTML,
                    reply_markup=manufacturer_keyboard(),
                )
                return

        try:
            oems = parse_oem_batch(raw_text)
        except OverflowError:
            await safe_reply_text(message, 
                f"Слишком много номеров в одном сообщении.\n\n"
                f"Отправь не больше <b>{MAX_BATCH_OEMS}</b> OEM за один раз.",
                parse_mode=ParseMode.HTML,
            )
            return
        except ValueError:
            await safe_reply_text(message, 
                "Не удалось распознать OEM / каталожные номера.\n\n"
                "Можно отправить один номер или несколько — каждый с новой строки, "
                "через пробел или запятую.\n\n"
                "Например:\n"
                "<code>417300571\n417300574\n417224332</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        jobs = [(selected_manufacturer, oem) for oem in oems]

    context.user_data["last_oem"] = jobs[-1][1]
    context.user_data["last_oems"] = [oem for _, oem in jobs]
    context.user_data["last_jobs"] = list(jobs)

    analytics_source_kind = "workchat" if _is_group_chat(update) else "private"
    analytics_request_ids = [
        pricing_analytics.begin_request_from_update(
            ORDERS_DB_FILE,
            source_bot="pricing",
            source_kind=analytics_source_kind,
            update=update,
            oem=analytics_oem,
            manufacturer=analytics_manufacturer,
        )
        for analytics_manufacturer, analytics_oem in jobs
    ]

    # Preserve the exact V2.3 single-item UX when this is the normal selected-
    # manufacturer mode. Explicit manufacturer input uses the common batch path.
    if len(jobs) == 1 and not mixed_mode:
        manufacturer, oem = jobs[0]
        wait_message = await safe_reply_text(message, 
            f"🔎 Ищу <code>{escape(oem)}</code>\n"
            f"Производитель: <b>{escape(manufacturer)}</b>",
            parse_mode=ParseMode.HTML,
        )

        try:
            identity = resolve_oem_identity(oem)
            result = await finder_service.search(manufacturer, oem)
            if str(result.get("status") or "").upper() != "FOUND" and identity.get("identified"):
                result = dict(result)
                result["status"] = "PARTIAL"
                result["manufacturer"] = identity.get("manufacturer") or manufacturer
                result["query_oem"] = oem
                result["oem"] = oem
                result["item_sku"] = oem
                result["name"] = identity.get("name") or result.get("name")
                result["_resolved_item_type"] = identity.get("item_type")
            result = await enrich_found_result_with_dealer_price(result)
            status = str(result.get("status") or "").upper()
            analytics_display_price = customer_rub_price_from_dp(
                result.get("_dealer_price_usd"),
                rate=load_usd_rub_rate(),
            )
            analytics_stock = [
                {
                    "warehouse": row.get("public_name"),
                    "qty": row.get("available_quantity"),
                    "price_rub": row.get("price_rub"),
                }
                for row in warehouse_stock_service.client_stock_summary(
                    _client_stock_oem(result),
                    db_file=ORDERS_DB_FILE,
                )
                if row.get("is_fresh")
                and row.get("available_quantity") is not None
                and float(row["available_quantity"]) > 0
            ]
            pricing_analytics.complete_request(
                ORDERS_DB_FILE,
                analytics_request_ids[0] if analytics_request_ids else None,
                manufacturer=manufacturer,
                result_status=status or "UNKNOWN",
                price_status=(
                    "FOUND" if analytics_display_price is not None else "UNAVAILABLE"
                ),
                display_price_amount=analytics_display_price,
                display_price_currency=(
                    "RUB" if analytics_display_price is not None else None
                ),
                stock_rf_status="FOUND" if analytics_stock else "NONE",
                stock_rf=analytics_stock,
            )
            if status in {"FOUND", "PARTIAL"}:
                cache_client_offer_result(context, result)
            await wait_message.edit_text(
                format_client_offer_card(result),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=keyboard_for_result(result),
            )
            if not _format_client_stock_message(_client_stock_oem(result)):
                _schedule_client_stock_update(
                    context,
                    message,
                    result,
                )
        except Exception as exc:
            pricing_analytics.complete_request(
                ORDERS_DB_FILE,
                analytics_request_ids[0] if analytics_request_ids else None,
                manufacturer=manufacturer,
                result_status="ERROR",
                price_status="UNAVAILABLE",
                stock_rf_status="UNKNOWN",
            )
            log.exception("Search failed for %s / %s", manufacturer, oem)
            await wait_message.edit_text(
                "⚠️ <b>Ошибка поиска</b>\n\n"
                "Каталог сейчас не удалось проверить. "
                "Попробуй повторить запрос через несколько секунд.\n\n"
                f"<code>{escape(type(exc).__name__)}</code>",
                parse_mode=ParseMode.HTML,
                reply_markup=result_keyboard(allow_retry=True),
            )
        return

    unique_manufacturers = {manufacturer for manufacturer, _ in jobs}

    if len(unique_manufacturers) == 1:
        mode_text = escape(next(iter(unique_manufacturers)))
    else:
        mode_text = "разных производителей"

    count = len(jobs)

    if count % 10 == 1 and count % 100 != 11:
        number_word = "номер"
    elif count % 10 in (2, 3, 4) and count % 100 not in (12, 13, 14):
        number_word = "номера"
    else:
        number_word = "номеров"

    first_manufacturer, first_oem = jobs[0]
    progress = await safe_reply_text(
        message,
        f"🔎 <b>Проверяю {count} {number_word}</b>\n"
        f"Режим: <b>{mode_text}</b>\n\n"
        f"1/{count}: <b>{escape(first_manufacturer)}</b> — <code>{escape(first_oem)}</code>",
        parse_mode=ParseMode.HTML,
    )

    batch_results = []
    for index, (manufacturer, oem) in enumerate(jobs, start=1):
        if index > 1:
            await progress.edit_text(
                f"🔎 <b>Проверяю {count} {number_word}</b>\n"
                f"Режим: <b>{mode_text}</b>\n\n"
                f"{index}/{count}: <b>{escape(manufacturer)}</b> — <code>{escape(oem)}</code>",
                parse_mode=ParseMode.HTML,
            )

        try:
            result = await finder_service.search(manufacturer, oem)
            result = await enrich_found_result_with_dealer_price(result)
            analytics_status = str(result.get("status") or "").upper()
            analytics_display_price = customer_rub_price_from_dp(
                result.get("_dealer_price_usd"),
                rate=load_usd_rub_rate(),
            )
            analytics_stock = [
                {
                    "warehouse": row.get("public_name"),
                    "qty": row.get("available_quantity"),
                    "price_rub": row.get("price_rub"),
                }
                for row in warehouse_stock_service.client_stock_summary(
                    _client_stock_oem(result),
                    db_file=ORDERS_DB_FILE,
                )
                if row.get("is_fresh")
                and row.get("available_quantity") is not None
                and float(row["available_quantity"]) > 0
            ]
            pricing_analytics.complete_request(
                ORDERS_DB_FILE,
                (
                    analytics_request_ids[index - 1]
                    if index - 1 < len(analytics_request_ids)
                    else None
                ),
                manufacturer=manufacturer,
                result_status=analytics_status or "UNKNOWN",
                price_status=(
                    "FOUND" if analytics_display_price is not None else "UNAVAILABLE"
                ),
                display_price_amount=analytics_display_price,
                display_price_currency=(
                    "RUB" if analytics_display_price is not None else None
                ),
                stock_rf_status="FOUND" if analytics_stock else "NONE",
                stock_rf=analytics_stock,
            )
            batch_results.append({
                "manufacturer": manufacturer,
                "oem": oem,
                "result": result,
                "exception": None,
            })
        except Exception as exc:
            pricing_analytics.complete_request(
                ORDERS_DB_FILE,
                (
                    analytics_request_ids[index - 1]
                    if index - 1 < len(analytics_request_ids)
                    else None
                ),
                manufacturer=manufacturer,
                result_status="ERROR",
                price_status="UNAVAILABLE",
                stock_rf_status="UNKNOWN",
            )
            log.exception("Batch search failed for %s / %s", manufacturer, oem)
            batch_results.append({
                "manufacturer": manufacturer,
                "oem": oem,
                "result": None,
                "exception": exc,
            })

    context.user_data["last_batch"] = batch_results

    await progress.edit_text(
        f"✅ <b>Проверка завершена</b>\n\n{escape(batch_summary(batch_results))}",
        parse_mode=ParseMode.HTML,
    )

    for entry in batch_results:
        manufacturer = entry["manufacturer"]
        oem = entry["oem"]
        exc = entry["exception"]
        result = entry["result"]

        if exc is not None:
            text = (
                "⚠️ <b>Ошибка поиска</b>\n\n"
                f"<b>Производитель:</b> {escape(manufacturer)}\n"
                f"<b>Запрос:</b> <code>{escape(oem)}</code>\n\n"
                "Каталог сейчас не удалось надёжно проверить.\n\n"
                f"<code>{escape(type(exc).__name__)}</code>"
            )
        else:
            text = format_client_offer_card(result)
            if str(result.get("status") or "").upper() in {"FOUND", "PARTIAL"}:
                cache_client_offer_result(context, result)

        await safe_reply_text(
            message,
            text,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
            reply_markup=(
                keyboard_for_result(result)
                if exc is None
                else None
            ),
        )
        if (
            exc is None
            and str(result.get("status") or "").upper() == "FOUND"
            and not _format_client_stock_message(_client_stock_oem(result))
        ):
            _schedule_client_stock_update(
                context,
                message,
                result,
            )

    await safe_reply_text(message, 
        "Что дальше?",
        reply_markup=(mixed_result_keyboard() if mixed_mode else result_keyboard(allow_retry=False)),
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await safe_reply_text(update.effective_message, 
        "Как пользоваться ботом:\n\n"
        "• Для одного производителя выбери его кнопкой и отправь один или несколько OEM-номеров.\n"
        "• Для разных производителей укажи производителя перед каждым OEM.\n"
        "  Например:\n"
        "  Ski-Doo 417300571\n"
        "  Arctic Cat 0746-933\n\n"
        "Найденные позиции можно добавить в корзину и оформить запрос.",
    )


async def rate_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or change USD/RUB customer rate. Manager only."""
    global USD_RUB_RATE

    user = update.effective_user
    message = update.effective_message

    if not user or user.id != RATE_ADMIN_USER_ID:
        return

    if not context.args:
        await safe_reply_text(
            message,
            f"💱 <b>Текущий курс:</b> {USD_RUB_RATE:g} ₽/$\n\n"
            "Чтобы изменить курс, отправь:\n"
            "<code>/rate 107.5</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_value = context.args[0].strip().replace(",", ".")

    try:
        new_rate = float(raw_value)
    except ValueError:
        await safe_reply_text(
            message,
            "⚠️ Не понял курс.\n\n"
            "Например: <code>/rate 107.5</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not 1 <= new_rate <= 1000:
        await safe_reply_text(
            message,
            "⚠️ Курс должен быть числом от 1 до 1000.",
        )
        return

    try:
        save_usd_rub_rate(new_rate)
    except OSError:
        log.exception("Could not save USD/RUB rate")
        await safe_reply_text(
            message,
            "⚠️ Не удалось сохранить курс. Курс не изменён.",
        )
        return

    USD_RUB_RATE = new_rate

    log.info(
        "USD/RUB customer rate changed by manager %s to %s",
        user.id,
        f"{USD_RUB_RATE:g}",
    )

    await safe_reply_text(
        message,
        f"✅ <b>Курс обновлён:</b> {USD_RUB_RATE:g} ₽/$\n"
        "Новые расчёты выполняются по новому курсу.",
        parse_mode=ParseMode.HTML,
    )


async def coefficient_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show or change DP customer-price coefficient. Manager only."""
    global PRICE_COEFFICIENT

    user = update.effective_user
    message = update.effective_message

    if not user or user.id != RATE_ADMIN_USER_ID:
        return

    if not context.args:
        await safe_reply_text(
            message,
            f"🧮 <b>Текущий коэффициент DP:</b> {PRICE_COEFFICIENT:g}\n\n"
            "Формула: <code>DP × коэффициент × курс</code>\n\n"
            "Чтобы изменить коэффициент, отправь:\n"
            "<code>/coef 1.34</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    raw_value = context.args[0].strip().replace(",", ".")
    try:
        new_value = float(raw_value)
    except ValueError:
        await safe_reply_text(
            message,
            "⚠️ Не понял коэффициент.\n\n"
            "Например: <code>/coef 1.34</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    if not 0.01 <= new_value <= 100:
        await safe_reply_text(
            message,
            "⚠️ Коэффициент должен быть числом от 0.01 до 100.",
        )
        return

    try:
        save_price_coefficient(new_value)
    except OSError:
        log.exception("Could not save DP price coefficient")
        await safe_reply_text(
            message,
            "⚠️ Не удалось сохранить коэффициент. Значение не изменено.",
        )
        return

    PRICE_COEFFICIENT = new_value
    log.info(
        "DP price coefficient changed by manager %s to %s",
        user.id,
        f"{PRICE_COEFFICIENT:g}",
    )
    await safe_reply_text(
        message,
        f"✅ <b>Коэффициент DP обновлён:</b> {PRICE_COEFFICIENT:g}\n"
        "Новые расчёты выполняются по новому коэффициенту.",
        parse_mode=ParseMode.HTML,
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    log.error(
        "Telegram update error",
        exc_info=(
            type(context.error),
            context.error,
            context.error.__traceback__,
        ) if context.error else None,
    )


# ---------------------------------------------------------------------------
# STARTUP / SHUTDOWN
# ---------------------------------------------------------------------------

async def post_init(application: Application):
    # Admin/UI test must stay available even if the attached Chrome CDP session
    # temporarily refuses Playwright. Real DCP search remains unchanged.
    try:
        await asyncio.wait_for(finder_service.start(), timeout=8.0)
        print("V6.6 SEARCH ENGINE: CONNECTED")
    except Exception as exc:
        print("V6.6 SEARCH ENGINE: OFFLINE FOR THIS TEST RUN:", type(exc).__name__, exc)
        print("Telegram admin/UI continues without DCP search.")
    print("TELEGRAM BOT V2.7.3 IS RUNNING")
    print("V6.6 search/parser path: UNCHANGED")
    print("False NOT_FOUND technical guard: ENABLED")
    print("V2.5.1 Chrome/Cloudflare session recovery + one automatic retry: ENABLED")
    print("V2.2 customer UI: PRESERVED")
    print("V2.3 multi-OEM batch input: PRESERVED")
    print("V2.4.1 mixed-manufacturer follow-up UX: PRESERVED")
    print("V2.5 cart / quantity / checkout: PRESERVED")
    print("V2.6 manager order handoff: ENABLED")
    print("V2.6.1 two-mode start screen UX fixes: ENABLED")
    print(f"V2.7.3 customer USD/RUB rate: {USD_RUB_RATE:g} RUB per USD (USD hidden from customer UI)")
    print(f"UPS3 DP coefficient: {PRICE_COEFFICIENT:g}")
    print("V2.7.1 group quiet-mode user session: PRESERVED (15 min, per group)\nV2.7.3 RUB customer prices + flood-control retry: ENABLED")
    application.bot_data["warehouse_refresh_task"] = asyncio.create_task(
        warehouse_refresh_scheduler.run_refresh_loop(
            db_file=ORDERS_DB_FILE,
            log=log,
        )
    )
    print("WAREHOUSE STOCK REFRESH SCHEDULER: ENABLED")
    print("Open Telegram and send /start to the bot.")
    print("Press Ctrl+C here to stop the bot.")


async def post_shutdown(application: Application):
    refresh_task = application.bot_data.get("warehouse_refresh_task")
    if refresh_task:
        refresh_task.cancel()
        try:
            await refresh_task
        except asyncio.CancelledError:
            pass
    await finder_service.stop()


def main():
    global USD_RUB_RATE, PRICE_COEFFICIENT
    USD_RUB_RATE = load_usd_rub_rate()
    PRICE_COEFFICIENT = load_price_coefficient()
    init_orders_db()
    pricing_analytics.init_analytics(ORDERS_DB_FILE)
    warehouse_admin.configure(
        ORDERS_DB_FILE,
        RATE_ADMIN_USER_ID,
        safe_reply_text,
        log,
    )
    warehouse_store.seed_initial_warehouses(ORDERS_DB_FILE)

    if not TOKEN:
        raise SystemExit(
            "EXTREMIZER_BOT_TOKEN is not set.\n"
            "In CMD run:\n"
            "set EXTREMIZER_BOT_TOKEN=YOUR_BOT_TOKEN\n"
            "py extremizer_bot.py"
        )

    if USD_RUB_RATE <= 0:
        raise SystemExit(
            "EXTREMIZER_USD_RUB_RATE is not set.\n"
            "In CMD run, for example:\n"
            "set EXTREMIZER_USD_RUB_RATE=95\n"
            "py extremizer_bot.py"
        )

    if PRICE_COEFFICIENT <= 0:
        raise SystemExit(
            "EXTREMIZER_PRICE_COEFFICIENT must be greater than zero."
        )

    application = (
        Application.builder()
        .token(TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rate", rate_command))
    application.add_handler(CommandHandler("coef", coefficient_command))
    application.add_handler(
        CallbackQueryHandler(
            warehouse_admin.handle_file_callback_nonblocking,
            pattern=(
                r"^admin:(?:warehouses$|warehouse:.*|stock.*)$"
            ),
            block=False,
        )
    )
    application.add_handler(CallbackQueryHandler(manufacturer_callback))
    application.add_handler(
        MessageHandler(
            filters.Document.ALL,
            warehouse_admin.admin_document_message,
            block=False,
        )
    )
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text_message)
    )
    application.add_error_handler(error_handler)

    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
