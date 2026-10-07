#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Extremizer = Пробник (@ExtremizerBOT).

Минимальный клиентский бот:
- цена позиции из США в RUB по той же формуле, что основной бот;
- актуальные остатки активных складов РФ;
- без корзины, заказа, доставки, админки и дилерских функций.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import math
import os
import sqlite3
from html import escape
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

import dealercostparts_manufacturer_finder_v6_6 as finder
import warehouse_stock_service
import dp_live_bridge
import pricing_analytics

TOKEN = os.getenv("PROBNIK_BOT_TOKEN", "").strip()
ORDERS_DB = Path(
    os.getenv("EXTREMIZER_ORDERS_DB_FILE", "").strip()
    or Path(__file__).with_name("extremizer_orders.db")
)
DP_MAX_AGE_HOURS = float(os.getenv("EXTREMIZER_DP_CACHE_MAX_AGE_HOURS", "168") or "168")
PRICE_COEFFICIENT = float(os.getenv("EXTREMIZER_PRICE_COEFFICIENT", "1.34") or "1.34")
USD_RUB_RATE = float((os.getenv("EXTREMIZER_USD_RUB_RATE", "0") or "0").replace(",", "."))

DELIVERY_NOTICE = (
    "🚚 Доставка из США не входит в стоимость товаров и оплачивается отдельно."
)

WELCOME = (
    "👋 <b>EXTREMIZER — ПРОБНИК</b>\n\n"
    "Отправь каталожный номер одним сообщением.\n"
    "Я покажу:\n"
    "🇺🇸 цену со склада США (доставка в РФ оплачивается отдельно)\n"
    "🇷🇺 цены и наличие на складах РФ, если твой номер есть в наличии"
)


def normalize_oem(value: str | None) -> str:
    return finder.normalize_oem(str(value or ""))


def _format_rub(value: int | float) -> str:
    return f"{int(round(float(value))):,}".replace(",", " ") + " ₽"


def _customer_price_rub(dp_usd: float) -> int | None:
    if dp_usd <= 0 or PRICE_COEFFICIENT <= 0 or USD_RUB_RATE <= 0:
        return None
    raw = dp_usd * PRICE_COEFFICIENT * USD_RUB_RATE
    return int(math.ceil(raw / 100.0) * 100)


def _db_tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}


def init_probnik_identity() -> None:
    """Create/seed verified OEM identity independent of MSRP or DP."""
    if not ORDERS_DB.exists():
        return
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    seeds = [
        (
            "Ski-Doo",
            "860200783",
            "860200783",
            "QRS Tech Link (REV-XM, XS, XP, except 550 Fan and 4-stroke engines)",
            "verified_public_catalog",
            "micpartsonline.com + outdoorworld.kz",
        ),
        (
            "Arctic Cat",
            "3006-682",
            "3006-682",
            "Controller, Injection",
            "verified_dcp_identity",
            "DCP exact OEM row",
        ),
    ]
    with sqlite3.connect(ORDERS_DB) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS probnik_oem_identity (
                   manufacturer TEXT NOT NULL,
                   oem TEXT NOT NULL,
                   current_oem TEXT NOT NULL,
                   name TEXT,
                   verified INTEGER NOT NULL DEFAULT 0,
                   source_kind TEXT,
                   source_ref TEXT,
                   first_seen_at TEXT NOT NULL,
                   last_verified_at TEXT NOT NULL,
                   PRIMARY KEY(manufacturer,oem)
               )"""
        )
        for manufacturer, oem, current_oem, name, source_kind, source_ref in seeds:
            conn.execute(
                """INSERT INTO probnik_oem_identity(
                       manufacturer,oem,current_oem,name,verified,
                       source_kind,source_ref,first_seen_at,last_verified_at
                   ) VALUES(?,?,?,?,1,?,?,?,?)
                   ON CONFLICT(manufacturer,oem) DO UPDATE SET
                       current_oem=excluded.current_oem,
                       name=excluded.name,
                       verified=1,
                       source_kind=excluded.source_kind,
                       source_ref=excluded.source_ref,
                       last_verified_at=excluded.last_verified_at""",
                (
                    manufacturer, oem, current_oem, name,
                    source_kind, source_ref, now, now,
                ),
            )


def _identity_and_price_legacy(oem: str) -> dict:
    """Read only confirmed production data; never guess a manufacturer."""
    result = {
        "oem": oem,
        "current_oem": oem,
        "manufacturer": None,
        "name": None,
        "dp_usd": None,
        "msrp_usd": None,
        "rrp_rub": None,
        "customer_rub": None,
        "price_fresh": False,
    }
    if not ORDERS_DB.exists():
        return result

    with sqlite3.connect(ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        tables = _db_tables(conn)
        candidates: set[str] = set()
        alias_rows = []

        if "oem_catalog_aliases" in tables and "oem_catalog_cache" in tables:
            alias_rows = conn.execute(
                """SELECT a.manufacturer,a.current_oem,c.name,c.msrp_usd,c.last_verified_at
                     FROM oem_catalog_aliases AS a
                     JOIN oem_catalog_cache AS c
                       ON c.manufacturer=a.manufacturer
                      AND c.current_oem=a.current_oem
                    WHERE a.alias_oem=?
                      AND c.msrp_verified=1
                      AND c.msrp_usd IS NOT NULL
                      AND c.msrp_usd>0
                    ORDER BY c.last_verified_at DESC""",
                (oem,),
            ).fetchall()
            for row in alias_rows:
                canonical = finder.manufacturer_alias(str(row["manufacturer"] or ""))
                if canonical:
                    candidates.add(canonical)

        if "dealer_price_cache" in tables:
            for row in conn.execute(
                "SELECT DISTINCT manufacturer FROM dealer_price_cache WHERE oem=?",
                (oem,),
            ):
                canonical = finder.manufacturer_alias(str(row[0] or ""))
                if canonical:
                    candidates.add(canonical)

        if "warehouse_stock_current" in tables:
            if not result["name"]:
                row = conn.execute(
                    """SELECT name FROM warehouse_stock_current
                       WHERE oem=? AND name IS NOT NULL AND TRIM(name)<>''
                       ORDER BY observed_at DESC LIMIT 1""",
                    (oem,),
                ).fetchone()
                if row and row["name"]:
                    result["name"] = str(row["name"]).strip()
            for row in conn.execute(
                """SELECT DISTINCT manufacturer,name FROM warehouse_stock_current
                   WHERE oem=? AND manufacturer IS NOT NULL AND TRIM(manufacturer)<>''""",
                (oem,),
            ):
                raw = str(row["manufacturer"] or "").strip()
                canonical = finder.manufacturer_alias(raw)
                if not canonical and raw.upper() == "BRP":
                    name = str(row["name"] or "").lower()
                    if "ski-doo" in name or "ski doo" in name:
                        canonical = "Ski-Doo"
                    elif "sea-doo" in name or "sea doo" in name:
                        canonical = "Sea-Doo"
                    elif "can-am" in name or "can am" in name:
                        canonical = "Can-Am"
                if canonical:
                    candidates.add(canonical)

        if len(candidates) != 1:
            return result

        manufacturer = next(iter(candidates))
        result["manufacturer"] = manufacturer

        catalog_row = next(
            (
                row for row in alias_rows
                if (finder.manufacturer_alias(str(row["manufacturer"] or "")) or "")
                == manufacturer
            ),
            None,
        )
        if catalog_row:
            result["current_oem"] = str(catalog_row["current_oem"] or oem)
            if catalog_row["name"]:
                result["name"] = str(catalog_row["name"]).strip()
            result["msrp_usd"] = float(catalog_row["msrp_usd"])
            result["rrp_rub"] = int(
                round((result["msrp_usd"] * USD_RUB_RATE) / 50.0) * 50
            )

        if "dealer_price_cache" not in tables:
            return result

        price_oems = [oem]
        current_oem = str(result.get("current_oem") or oem)
        if current_oem not in price_oems:
            price_oems.append(current_oem)

        row = None
        for price_oem in price_oems:
            row = conn.execute(
                """SELECT dealer_price_usd,last_verified_at
                     FROM dealer_price_cache
                    WHERE manufacturer=? AND oem=?""",
                (manufacturer, price_oem),
            ).fetchone()
            if row and row["dealer_price_usd"] is not None:
                break

        if not row or row["dealer_price_usd"] is None:
            return result

        fresh = False
        try:
            from datetime import datetime
            verified = datetime.fromisoformat(str(row["last_verified_at"]))
            now = datetime.now().astimezone()
            if verified.tzinfo is None:
                verified = verified.replace(tzinfo=now.tzinfo)
            age = max(
                0.0,
                (now - verified.astimezone(now.tzinfo)).total_seconds() / 3600.0,
            )
            fresh = DP_MAX_AGE_HOURS > 0 and age <= DP_MAX_AGE_HOURS
        except Exception:
            fresh = False

        if fresh:
            result["dp_usd"] = float(row["dealer_price_usd"])
            result["customer_rub"] = _customer_price_rub(result["dp_usd"])
            result["price_fresh"] = True

    return result


def _identity_and_price(oem: str) -> dict:
    """Resolve customer pricing only from verified public catalog + fresh DP cache.

    Safe order:
    requested OEM -> verified alias/current OEM -> unique manufacturer
    -> fresh DP cache on requested/current/equivalent OEM.

    MSRP is used only for RRP. It is never substituted for DP.
    """
    result = {
        "oem": oem,
        "current_oem": oem,
        "manufacturer": None,
        "name": None,
        "dp_usd": None,
        "msrp_usd": None,
        "rrp_rub": None,
        "customer_rub": None,
        "price_fresh": False,
    }
    if not ORDERS_DB.exists():
        return result

    def canonical(raw: object) -> str:
        return finder.manufacturer_alias(str(raw or "").strip()) or str(raw or "").strip()

    def fresh_dp(row: sqlite3.Row | None) -> bool:
        if not row or row["dealer_price_usd"] is None:
            return False
        try:
            from datetime import datetime
            verified = datetime.fromisoformat(str(row["last_verified_at"]))
            now = datetime.now().astimezone()
            if verified.tzinfo is None:
                verified = verified.replace(tzinfo=now.tzinfo)
            age = max(
                0.0,
                (now - verified.astimezone(now.tzinfo)).total_seconds() / 3600.0,
            )
            return DP_MAX_AGE_HOURS > 0 and age <= DP_MAX_AGE_HOURS
        except Exception:
            return False

    with sqlite3.connect(ORDERS_DB) as conn:
        conn.row_factory = sqlite3.Row
        tables = _db_tables(conn)

        equivalent_oems: list[str] = [oem]
        verified_catalog_rows: list[sqlite3.Row] = []

        if "oem_catalog_cache" in tables:
            if "oem_catalog_aliases" in tables:
                verified_catalog_rows = conn.execute(
                    """SELECT DISTINCT
                              c.manufacturer,
                              c.current_oem,
                              c.name,
                              c.msrp_usd,
                              c.last_verified_at
                         FROM oem_catalog_cache AS c
                         JOIN oem_catalog_aliases AS a
                           ON a.manufacturer=c.manufacturer
                          AND a.current_oem=c.current_oem
                        WHERE c.msrp_verified=1
                          AND c.msrp_usd IS NOT NULL
                          AND c.msrp_usd>0
                          AND (a.alias_oem=? OR c.current_oem=?)
                        ORDER BY c.last_verified_at DESC""",
                    (oem, oem),
                ).fetchall()
            if not verified_catalog_rows:
                verified_catalog_rows = conn.execute(
                    """SELECT manufacturer,current_oem,name,msrp_usd,last_verified_at
                         FROM oem_catalog_cache
                        WHERE current_oem=?
                          AND msrp_verified=1
                          AND msrp_usd IS NOT NULL
                          AND msrp_usd>0
                        ORDER BY last_verified_at DESC""",
                    (oem,),
                ).fetchall()

        identity_rows: list[sqlite3.Row] = []
        if "probnik_oem_identity" in tables:
            identity_rows = conn.execute(
                """SELECT manufacturer,oem,current_oem,name,last_verified_at
                     FROM probnik_oem_identity
                    WHERE verified=1
                      AND (oem=? OR current_oem=?)
                    ORDER BY last_verified_at DESC""",
                (oem, oem),
            ).fetchall()

        identity_manufacturers = {
            canonical(row["manufacturer"])
            for row in identity_rows
            if canonical(row["manufacturer"])
        }
        catalog_manufacturers = {
            canonical(row["manufacturer"])
            for row in verified_catalog_rows
            if canonical(row["manufacturer"])
        }
        confirmed_manufacturers = identity_manufacturers | catalog_manufacturers
        if len(confirmed_manufacturers) > 1:
            return result

        manufacturer = next(iter(confirmed_manufacturers), None)

        identity_row = None
        if manufacturer:
            identity_row = next(
                (
                    row for row in identity_rows
                    if canonical(row["manufacturer"]) == manufacturer
                ),
                None,
            )
            if identity_row:
                current_oem = normalize_oem(
                    str(identity_row["current_oem"] or identity_row["oem"] or "")
                ) or oem
                result["current_oem"] = current_oem
                if current_oem not in equivalent_oems:
                    equivalent_oems.append(current_oem)
                identity_oem = normalize_oem(str(identity_row["oem"] or ""))
                if identity_oem and identity_oem not in equivalent_oems:
                    equivalent_oems.append(identity_oem)
                if identity_row["name"]:
                    result["name"] = str(identity_row["name"]).strip()

        catalog_row = None
        if manufacturer:
            catalog_row = next(
                (
                    row for row in verified_catalog_rows
                    if canonical(row["manufacturer"]) == manufacturer
                ),
                None,
            )
            if catalog_row:
                current_oem = normalize_oem(str(catalog_row["current_oem"] or "")) or oem
                result["current_oem"] = current_oem
                if current_oem not in equivalent_oems:
                    equivalent_oems.append(current_oem)
                if catalog_row["name"]:
                    result["name"] = str(catalog_row["name"]).strip()
                result["msrp_usd"] = float(catalog_row["msrp_usd"])
                result["rrp_rub"] = int(
                    round((result["msrp_usd"] * USD_RUB_RATE) / 50.0) * 50
                )

                if "oem_catalog_aliases" in tables:
                    for row in conn.execute(
                        """SELECT alias_oem
                             FROM oem_catalog_aliases
                            WHERE manufacturer=? AND current_oem=?""",
                        (str(catalog_row["manufacturer"]), current_oem),
                    ):
                        alias = normalize_oem(str(row[0] or ""))
                        if alias and alias not in equivalent_oems:
                            equivalent_oems.append(alias)

        # If verified catalog identity is unavailable, infer only from trusted
        # existing data and require a single unambiguous manufacturer.
        if not manufacturer:
            candidates: set[str] = set()

            if "dealer_price_cache" in tables:
                placeholders = ",".join("?" for _ in equivalent_oems)
                for row in conn.execute(
                    f"""SELECT DISTINCT manufacturer
                          FROM dealer_price_cache
                         WHERE oem IN ({placeholders})""",
                    tuple(equivalent_oems),
                ):
                    value = canonical(row[0])
                    if value:
                        candidates.add(value)

            if "warehouse_stock_current" in tables:
                for row in conn.execute(
                    """SELECT DISTINCT manufacturer,name
                         FROM warehouse_stock_current
                        WHERE oem=?
                          AND manufacturer IS NOT NULL
                          AND TRIM(manufacturer)<>''""",
                    (oem,),
                ):
                    raw = str(row["manufacturer"] or "").strip()
                    value = canonical(raw)
                    name = str(row["name"] or "").lower()
                    if raw.upper() == "BRP":
                        if "ski-doo" in name or "ski doo" in name:
                            value = "Ski-Doo"
                        elif "sea-doo" in name or "sea doo" in name:
                            value = "Sea-Doo"
                        elif "can-am" in name or "can am" in name:
                            value = "Can-Am"
                    if value:
                        candidates.add(value)
                    if not result["name"] and row["name"]:
                        result["name"] = str(row["name"]).strip()

            if len(candidates) != 1:
                # NEW OEM: ask the trusted local DCP agent to discover identity.
                # The cloud never guesses manufacturer and MSRP is never used as DP.
                live = dp_live_bridge.request_live_dp(
                    ORDERS_DB, "__AUTO__", oem, wait_seconds=10.0,
                )
                live_manufacturer = finder.manufacturer_alias(
                    str(live.get("manufacturer") or "").strip()
                )
                if (
                    str(live.get("status") or "").upper() == "FOUND"
                    and live_manufacturer
                    and isinstance(live.get("dealer_price_usd"), (int, float))
                    and float(live["dealer_price_usd"]) > 0
                ):
                    result["manufacturer"] = live_manufacturer
                    result["dp_usd"] = float(live["dealer_price_usd"])
                    result["customer_rub"] = _customer_price_rub(result["dp_usd"])
                    result["price_fresh"] = True
                    result["dp_oem"] = normalize_oem(
                        str(live.get("current_oem") or "")
                    ) or oem
                    if live.get("name"):
                        result["name"] = str(live["name"]).strip()
                return result
            manufacturer = next(iter(candidates))

        result["manufacturer"] = manufacturer

        if "dealer_price_cache" not in tables:
            return result

        # Search DP across the exact requested OEM, its verified current OEM,
        # and verified aliases. Prefer freshest valid DP, never MSRP.
        placeholders = ",".join("?" for _ in equivalent_oems)
        rows = conn.execute(
            f"""SELECT oem,dealer_price_usd,last_verified_at
                  FROM dealer_price_cache
                 WHERE manufacturer=?
                   AND oem IN ({placeholders})
                 ORDER BY last_verified_at DESC""",
            (manufacturer, *equivalent_oems),
        ).fetchall()

        dp_row = next((row for row in rows if fresh_dp(row)), None)
        if not dp_row:
            # CACHE-FIRST: only a real cache miss is allowed to request live DP.
            # Railway never talks to the user's Chrome directly. A trusted local
            # agent claims this short-lived request, verifies DCP, and returns
            # only the result. Any timeout/auth/Cloudflare/technical failure
            # falls through to the already-approved "price unavailable" UI.
            live = dp_live_bridge.request_live_dp(
                ORDERS_DB,
                manufacturer,
                oem,
                wait_seconds=10.0,
            )
            if (
                str(live.get("status") or "").upper() == "FOUND"
                and isinstance(live.get("dealer_price_usd"), (int, float))
                and float(live["dealer_price_usd"]) > 0
            ):
                result["dp_usd"] = float(live["dealer_price_usd"])
                result["customer_rub"] = _customer_price_rub(result["dp_usd"])
                result["price_fresh"] = True
                result["dp_oem"] = (
                    normalize_oem(str(live.get("current_oem") or "")) or oem
                )
                if live.get("name") and not result.get("name"):
                    result["name"] = str(live["name"]).strip()
            return result

        result["dp_usd"] = float(dp_row["dealer_price_usd"])
        result["customer_rub"] = _customer_price_rub(result["dp_usd"])
        result["price_fresh"] = True
        result["dp_oem"] = str(dp_row["oem"])
        return result


def _stock_rows(oem: str) -> list[dict]:
    return warehouse_stock_service.client_stock_summary(oem, db_file=ORDERS_DB)


async def _stock_with_refresh(oem: str) -> list[dict]:
    rows = await asyncio.to_thread(_stock_rows, oem)
    all_fresh = bool(rows) and all(bool(r.get("is_fresh")) for r in rows)
    if not all_fresh:
        await asyncio.to_thread(
            warehouse_stock_service.refresh_all_active_warehouses,
            oem,
            ORDERS_DB,
        )
        rows = await asyncio.to_thread(_stock_rows, oem)

    # Migration guard for YARS: old fresh snapshots were created before the
    # adapter learned to persist the public product price. A positive YARS
    # stock row without price is therefore not final; refresh that warehouse
    # immediately instead of waiting for the 30-minute website TTL.
    yars_missing_price = next(
        (
            row for row in rows
            if str(row.get("public_name") or "") == "склад ЯРС"
            and row.get("is_fresh")
            and row.get("available_quantity") is not None
            and float(row["available_quantity"]) > 0
            and row.get("price_rub") is None
        ),
        None,
    )
    if yars_missing_price:
        await asyncio.to_thread(
            warehouse_stock_service.refresh_warehouse_oem,
            int(yars_missing_price["warehouse_id"]),
            oem,
            ORDERS_DB,
        )
        rows = await asyncio.to_thread(_stock_rows, oem)
    return rows


def _compose(oem: str, info: dict, rows: list[dict]) -> str:
    lines = [f"🔎 <b>OEM:</b> <code>{escape(oem)}</code>"]
    if info.get("name"):
        lines.append(escape(str(info["name"])))
    lines.extend(["", "🇺🇸 <b>склад США—</b>"])

    customer_rub = info.get("customer_rub")
    rrp_rub = info.get("rrp_rub")
    if customer_rub is not None:
        customer_price = _format_rub(customer_rub).replace(" ₽", "* ₽")
        lines.append(f"Ваша цена: <b>{customer_price}</b>")
        if rrp_rub is not None and rrp_rub > customer_rub:
            lines.append(f"РРЦ: <b>{_format_rub(rrp_rub)}</b>")
            benefit_pct = (rrp_rub - customer_rub) / rrp_rub * 100
            lines.append(f"Ваша выгода: <b>{benefit_pct:.1f}%</b>")
        delivery_line = "<b>* -</b> в цену не входит стоимость доставки из штатов 🚚"
    else:
        lines.append("Цена сейчас недоступна. Попробуйте повторить запрос позже.")
        delivery_line = "🚚 Доставка в РФ оплачивается отдельно."

    lines.extend(["", delivery_line, "", "🇷🇺 <b>Наличие в РФ:</b>"])

    positive = [
        r for r in rows
        if r.get("is_fresh")
        and r.get("available_quantity") is not None
        and float(r["available_quantity"]) > 0
    ]
    if positive:
        for row in positive:
            qty = float(row["available_quantity"])
            price_rub = row.get("price_rub")
            warehouse = escape(str(row["public_name"]))
            if price_rub is not None:
                lines.append(
                    f"• <b>{warehouse}</b> — <b>{_format_rub(float(price_rub))}</b> (<b>{qty:g} шт.</b>)"
                )
            else:
                lines.append(f"• <b>{warehouse}</b> — (<b>{qty:g} шт.</b>)")
    else:
        known = bool(rows) and all(
            r.get("is_fresh") and r.get("available_quantity") is not None
            for r in rows
        )
        lines.append(
            "Нет в наличии."
            if known
            else "Актуальный остаток сейчас не подтверждён."
        )
    return "\n".join(lines)


def _price_link_payload(chat_id: int) -> str:
    raw_chat_id = str(int(chat_id))
    signature = hmac.new(
        TOKEN.encode("utf-8"),
        f"price:{raw_chat_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
    return f"price_{raw_chat_id}_{signature}"


def _parse_price_link_payload(payload: str) -> int | None:
    if not payload.startswith("price_"):
        return None
    try:
        _, raw_chat_id, signature = payload.split("_", 2)
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return None
    expected = hmac.new(
        TOKEN.encode("utf-8"),
        f"price:{chat_id}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:12]
    if not hmac.compare_digest(signature, expected):
        return None
    return chat_id


async def _is_chat_member(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    user_id: int,
) -> bool:
    try:
        member = await context.bot.get_chat_member(chat_id, user_id)
    except Exception:
        logging.exception(
            "PROBNIK price-link membership check failed chat_id=%s user_id=%s",
            chat_id,
            user_id,
        )
        return False
    return str(member.status) in {"creator", "administrator", "member", "restricted"}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    payload = str(context.args[0]).strip() if context.args else ""
    if payload.startswith("price_"):
        source_chat_id = _parse_price_link_payload(payload)
        user = update.effective_user
        chat = update.effective_chat
        allowed = bool(
            source_chat_id is not None
            and user is not None
            and chat is not None
            and str(chat.type) == "private"
            and await _is_chat_member(context, source_chat_id, int(user.id))
        )
        if not allowed:
            await update.effective_message.reply_text(
                "кнопка <b>ПРОЦЕНИТЬ</b> доступна только членам <b>Extremizer Pro</b>",
                parse_mode=ParseMode.HTML,
            )
            return
        context.user_data["pricing_source_chat_id"] = int(source_chat_id)
        try:
            source_chat = await context.bot.get_chat(int(source_chat_id))
            context.user_data["pricing_source_chat_title"] = str(
                getattr(source_chat, "title", "") or ""
            ).strip() or None
        except Exception:
            context.user_data["pricing_source_chat_title"] = None
        await update.effective_message.reply_text(
            "Отправь OEM-каталожный номер одним сообщением."
        )
        return

    await update.effective_message.reply_text(WELCOME, parse_mode=ParseMode.HTML)


async def price_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    message = update.effective_message
    if chat is None or user is None or message is None:
        return
    if str(chat.type) not in {"group", "supergroup"}:
        await message.reply_text("Эта команда используется только в рабочем чате.")
        return

    try:
        caller = await context.bot.get_chat_member(int(chat.id), int(user.id))
    except Exception:
        await message.reply_text("Не удалось проверить права администратора.")
        return
    if str(caller.status) not in {"creator", "administrator"}:
        await message.reply_text("Кнопку ПРОЦЕНИТЬ может установить администратор чата.")
        return

    me = await context.bot.get_me()
    username = str(me.username or "").strip()
    if not username:
        await message.reply_text("Не удалось определить username Пробника.")
        return

    payload = _price_link_payload(int(chat.id))
    url = f"https://t.me/{username}?start={payload}"
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("ПРОЦЕНИТЬ", url=url)]]
    )
    posted = await context.bot.send_message(
        chat_id=int(chat.id),
        text="Узнать цену и наличие по OEM",
        reply_markup=markup,
    )
    try:
        await context.bot.pin_chat_message(
            chat_id=int(chat.id),
            message_id=int(posted.message_id),
            disable_notification=True,
        )
    except Exception:
        logging.exception(
            "PROBNIK could not pin price button chat_id=%s message_id=%s",
            chat.id,
            posted.message_id,
        )
        await message.reply_text(
            "Кнопка ПРОЦЕНИТЬ создана. Закрепи это сообщение вручную."
        )


async def oem_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    if chat is None or str(chat.type) != "private":
        return
    raw = str(update.effective_message.text or "").strip()
    parts = [x for x in raw.replace(",", " ").replace(";", " ").split() if x]
    if len(parts) != 1:
        await update.effective_message.reply_text(
            "Отправьте один OEM-каталожный номер одним сообщением."
        )
        return
    oem = normalize_oem(parts[0])
    if not oem:
        await update.effective_message.reply_text(
            "Не удалось распознать OEM. Проверьте номер и повторите."
        )
        return

    source_chat_id = context.user_data.get("pricing_source_chat_id")
    source_kind = "workchat" if source_chat_id is not None else "private"
    analytics_request_id = pricing_analytics.begin_request_from_update(
        ORDERS_DB,
        source_bot="probnik",
        source_kind=source_kind,
        update=update,
        source_chat_id=source_chat_id,
        source_chat_title=context.user_data.get("pricing_source_chat_title"),
        oem=oem,
    )

    status = await update.effective_message.reply_text("🔎 Проверяю цену и наличие…")
    try:
        info_task = asyncio.to_thread(_identity_and_price, oem)
        stock_task = _stock_with_refresh(oem)
        info, rows = await asyncio.gather(info_task, stock_task)
        stock_rows = [
            {
                "warehouse": row.get("public_name"),
                "qty": row.get("available_quantity"),
                "price_rub": row.get("price_rub"),
            }
            for row in rows
            if row.get("is_fresh")
            and row.get("available_quantity") is not None
            and float(row["available_quantity"]) > 0
        ]
        price_found = info.get("customer_rub") is not None
        pricing_analytics.complete_request(
            ORDERS_DB,
            analytics_request_id,
            manufacturer=info.get("manufacturer"),
            result_status="FOUND" if price_found or stock_rows else "UNAVAILABLE",
            price_status="FOUND" if price_found else "UNAVAILABLE",
            display_price_amount=info.get("customer_rub"),
            display_price_currency="RUB" if price_found else None,
            stock_rf_status="FOUND" if stock_rows else "NONE",
            stock_rf=stock_rows,
        )
        await status.edit_text(
            _compose(oem, info, rows),
            parse_mode=ParseMode.HTML,
        )
    except Exception:
        import logging
        logging.exception("PROBNIK lookup failed for %s", oem)
        await status.edit_text(
            "Не удалось завершить проверку. Попробуйте повторить запрос чуть позже."
        )


def build_application(token: str | None = None) -> Application:
    token = token or TOKEN
    if not token:
        raise RuntimeError("PROBNIK_BOT_TOKEN is not configured")
    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("pricebutton", price_button))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, oem_message))
    return app


def main() -> None:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    init_probnik_identity()
    pricing_analytics.init_analytics(ORDERS_DB)
    logging.info("PROBNIK polling runtime starting")
    build_application().run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
