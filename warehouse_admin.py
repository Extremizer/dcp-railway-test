#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
from datetime import datetime
from html import escape
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import ContextTypes

import warehouse_importer
import warehouse_store
import stock_engine
import warehouse_stock_service
import warehouse_alerts

ORDERS_DB_FILE = Path("extremizer_orders.db")
RATE_ADMIN_USER_ID = 0
safe_reply_text = None
log = logging.getLogger(__name__)


def configure(db_file, admin_user_id, safe_reply_func, logger=None):
    global ORDERS_DB_FILE, RATE_ADMIN_USER_ID, safe_reply_text, log
    ORDERS_DB_FILE = Path(db_file)
    RATE_ADMIN_USER_ID = int(admin_user_id)
    safe_reply_text = safe_reply_func
    if logger is not None:
        log = logger


def _is_group_chat(update):
    chat = update.effective_chat
    return bool(chat and getattr(chat, "type", "") in {"group", "supergroup"})


def clear_states(context):
    clear_pending_warehouse_import(context)
    for key in (
        "admin_warehouse_stockcheck_id",
        "admin_warehouse_add",
        "admin_warehouse_edit",
        "admin_warehouse_source_edit",
        "admin_warehouse_manual_stock",
    ):
        context.user_data.pop(key, None)


def is_text_mode_active(context):
    return any(
        context.user_data.get(key)
        for key in (
            "admin_warehouse_stockcheck_id",
            "admin_warehouse_add",
            "admin_warehouse_edit",
            "admin_warehouse_source_edit",
            "admin_warehouse_manual_stock",
        )
    )


def format_admin_warehouses() -> str:
    warehouses = warehouse_store.list_warehouses(
        include_inactive=True,
        db_file=ORDERS_DB_FILE,
    )
    lines = ["🏬 <b>Склады</b>", ""]
    if not warehouses:
        lines.append("Складов пока нет.")
        return "\n".join(lines)

    lines.append(f"Всего: <b>{len(warehouses)}</b>")
    lines.append("")
    for warehouse in warehouses:
        icon = "🟢" if warehouse["active"] else "⚪"
        lines.append(
            f"{icon} <b>{escape(warehouse['internal_name'])}</b> — "
            f"{escape(warehouse['city'])} · "
            f"{escape(warehouse['public_name'])}"
        )
    return "\n".join(lines)


def format_admin_warehouse_problems() -> str:
    data = warehouse_alerts.problem_counts(ORDERS_DB_FILE)
    items = data["items"]
    lines = ["⚠️ <b>Проблемы складов</b>", ""]
    if not items:
        lines.append("✅ Активных проблем не найдено.")
        return "\n".join(lines)

    labels = {
        "confirmed_no_warehouse": "🟡 Подтверждён без склада",
        "executing_no_committed": "🔴 Выполняется без committed",
        "stock_unknown": "🟡 Остаток неизвестен",
        "stock_stale": "🟡 Остаток устарел",
        "stock_insufficient": "🔴 Недостаточный остаток",
        "hanging_reservation": "🟡 Зависший резерв",
        "source_error": "🟡 Ошибка источника",
        "snapshot_mismatch": "🔴 Snapshot не поглотил committed",
    }
    lines.append(f"Всего: <b>{len(items)}</b>")
    lines.append("")
    for item in items[:40]:
        label = labels.get(item["kind"], "⚠️ Проблема")
        order_id = item.get("order_id")
        warehouse_name = item.get("warehouse_name")
        oem = item.get("oem")
        detail = []
        if order_id:
            detail.append(f"заказ <code>{escape(str(order_id))}</code>")
        if warehouse_name:
            detail.append(escape(str(warehouse_name)))
        if oem:
            detail.append(f"OEM <code>{escape(str(oem))}</code>")
        if item.get("detail"):
            detail.append(escape(str(item["detail"]))[:120])
        lines.append(f"{label}" + (f" — {' · '.join(detail)}" if detail else ""))
    if len(items) > 40:
        lines.append(f"\n…ещё {len(items) - 40}")
    return "\n".join(lines)


def admin_warehouse_problems_keyboard() -> InlineKeyboardMarkup:
    rows = []
    items = warehouse_alerts.collect_warehouse_problems(ORDERS_DB_FILE)
    seen_orders = set()
    seen_warehouses = set()
    for item in items:
        order_id = item.get("order_id")
        if order_id and order_id not in seen_orders and len(seen_orders) < 8:
            seen_orders.add(order_id)
            rows.append([
                InlineKeyboardButton(
                    f"📦 {order_id}",
                    callback_data=f"admin:order:{order_id}",
                )
            ])
    for item in items:
        wid = item.get("warehouse_id")
        if (
            wid and wid not in seen_warehouses
            and len(seen_warehouses) < 4
            and not item.get("order_id")
        ):
            seen_warehouses.add(wid)
            rows.append([
                InlineKeyboardButton(
                    f"🏬 {item.get('warehouse_name') or wid}",
                    callback_data=f"admin:warehouse:view:{wid}",
                )
            ])
    rows.extend([
        [InlineKeyboardButton("🔄 Обновить", callback_data="admin:warehouse:problems")],
        [InlineKeyboardButton("⬅️ К складам", callback_data="admin:warehouses")],
    ])
    return InlineKeyboardMarkup(rows)


def format_admin_warehouse_health() -> str:
    rows = warehouse_store.warehouse_health_summary(
        db_file=ORDERS_DB_FILE,
    )
    lines = [
        "🩺 <b>Диагностика складов</b>",
        "",
    ]
    if not rows:
        lines.append("Складов пока нет.")
        return "\n".join(lines)

    source_icons = {
        "website": "🌐",
        "file": "📄",
        "manual": "✏️",
        "api": "🔌",
    }

    for item in rows:
        warehouse = item["warehouse"]
        warehouse_state = (
            "🟢"
            if warehouse["active"] and not warehouse.get("deleted_at")
            else "⚪"
        )
        lines.extend([
            (
                f"{warehouse_state} "
                f"<b>{escape(warehouse['internal_name'])}</b> "
                f"· {escape(warehouse['public_name'])}"
            ),
        ])

        for source in item["sources"]:
            source_type = str(source["source_type"])
            icon = source_icons.get(source_type, "•")
            counts = item["stock_counts"].get(
                source_type,
                {"total": 0, "positive": 0, "fresh": 0},
            )
            if not source["enabled"]:
                state = "⚪ выкл."
            elif source.get("last_error"):
                state = "⚠️ ошибка"
            elif source.get("last_success_at"):
                state = "✅ ок"
            else:
                state = "▫️ нет данных"

            lines.append(
                f"  {icon} {source_type}: {state} · "
                f"{counts['fresh']}/{counts['total']} свежих"
            )

        lines.append(
            "  📦 кэш: "
            f"<b>{sum(v['total'] for v in item['stock_counts'].values())}</b> OEM"
        )
        lines.append(
            "  🔒 активные резервы: "
            f"<b>{item['reservation_count']}</b> "
            f"({item['reserved_quantity']:g} шт.)"
        )
        if warehouse.get("last_error"):
            lines.append(
                "  ⚠️ последняя ошибка: "
                f"<code>{escape(str(warehouse['last_error']))}</code>"
            )
        lines.append("")

    return "\n".join(lines)


def admin_warehouse_health_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data="admin:warehouse:health",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К складам",
                callback_data="admin:warehouses",
            )
        ],
    ])


def admin_warehouses_keyboard() -> InlineKeyboardMarkup:
    rows = []
    for warehouse in warehouse_store.list_warehouses(
        include_inactive=True,
        db_file=ORDERS_DB_FILE,
    ):
        icon = "🟢" if warehouse["active"] else "⚪"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {warehouse['internal_name']}",
                callback_data=f"admin:warehouse:view:{warehouse['id']}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "⚠️ Проблемы складов",
            callback_data="admin:warehouse:problems",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            "🩺 Диагностика складов",
            callback_data="admin:warehouse:health",
        )
    ])
    rows.append([
        InlineKeyboardButton(
            "➕ Добавить склад",
            callback_data="admin:warehouse:add",
        )
    ])
    rows.append([
        InlineKeyboardButton("⬅️ Назад в админку", callback_data="admin:home")
    ])
    return InlineKeyboardMarkup(rows)


def format_admin_warehouse(warehouse_id: int) -> str | None:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        return None

    sources = warehouse_store.get_sources(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    source_labels = {
        "website": "🌐 Сайт",
        "file": "📄 Файл",
        "manual": "✏️ Ручные данные",
        "api": "🔌 API",
    }

    lines = [
        f"🏬 <b>{escape(warehouse['internal_name'])}</b>",
        "",
        f"Город: <b>{escape(warehouse['city'])}</b>",
        f"Код: <code>{escape(warehouse['code'])}</code>",
        f"Для клиента: <b>{escape(warehouse['public_name'])}</b>",
        f"Сайт: {escape(warehouse['website_url'] or '—')}",
        f"Adapter: <code>{escape(warehouse['adapter_type'] or '—')}</code>",
        f"Приоритет: <b>{warehouse['priority']}</b>",
        f"Статус: {'🟢 Активен' if warehouse['active'] else '⚪ Отключён'}",
        (
            "Учёт наших заказов: <b>авто по новому снимку</b>"
            if warehouse.get("stock_sync_mode") == "snapshot_absorbs_committed"
            else "Учёт наших заказов: <b>держать бронь вручную</b>"
        ),
        "",
        "<b>Источники остатков:</b>",
    ]

    for source in sources:
        label = source_labels.get(source["source_type"], source["source_type"])
        state = "🟢" if source["enabled"] else "⚪"
        ttl = source["ttl_minutes"]
        ttl_text = f" · TTL {ttl} мин." if ttl is not None else ""
        lines.append(f"{state} {label}{ttl_text}")
        if source.get("last_success_at"):
            lines.append(
                "   ✅ Последний успех: "
                f"<code>{escape(str(source['last_success_at']))}</code>"
            )
        if source.get("last_error"):
            lines.append(
                "   ⚠️ Последняя ошибка: "
                f"<code>{escape(str(source['last_error']))}</code>"
            )

    latest = warehouse_store.latest_import(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if latest:
        total, positive = warehouse_store.count_current_stock(
            warehouse_id,
            source_type="file",
            db_file=ORDERS_DB_FILE,
        )
        lines.extend([
            "",
            "<b>Последний файл остатков:</b>",
            f"{escape(latest['filename'])}",
            f"Загружен: <code>{escape(latest['imported_at'])}</code>",
            f"Строк: <b>{latest['rows_success']}</b> успешно · "
            f"<b>{latest['rows_error']}</b> ошибок",
            f"Текущий файл: <b>{total}</b> позиций · "
            f"<b>{positive}</b> с остатком",
        ])

    return "\n".join(lines)


def format_admin_warehouse_stockcheck(
    warehouse_id: int,
    oem: str,
) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        return "Склад не найден."

    stock = stock_engine.get_available_stock(
        warehouse_id,
        oem,
        db_file=ORDERS_DB_FILE,
    )

    raw = stock.get("raw_quantity")
    blocked = float(stock.get("blocked_quantity") or 0)
    available = stock.get("available_quantity")
    source_type = stock.get("source_type") or "—"
    fresh = bool(stock.get("is_fresh"))
    observed = stock.get("observed_at") or "—"
    expires = stock.get("expires_at") or "—"
    reservations = stock.get("reservations") or []

    def qty(value):
        return "неизвестно" if value is None else f"{float(value):g} шт."

    lines = [
        "🔎 <b>Остаток по OEM</b>",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        f"Для клиента: <b>{escape(warehouse['public_name'])}</b>",
        f"OEM: <code>{escape(oem)}</code>",
        "",
        f"Внешний остаток: <b>{escape(qty(raw))}</b>",
        f"Наши активные резервы: <b>{blocked:g} шт.</b>",
        f"Доступно: <b>{escape(qty(available))}</b>",
        "",
        f"Источник: <code>{escape(str(source_type))}</code>",
        f"Свежесть: <b>{'🟢 свежие данные' if fresh else '⚠️ данные устарели/неизвестны'}</b>",
        f"Получено: <code>{escape(str(observed))}</code>",
        f"Действительно до: <code>{escape(str(expires))}</code>",
    ]

    if reservations:
        status_labels = {
            "hold": "HOLD",
            "reserved": "RESERVED",
            "committed": "COMMITTED",
        }
        lines.extend(["", "<b>Активные резервы:</b>"])
        for reservation in reservations[:20]:
            order_id = reservation.get("order_id") or "—"
            status = status_labels.get(
                reservation.get("status"),
                str(reservation.get("status") or "—"),
            )
            lines.append(
                f"• {escape(status)} · "
                f"{float(reservation.get('quantity') or 0):g} шт. · "
                f"запрос <code>{escape(str(order_id))}</code>"
            )
    else:
        lines.extend(["", "Активных резервов по OEM нет."])

    return "\n".join(lines)


SOURCE_LABELS = {
    "manual": "✏️ Ручные данные",
    "website": "🌐 Сайт",
    "file": "📄 Файл",
    "api": "🔌 API",
}


def format_admin_warehouse_sources(warehouse_id: int) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        return "Склад не найден."

    sources = warehouse_store.get_sources(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    lines = [
        "⚙️ <b>Источники остатков</b>",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        "",
    ]
    for source in sources:
        label = SOURCE_LABELS.get(
            source["source_type"],
            source["source_type"],
        )
        state = "🟢 включён" if source["enabled"] else "⚪ выключен"
        ttl = (
            "без TTL"
            if source["ttl_minutes"] is None
            else f"{source['ttl_minutes']} мин."
        )
        lines.extend([
            f"<b>{label}</b>",
            f"Статус: {state}",
            f"TTL: <b>{ttl}</b>",
            f"Приоритет: <b>{source['priority']}</b>",
            "",
        ])
    lines.append(
        "Меньшее число приоритета = источник сильнее."
    )
    return "\n".join(lines)


def admin_warehouse_sources_keyboard(
    warehouse_id: int,
) -> InlineKeyboardMarkup:
    rows = []
    for source in warehouse_store.get_sources(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    ):
        label = SOURCE_LABELS.get(
            source["source_type"],
            source["source_type"],
        )
        icon = "🟢" if source["enabled"] else "⚪"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {label}",
                callback_data=(
                    f"admin:warehouse:source:view:"
                    f"{warehouse_id}:{source['source_type']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "⬅️ К складу",
            callback_data=f"admin:warehouse:view:{warehouse_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


def format_admin_warehouse_source(
    warehouse_id: int,
    source_type: str,
) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    source = warehouse_store.get_source(
        warehouse_id,
        source_type,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse or not source:
        return "Источник не найден."

    label = SOURCE_LABELS.get(source_type, source_type)
    ttl = (
        "без TTL"
        if source["ttl_minutes"] is None
        else f"{source['ttl_minutes']} мин."
    )
    lines = [
        f"{label}",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        f"Статус: {'🟢 включён' if source['enabled'] else '⚪ выключен'}",
        f"TTL: <b>{ttl}</b>",
        f"Приоритет: <b>{source['priority']}</b>",
    ]
    if source_type == "file":
        config = warehouse_store.get_source_config(
            warehouse_id,
            "file",
            db_file=ORDERS_DB_FILE,
        )
        import_mode = config.get(
            "import_mode",
            "full_snapshot",
        )
        missing_policy = config.get(
            "missing_oem_policy",
            "unknown",
        )
        duplicate_policy = config.get(
            "duplicate_oem_policy",
            "sum",
        )
        duplicate_labels = {
            "sum": "суммировать",
            "max": "взять максимум",
            "last": "последняя строка",
            "reject": "ошибка / не импортировать",
        }
        lines.extend([
            (
                "Режим файла: <b>полный снимок</b>"
                if import_mode == "full_snapshot"
                else "Режим файла: <b>delta</b>"
            ),
            (
                "Если OEM нет в файле: <b>не применяется в delta</b>"
                if import_mode == "delta"
                else (
                    "Если OEM нет в файле: <b>неизвестно</b>"
                    if missing_policy == "unknown"
                    else "Если OEM нет в файле: <b>считать 0</b>"
                )
            ),
            (
                "Повторяющиеся OEM: <b>"
                + escape(
                    duplicate_labels.get(
                        duplicate_policy,
                        duplicate_policy,
                    )
                )
                + "</b>"
            ),
        ])
    if source.get("last_success_at"):
        lines.append(
            "Последний успех: "
            f"<code>{escape(str(source['last_success_at']))}</code>"
        )
    if source.get("last_error"):
        lines.append(
            "Последняя ошибка: "
            f"<code>{escape(str(source['last_error']))}</code>"
        )
    return "\n".join(lines)


def admin_warehouse_source_keyboard(
    warehouse_id: int,
    source_type: str,
) -> InlineKeyboardMarkup:
    source = warehouse_store.get_source(
        warehouse_id,
        source_type,
        db_file=ORDERS_DB_FILE,
    )
    if not source:
        return admin_warehouse_sources_keyboard(warehouse_id)

    toggle_text = "⏸ Выключить" if source["enabled"] else "▶️ Включить"
    rows = [
        [
            InlineKeyboardButton(
                toggle_text,
                callback_data=(
                    f"admin:warehouse:source:toggle:"
                    f"{warehouse_id}:{source_type}"
                ),
            )
        ],
    ]

    if source_type == "file":
        config = warehouse_store.get_source_config(
            warehouse_id,
            "file",
            db_file=ORDERS_DB_FILE,
        )
        import_mode = config.get(
            "import_mode",
            "full_snapshot",
        )
        missing_policy = config.get(
            "missing_oem_policy",
            "unknown",
        )
        duplicate_policy = config.get(
            "duplicate_oem_policy",
            "sum",
        )
        duplicate_button_labels = {
            "sum": "➕ Дубли: сумма",
            "max": "📈 Дубли: max",
            "last": "↩️ Дубли: последняя",
            "reject": "⛔ Дубли: ошибка",
        }
        rows.extend([
            [
                InlineKeyboardButton(
                    (
                        "📦 Режим: полный снимок"
                        if import_mode == "full_snapshot"
                        else "📦 Режим: delta"
                    ),
                    callback_data=(
                        f"admin:warehouse:source:filemode:"
                        f"{warehouse_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    (
                        "❓ Нет OEM: неизвестно"
                        if missing_policy == "unknown"
                        else "❓ Нет OEM: считать 0"
                    ),
                    callback_data=(
                        f"admin:warehouse:source:missing:"
                        f"{warehouse_id}"
                    ),
                )
            ],
            [
                InlineKeyboardButton(
                    duplicate_button_labels.get(
                        duplicate_policy,
                        "➕ Дубли: сумма",
                    ),
                    callback_data=(
                        f"admin:warehouse:source:duplicates:"
                        f"{warehouse_id}"
                    ),
                )
            ],
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                "⏱ Изменить TTL",
                callback_data=(
                    f"admin:warehouse:source:edit:"
                    f"{warehouse_id}:{source_type}:ttl"
                ),
            ),
            InlineKeyboardButton(
                "↕️ Приоритет",
                callback_data=(
                    f"admin:warehouse:source:edit:"
                    f"{warehouse_id}:{source_type}:priority"
                ),
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ К источникам",
                callback_data=f"admin:warehouse:sources:{warehouse_id}",
            )
        ],
    ])
    return InlineKeyboardMarkup(rows)


def format_admin_warehouse_history(
    warehouse_id: int,
    limit: int = 20,
) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        return "Склад не найден."

    rows = warehouse_store.list_recent_stock_observations(
        warehouse_id,
        limit=limit,
        db_file=ORDERS_DB_FILE,
    )

    source_icons = {
        "manual": "✏️",
        "website": "🌐",
        "file": "📄",
        "api": "🔌",
    }
    status_labels = {
        "in_stock": "в наличии",
        "out_of_stock": "нет",
        "quantity_unknown": "кол-во ?",
        "not_found": "не найден",
        "ambiguous": "неоднозначно",
    }

    lines = [
        "📜 <b>История остатков</b>",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        f"Последних записей: <b>{min(len(rows), limit)}</b>",
        "",
    ]
    if not rows:
        lines.append("Истории пока нет.")
        return "\n".join(lines)

    for row in rows:
        icon = source_icons.get(
            row.get("source_type"),
            "•",
        )
        quantity = row.get("quantity")
        status = str(row.get("status") or "")
        if quantity is not None:
            value = f"{float(quantity):g} шт."
        else:
            value = status_labels.get(status, status or "—")
        observed = str(row.get("observed_at") or "—")
        lines.extend([
            (
                f"{icon} <code>{escape(str(row['oem']))}</code> "
                f"→ <b>{escape(value)}</b>"
            ),
            f"   <code>{escape(observed)}</code>",
        ])

    return "\n".join(lines)


def admin_warehouse_history_keyboard(
    warehouse_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data=f"admin:warehouse:history:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К складу",
                callback_data=f"admin:warehouse:view:{warehouse_id}",
            )
        ],
    ])


def format_admin_mapping_profiles(
    warehouse_id: int,
) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        return "Склад не найден."

    profiles = warehouse_store.list_mapping_profiles(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    lines = [
        "🧠 <b>Шаблоны файлов</b>",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        f"Активных шаблонов: <b>{len(profiles)}</b>",
        "",
    ]
    if not profiles:
        lines.append(
            "Шаблонов пока нет. Первый успешный импорт "
            "автоматически создаст профиль формата."
        )
        return "\n".join(lines)

    for profile in profiles:
        used = int(profile.get("use_count") or 0)
        sheet = profile.get("sheet_name") or "—"
        lines.extend([
            (
                f"<b>#{profile['id']} · "
                f"{escape(str(profile['file_format'] or '').upper())}</b>"
            ),
            f"Лист: <code>{escape(str(sheet))}</code>",
            f"Использован: <b>{used}</b> раз",
            (
                "Последний раз: "
                f"<code>{escape(str(profile.get('last_used_at') or '—'))}</code>"
            ),
            "",
        ])
    return "\n".join(lines)


def admin_mapping_profiles_keyboard(
    warehouse_id: int,
) -> InlineKeyboardMarkup:
    rows = []
    for profile in warehouse_store.list_mapping_profiles(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    ):
        rows.append([
            InlineKeyboardButton(
                (
                    f"🧠 #{profile['id']} "
                    f"{str(profile['file_format'] or '').upper()} · "
                    f"{int(profile.get('use_count') or 0)}×"
                )[:60],
                callback_data=(
                    f"admin:warehouse:profile:view:"
                    f"{warehouse_id}:{profile['id']}"
                ),
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "⬅️ К складу",
            callback_data=f"admin:warehouse:view:{warehouse_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


def format_admin_mapping_profile(
    warehouse_id: int,
    profile_id: int,
) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    profile = warehouse_store.get_mapping_profile(
        warehouse_id,
        profile_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse or not profile:
        return "Шаблон не найден."

    mapping = profile.get("mapping") or {}
    pairs = []
    for field, index in sorted(mapping.items()):
        pairs.append(f"{field} → колонка {int(index) + 1}")
    mapping_text = ", ".join(pairs) if pairs else "—"

    return "\n".join([
        "🧠 <b>Шаблон файла</b>",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        f"Шаблон: <b>#{profile['id']}</b>",
        f"Формат: <b>{escape(str(profile['file_format'] or '').upper())}</b>",
        f"Лист: <code>{escape(str(profile.get('sheet_name') or '—'))}</code>",
        f"Строка заголовка: <b>{profile.get('header_row') or '—'}</b>",
        f"Использован: <b>{int(profile.get('use_count') or 0)}</b> раз",
        f"Сопоставление: <code>{escape(mapping_text)}</code>",
    ])


def admin_mapping_profile_keyboard(
    warehouse_id: int,
    profile_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🗑 Отключить шаблон",
                callback_data=(
                    f"admin:warehouse:profile:disable:"
                    f"{warehouse_id}:{profile_id}"
                ),
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К шаблонам",
                callback_data=f"admin:warehouse:profiles:{warehouse_id}",
            )
        ],
    ])


def format_admin_import_history(
    warehouse_id: int,
    limit: int = 20,
) -> str:
    warehouse = warehouse_store.get_warehouse(
        warehouse_id,
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        return "Склад не найден."

    imports = warehouse_store.list_recent_imports(
        warehouse_id,
        limit=limit,
        db_file=ORDERS_DB_FILE,
    )
    lines = [
        "📥 <b>История файлов</b>",
        "",
        f"Склад: <b>{escape(warehouse['internal_name'])}</b>",
        f"Последних импортов: <b>{len(imports)}</b>",
        "",
    ]
    if not imports:
        lines.append("Файлы остатков ещё не импортировались.")
        return "\n".join(lines)

    for item in imports:
        mode = (
            "полный снимок"
            if item.get("import_mode") == "full_snapshot"
            else "delta"
        )
        missing = (
            "неизвестно"
            if item.get("missing_oem_policy") == "unknown"
            else "0"
        )
        duplicate_policy = str(
            item.get("duplicate_oem_policy") or "sum"
        )
        duplicate_labels = {
            "sum": "sum",
            "max": "max",
            "last": "last",
            "reject": "reject",
        }
        lines.extend([
            f"<b>#{item['id']} · {escape(str(item['filename']))}</b>",
            f"<code>{escape(str(item['imported_at']))}</code>",
            (
                f"{mode} · нет OEM = {missing} · "
                f"дубли = {duplicate_labels.get(duplicate_policy, duplicate_policy)} · "
                f"✅ {item['rows_success']} · ⚠️ {item['rows_error']}"
            ),
            "",
        ])
    return "\n".join(lines)


def admin_import_history_keyboard(
    warehouse_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔄 Обновить",
                callback_data=f"admin:warehouse:imports:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ К складу",
                callback_data=f"admin:warehouse:view:{warehouse_id}",
            )
        ],
    ])


def admin_warehouse_keyboard(
    warehouse_id: int,
    active: bool,
) -> InlineKeyboardMarkup:
    toggle_text = "⏸ Отключить" if active else "▶️ Включить"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✏️ Редактировать",
                callback_data=f"admin:warehouse:edit:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📄 Остатки из файла",
                callback_data=f"admin:warehouse:file:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🧠 Шаблоны файлов",
                callback_data=f"admin:warehouse:profiles:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📥 История файлов",
                callback_data=f"admin:warehouse:imports:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "⚙️ Источники остатков",
                callback_data=f"admin:warehouse:sources:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "✏️ Ручной остаток",
                callback_data=f"admin:warehouse:manual:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔎 Проверить остаток по OEM",
                callback_data=f"admin:warehouse:stockcheck:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "📜 История остатков",
                callback_data=f"admin:warehouse:history:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                "🔄 Учёт наших заказов",
                callback_data=f"admin:warehouse:syncmode:{warehouse_id}",
            )
        ],
        [
            InlineKeyboardButton(
                toggle_text,
                callback_data=f"admin:warehouse:toggle:{warehouse_id}",
            ),
            InlineKeyboardButton(
                "🗑 Удалить",
                callback_data=f"admin:warehouse:delete:{warehouse_id}",
            ),
        ],
        [
            InlineKeyboardButton(
                "⬅️ К складам",
                callback_data="admin:warehouses",
            )
        ],
        [
            InlineKeyboardButton(
                "⚙️ В админку",
                callback_data="admin:home",
            )
        ],
    ])


def admin_warehouse_edit_keyboard(warehouse_id: int) -> InlineKeyboardMarkup:
    fields = [
        ("internal_name", "🏷 Название в админке"),
        ("city", "🏙 Город"),
        ("code", "🔤 Код склада"),
        ("public_name", "👤 Название для клиента"),
        ("website_url", "🌐 Сайт"),
        ("adapter_type", "🔌 Adapter"),
        ("priority", "↕️ Приоритет"),
        ("notes", "📝 Заметки"),
    ]
    rows = [
        [
            InlineKeyboardButton(
                label,
                callback_data=f"admin:warehouse:editfield:{warehouse_id}:{field}",
            )
        ]
        for field, label in fields
    ]
    rows.append([
        InlineKeyboardButton(
            "⬅️ К складу",
            callback_data=f"admin:warehouse:view:{warehouse_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


def refresh_pending_duplicate_stats(pending: dict) -> None:
    mapping = pending.get("mapping") or {}
    if "oem" not in mapping:
        pending["duplicate_stats"] = {
            "duplicate_oems": 0,
            "duplicate_rows": 0,
        }
        return

    try:
        pending["duplicate_stats"] = (
            warehouse_importer.duplicate_oem_stats(
                pending["path"],
                mapping=mapping,
                sheet_name=pending["inspection"]["sheet_name"],
            )
        )
    except Exception:
        log.exception("Could not analyze duplicate warehouse OEMs")
        pending["duplicate_stats"] = {
            "duplicate_oems": 0,
            "duplicate_rows": 0,
        }


def format_warehouse_file_preview(pending: dict) -> str:
    inspection = pending["inspection"]
    mapping = pending.get("mapping") or {}
    headers = inspection["headers"]

    def header_for(field: str) -> str:
        index = mapping.get(field)
        if index is None or index >= len(headers):
            return "не определена"
        return headers[index] or f"колонка {index + 1}"

    lines = [
        "📄 <b>Предпросмотр файла остатков</b>",
        "",
        f"Файл: <code>{escape(pending['filename'])}</code>",
        f"Формат: <b>{escape(inspection['file_format'].upper())}</b>",
        f"Лист: <b>{escape(str(inspection['sheet_name']))}</b>",
        f"Листов в файле: <b>{len(pending.get('sheet_names') or [inspection['sheet_name']])}</b>",
        f"Строк данных: <b>{inspection['rows_total']}</b>",
        (
            "Шаблон склада: <b>🧠 применён автоматически</b>"
            if pending.get("mapping_profile_id")
            else "Шаблон склада: <b>новый формат</b>"
        ),
        "",
        "<b>Распознанные колонки:</b>",
        f"OEM: <b>{escape(header_for('oem'))}</b>",
        f"Количество: <b>{escape(header_for('quantity'))}</b>",
        f"Производитель: {escape(header_for('manufacturer'))}",
        f"Название: {escape(header_for('name'))}",
        "",
        "<b>Первые строки:</b>",
    ]

    for row in inspection.get("preview", [])[:5]:
        preview = " | ".join(str(value or "") for value in row)
        lines.append(f"<code>{escape(preview[:250])}</code>")

    duplicate_stats = pending.get("duplicate_stats") or {}
    duplicate_oems = int(
        duplicate_stats.get("duplicate_oems") or 0
    )
    duplicate_rows = int(
        duplicate_stats.get("duplicate_rows") or 0
    )
    if duplicate_oems:
        config = warehouse_store.get_source_config(
            int(pending["warehouse_id"]),
            "file",
            db_file=ORDERS_DB_FILE,
        )
        duplicate_policy = config.get(
            "duplicate_oem_policy",
            "sum",
        )
        duplicate_labels = {
            "sum": "суммировать",
            "max": "взять максимум",
            "last": "последняя строка",
            "reject": "не импортировать",
        }
        lines.extend([
            "",
            (
                f"⚠️ Повторяющихся OEM: <b>{duplicate_oems}</b> · "
                f"лишних строк: <b>{duplicate_rows}</b>"
            ),
            (
                "Политика дублей: <b>"
                + escape(
                    duplicate_labels.get(
                        duplicate_policy,
                        duplicate_policy,
                    )
                )
                + "</b>"
            ),
        ])

    if "oem" not in mapping or "quantity" not in mapping:
        lines.extend([
            "",
            "⚠️ Не удалось надёжно определить обязательные колонки.",
            "Укажи их вручную перед импортом.",
        ])

    return "\n".join(lines)


def warehouse_file_preview_keyboard(pending: dict) -> InlineKeyboardMarkup:
    mapping = pending.get("mapping") or {}
    rows = []

    if "oem" in mapping and "quantity" in mapping:
        rows.append([
            InlineKeyboardButton(
                "✅ Импортировать",
                callback_data="admin:warehouse:import:confirm",
            )
        ])

    if len(pending.get("sheet_names") or []) > 1:
        rows.append([
            InlineKeyboardButton(
                "📑 Выбрать лист",
                callback_data="admin:warehouse:sheetstart",
            )
        ])

    rows.extend([
        [
            InlineKeyboardButton(
                "🧩 Колонка OEM",
                callback_data="admin:warehouse:mapstart:oem",
            ),
            InlineKeyboardButton(
                "🧩 Количество",
                callback_data="admin:warehouse:mapstart:quantity",
            ),
        ],
        [
            InlineKeyboardButton(
                "❌ Отмена",
                callback_data="admin:warehouse:import:cancel",
            )
        ],
    ])
    return InlineKeyboardMarkup(rows)


def warehouse_sheet_keyboard(
    pending: dict,
) -> InlineKeyboardMarkup:
    current = pending["inspection"]["sheet_name"]
    rows = []
    for index, sheet_name in enumerate(pending.get("sheet_names") or []):
        icon = "✅" if sheet_name == current else "▫️"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {sheet_name}"[:60],
                callback_data=f"admin:warehouse:sheet:{index}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "⬅️ К предпросмотру",
            callback_data="admin:warehouse:import:preview",
        )
    ])
    return InlineKeyboardMarkup(rows)


def warehouse_mapping_keyboard(
    pending: dict,
    field: str,
) -> InlineKeyboardMarkup:
    headers = pending["inspection"]["headers"]
    rows = []
    for index, header in enumerate(headers):
        label = header or f"Колонка {index + 1}"
        rows.append([
            InlineKeyboardButton(
                f"{index + 1}. {label}"[:60],
                callback_data=f"admin:warehouse:map:{field}:{index}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            "⬅️ К предпросмотру",
            callback_data="admin:warehouse:import:preview",
        )
    ])
    return InlineKeyboardMarkup(rows)


def clear_pending_warehouse_import(
    context: ContextTypes.DEFAULT_TYPE,
    delete_file: bool = True,
) -> None:
    pending = context.user_data.pop("admin_warehouse_pending_import", None)
    context.user_data.pop("admin_warehouse_file_upload_id", None)
    if delete_file and pending and pending.get("path"):
        try:
            Path(pending["path"]).unlink(missing_ok=True)
        except OSError:
            log.warning("Could not delete pending warehouse upload %s", pending["path"])


def get_order_stock_items(order_id: str):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        return conn.execute(
            """
            SELECT id, position, manufacturer, oem, name, quantity
            FROM order_items
            WHERE order_id = ?
            ORDER BY position
            """,
            (order_id,),
        ).fetchall()


def get_order_item_for_stock(order_id: str, order_item_id: int):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        return conn.execute(
            """
            SELECT id, position, manufacturer, oem, name, quantity
            FROM order_items
            WHERE order_id = ? AND id = ?
            """,
            (order_id, order_item_id),
        ).fetchone()


def get_item_stock_assignment(order_item_id: int):
    with sqlite3.connect(ORDERS_DB_FILE) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            """
            SELECT
                r.*,
                w.internal_name,
                w.public_name,
                w.city
            FROM warehouse_stock_reservations r
            JOIN warehouses w ON w.id = r.warehouse_id
            WHERE r.order_item_id = ?
              AND r.status IN ('hold','reserved','committed','absorbed')
            ORDER BY r.created_at DESC, r.id DESC
            LIMIT 1
            """,
            (order_item_id,),
        ).fetchone()
    return dict(row) if row else None


def format_admin_order_stock(order_id: str) -> str:
    items = get_order_stock_items(order_id)
    lines = [
        "🏬 <b>Склад / резерв</b>",
        "",
        f"Запрос: <code>{escape(order_id)}</code>",
        "",
    ]
    if not items:
        lines.append("В запросе нет позиций.")
        return "\n".join(lines)

    status_labels = {
        "hold": "⏳ HOLD",
        "reserved": "🔒 Резерв",
        "committed": "📤 Передан складу",
        "absorbed": "✅ Учтён новым остатком",
    }

    for item_id, position, manufacturer, oem, name, quantity in items:
        assignment = get_item_stock_assignment(item_id)
        lines.extend([
            f"<b>{position}. {escape(str(manufacturer or '—'))}</b>",
            f"OEM: <code>{escape(str(oem or '—'))}</code>",
            f"{escape(str(name or '—'))}",
            f"Количество: <b>{quantity} шт.</b>",
        ])
        if assignment:
            lines.append(
                f"Склад: <b>{escape(assignment['internal_name'])}</b> "
                f"({escape(assignment['public_name'])})"
            )
            lines.append(
                f"Статус: {status_labels.get(assignment['status'], assignment['status'])}"
            )
        else:
            lines.append("Склад: <b>не выбран</b>")
        lines.append("")

    return "\n".join(lines)


def admin_order_stock_keyboard(order_id: str) -> InlineKeyboardMarkup:
    rows = []
    for item_id, position, _manufacturer, oem, _name, quantity in get_order_stock_items(order_id):
        assignment = get_item_stock_assignment(item_id)
        icon = "✅" if assignment else "▫️"
        rows.append([
            InlineKeyboardButton(
                f"{icon} {position}. {oem} × {quantity}",
                callback_data=f"admin:stockitem:{order_id}:{item_id}",
            )
        ])
    rows.append([
        InlineKeyboardButton("⬅️ К запросу", callback_data=f"admin:order:{order_id}")
    ])
    return InlineKeyboardMarkup(rows)


def format_admin_stock_item(order_id: str, order_item_id: int) -> str | None:
    item = get_order_item_for_stock(order_id, order_item_id)
    if not item:
        return None

    _id, position, manufacturer, oem, name, quantity = item
    lines = [
        "🏬 <b>Склад / резерв позиции</b>",
        "",
        f"Запрос: <code>{escape(order_id)}</code>",
        f"Позиция: <b>{position}</b>",
        f"Производитель: <b>{escape(str(manufacturer or '—'))}</b>",
        f"OEM: <code>{escape(str(oem or '—'))}</code>",
        f"Название: {escape(str(name or '—'))}",
        f"Нужно: <b>{quantity} шт.</b>",
        "",
    ]

    assignment = get_item_stock_assignment(order_item_id)
    if assignment:
        lines.extend([
            f"Привязан к: <b>{escape(assignment['internal_name'])}</b>",
            f"Для клиента: <b>{escape(assignment['public_name'])}</b>",
            f"Зарезервировано: <b>{assignment['quantity']:g} шт.</b>",
            f"Статус: <b>{escape(assignment['status'])}</b>",
        ])
        return "\n".join(lines)

    lines.append("<b>Доступные склады:</b>")
    for warehouse in warehouse_store.list_warehouses(
        include_inactive=False,
        db_file=ORDERS_DB_FILE,
    ):
        stock = stock_engine.get_available_stock(
            warehouse["id"],
            str(oem or ""),
            db_file=ORDERS_DB_FILE,
        )
        available = stock.get("available_quantity")
        if available is None:
            qty_text = "неизвестно"
        else:
            qty_text = f"{available:g} шт."
        fresh = "свежие" if stock.get("is_fresh") else "устарели"
        lines.append(
            f"• <b>{escape(warehouse['internal_name'])}</b>: "
            f"{escape(qty_text)} · {fresh}"
        )

    return "\n".join(lines)


def admin_stock_item_keyboard(
    order_id: str,
    order_item_id: int,
) -> InlineKeyboardMarkup:
    item = get_order_item_for_stock(order_id, order_item_id)
    if not item:
        return admin_order_stock_keyboard(order_id)

    assignment = get_item_stock_assignment(order_item_id)
    rows = []

    if assignment:
        if assignment["status"] in {"hold", "reserved"}:
            rows.append([
                InlineKeyboardButton(
                    "📤 Передан складу",
                    callback_data=(
                        f"admin:stockcommit:{order_id}:"
                        f"{order_item_id}:{assignment['id']}"
                    ),
                )
            ])
        rows.append([
            InlineKeyboardButton(
                "♻️ Снять привязку / резерв",
                callback_data=(
                    f"admin:stockrelease:{order_id}:"
                    f"{order_item_id}:{assignment['id']}"
                ),
            )
        ])
    else:
        _id, _position, _manufacturer, oem, _name, quantity = item
        rows.append([
            InlineKeyboardButton(
                "🌐 Обновить остатки складов",
                callback_data=f"admin:stockrefresh:{order_id}:{order_item_id}",
            )
        ])
        for warehouse in warehouse_store.list_warehouses(
            include_inactive=False,
            db_file=ORDERS_DB_FILE,
        ):
            stock = stock_engine.get_available_stock(
                warehouse["id"],
                str(oem or ""),
                db_file=ORDERS_DB_FILE,
            )
            available = stock.get("available_quantity")
            if (
                stock.get("is_fresh")
                and available is not None
                and float(available) >= float(quantity)
            ):
                rows.append([
                    InlineKeyboardButton(
                        (
                            f"🔒 {warehouse['internal_name']} · "
                            f"{available:g} шт."
                        )[:60],
                        callback_data=(
                            f"admin:stockreserve:{order_id}:"
                            f"{order_item_id}:{warehouse['id']}"
                        ),
                    )
                ])

    rows.append([
        InlineKeyboardButton(
            "⬅️ К позициям",
            callback_data=f"admin:stock:{order_id}",
        )
    ])
    return InlineKeyboardMarkup(rows)


async def handle_callback(update, context, data):
    query = update.callback_query
    user = update.effective_user
    if data == "admin:warehouse:problems":
        await query.edit_message_text(
            format_admin_warehouse_problems(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_problems_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == "admin:warehouse:health":
        await query.edit_message_text(
            format_admin_warehouse_health(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_health_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data == "admin:warehouses":
        clear_pending_warehouse_import(context)
        context.user_data.pop("admin_warehouse_stockcheck_id", None)
        context.user_data.pop("admin_warehouse_add", None)
        context.user_data.pop("admin_warehouse_edit", None)
        context.user_data.pop("admin_warehouse_source_edit", None)
        context.user_data.pop("admin_warehouse_manual_stock", None)
        context.user_data.pop("admin_search_mode", None)
        context.user_data.pop("admin_delivery_edit", None)
        context.user_data.pop("admin_customer_total_order_id", None)

        await query.edit_message_text(
            format_admin_warehouses(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouses_keyboard(),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:view:"):
        context.user_data.pop("admin_warehouse_stockcheck_id", None)
        context.user_data.pop("admin_warehouse_source_edit", None)
        context.user_data.pop("admin_warehouse_manual_stock", None)
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        warehouse_text = format_admin_warehouse(warehouse_id)
        if not warehouse or warehouse_text is None:
            await query.answer("Склад не найден.", show_alert=True)
            return

        await query.edit_message_text(
            warehouse_text,
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_keyboard(
                warehouse_id,
                bool(warehouse["active"]),
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:imports:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        await query.edit_message_text(
            format_admin_import_history(warehouse_id),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_import_history_keyboard(warehouse_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:profiles:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        await query.edit_message_text(
            format_admin_mapping_profiles(warehouse_id),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_mapping_profiles_keyboard(warehouse_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:profile:view:"):
        parts = data.split(":")
        if len(parts) != 6:
            await query.answer("Некорректный шаблон.", show_alert=True)
            return
        try:
            warehouse_id = int(parts[4])
            profile_id = int(parts[5])
        except ValueError:
            await query.answer("Некорректный шаблон.", show_alert=True)
            return

        profile = warehouse_store.get_mapping_profile(
            warehouse_id,
            profile_id,
            db_file=ORDERS_DB_FILE,
        )
        if not profile or not profile.get("active"):
            await query.answer("Шаблон не найден.", show_alert=True)
            return

        await query.edit_message_text(
            format_admin_mapping_profile(
                warehouse_id,
                profile_id,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_mapping_profile_keyboard(
                warehouse_id,
                profile_id,
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:profile:disable:"):
        parts = data.split(":")
        if len(parts) != 6:
            await query.answer("Некорректный шаблон.", show_alert=True)
            return
        try:
            warehouse_id = int(parts[4])
            profile_id = int(parts[5])
        except ValueError:
            await query.answer("Некорректный шаблон.", show_alert=True)
            return

        if not warehouse_store.deactivate_mapping_profile(
            warehouse_id,
            profile_id,
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Шаблон уже отключён или не найден.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            "✅ <b>Шаблон отключён.</b>\n\n"
            + format_admin_mapping_profiles(warehouse_id),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_mapping_profiles_keyboard(warehouse_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:history:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        await query.edit_message_text(
            format_admin_warehouse_history(warehouse_id),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_history_keyboard(
                warehouse_id,
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:manual:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        context.user_data["admin_warehouse_manual_stock"] = {
            "warehouse_id": warehouse_id,
            "step": "oem",
        }
        await query.edit_message_text(
            "✏️ <b>Ручной остаток</b>\n\n"
            f"Склад: <b>{escape(warehouse['internal_name'])}</b>\n\n"
            "Отправь OEM / каталожный номер.\n\n"
            "Например: <code>417300574</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К складу",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ]
            ]),
        )
        return

    if data.startswith("admin:warehouse:sources:"):
        context.user_data.pop("admin_warehouse_source_edit", None)
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        text = format_admin_warehouse_sources(warehouse_id)
        keyboard = admin_warehouse_sources_keyboard(warehouse_id)
        try:
            await query.answer("Открываю источники остатков…")
        except TelegramError:
            log.warning(
                "Could not acknowledge warehouse sources callback: warehouse=%s",
                warehouse_id,
            )
        try:
            await query.edit_message_text(
                text,
                parse_mode=ParseMode.HTML,
                reply_markup=keyboard,
                disable_web_page_preview=True,
            )
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            log.exception(
                "Warehouse sources card edit failed: warehouse=%s",
                warehouse_id,
            )
            if query.message:
                await query.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
        except TelegramError:
            log.exception(
                "Warehouse sources Telegram edit failed: warehouse=%s",
                warehouse_id,
            )
            if query.message:
                await query.message.reply_text(
                    text,
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                    disable_web_page_preview=True,
                )
        return

    if data.startswith("admin:warehouse:source:view:"):
        parts = data.split(":", 5)
        if len(parts) != 6:
            await query.answer("Некорректный источник.", show_alert=True)
            return
        try:
            warehouse_id = int(parts[4])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return
        source_type = parts[5]

        source = warehouse_store.get_source(
            warehouse_id,
            source_type,
            db_file=ORDERS_DB_FILE,
        )
        if not source:
            await query.answer("Источник не найден.", show_alert=True)
            return

        context.user_data.pop("admin_warehouse_source_edit", None)
        await query.edit_message_text(
            format_admin_warehouse_source(
                warehouse_id,
                source_type,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_source_keyboard(
                warehouse_id,
                source_type,
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:source:toggle:"):
        parts = data.split(":", 5)
        if len(parts) != 6:
            await query.answer("Некорректный источник.", show_alert=True)
            return
        try:
            warehouse_id = int(parts[4])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return
        source_type = parts[5]

        source = warehouse_store.get_source(
            warehouse_id,
            source_type,
            db_file=ORDERS_DB_FILE,
        )
        if not source:
            await query.answer("Источник не найден.", show_alert=True)
            return

        if not warehouse_store.update_source_field(
            warehouse_id,
            source_type,
            "enabled",
            0 if source["enabled"] else 1,
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Не удалось изменить источник.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_warehouse_source(
                warehouse_id,
                source_type,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_source_keyboard(
                warehouse_id,
                source_type,
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:source:filemode:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        config = warehouse_store.get_source_config(
            warehouse_id,
            "file",
            db_file=ORDERS_DB_FILE,
        )
        current = config.get("import_mode", "full_snapshot")
        new_mode = (
            "delta"
            if current == "full_snapshot"
            else "full_snapshot"
        )
        if not warehouse_store.update_source_config(
            warehouse_id,
            "file",
            {"import_mode": new_mode},
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Не удалось изменить режим файла.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_warehouse_source(
                warehouse_id,
                "file",
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_source_keyboard(
                warehouse_id,
                "file",
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:source:missing:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        config = warehouse_store.get_source_config(
            warehouse_id,
            "file",
            db_file=ORDERS_DB_FILE,
        )
        current = config.get(
            "missing_oem_policy",
            "unknown",
        )
        new_policy = "zero" if current == "unknown" else "unknown"
        if not warehouse_store.update_source_config(
            warehouse_id,
            "file",
            {"missing_oem_policy": new_policy},
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Не удалось изменить правило OEM.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_warehouse_source(
                warehouse_id,
                "file",
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_source_keyboard(
                warehouse_id,
                "file",
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:source:duplicates:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        config = warehouse_store.get_source_config(
            warehouse_id,
            "file",
            db_file=ORDERS_DB_FILE,
        )
        current = config.get(
            "duplicate_oem_policy",
            "sum",
        )
        cycle = ["sum", "max", "last", "reject"]
        try:
            index = cycle.index(current)
        except ValueError:
            index = 0
        new_policy = cycle[(index + 1) % len(cycle)]

        if not warehouse_store.update_source_config(
            warehouse_id,
            "file",
            {"duplicate_oem_policy": new_policy},
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Не удалось изменить политику дублей.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_warehouse_source(
                warehouse_id,
                "file",
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_source_keyboard(
                warehouse_id,
                "file",
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:source:edit:"):
        parts = data.split(":", 6)
        if len(parts) != 7:
            await query.answer("Некорректная команда.", show_alert=True)
            return
        try:
            warehouse_id = int(parts[4])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return
        source_type = parts[5]
        field = parts[6]
        if field not in {"ttl", "priority"}:
            await query.answer("Поле нельзя изменить.", show_alert=True)
            return

        source = warehouse_store.get_source(
            warehouse_id,
            source_type,
            db_file=ORDERS_DB_FILE,
        )
        if not source:
            await query.answer("Источник не найден.", show_alert=True)
            return

        context.user_data["admin_warehouse_source_edit"] = {
            "warehouse_id": warehouse_id,
            "source_type": source_type,
            "field": field,
        }

        if field == "ttl":
            current = (
                "без TTL"
                if source["ttl_minutes"] is None
                else f"{source['ttl_minutes']} мин."
            )
            prompt = (
                "⏱ <b>TTL источника</b>\n\n"
                f"Текущее значение: <b>{current}</b>\n\n"
                "Введи количество минут, например <code>30</code>.\n"
                "Для режима без срока — отправь <code>-</code>."
            )
        else:
            prompt = (
                "↕️ <b>Приоритет источника</b>\n\n"
                f"Текущее значение: <b>{source['priority']}</b>\n\n"
                "Введи целое число от 0 до 999.\n"
                "Меньшее число = источник сильнее."
            )

        await query.edit_message_text(
            prompt,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К источнику",
                        callback_data=(
                            f"admin:warehouse:source:view:"
                            f"{warehouse_id}:{source_type}"
                        ),
                    )
                ]
            ]),
        )
        return

    if data == "admin:warehouse:add":
        clear_pending_warehouse_import(context)
        context.user_data.pop("admin_warehouse_stockcheck_id", None)
        context.user_data.pop("admin_warehouse_edit", None)
        context.user_data["admin_warehouse_add"] = {
            "step": "internal_name",
            "data": {},
        }
        await query.edit_message_text(
            "➕ <b>Новый склад</b>\n\n"
            "Шаг 1/5. Введи внутреннее название для админки.\n\n"
            "Например: <code>Orange ATV</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "❌ Отмена",
                        callback_data="admin:warehouse:add:cancel",
                    )
                ]
            ]),
        )
        return

    if data == "admin:warehouse:add:cancel":
        context.user_data.pop("admin_warehouse_add", None)
        await query.edit_message_text(
            format_admin_warehouses(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouses_keyboard(),
        )
        return

    if data.startswith("admin:warehouse:edit:"):
        context.user_data.pop("admin_warehouse_stockcheck_id", None)
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        context.user_data.pop("admin_warehouse_add", None)
        context.user_data.pop("admin_warehouse_edit", None)
        await query.edit_message_text(
            "✏️ <b>Редактирование склада</b>\n\n"
            f"<b>{escape(warehouse['internal_name'])}</b>\n"
            "Выбери, что изменить:",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_edit_keyboard(warehouse_id),
        )
        return

    if data.startswith("admin:warehouse:editfield:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Некорректная команда.", show_alert=True)
            return

        try:
            warehouse_id = int(parts[3])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        field = parts[4]
        prompts = {
            "internal_name": ("название для админки", "Orange ATV"),
            "city": ("город", "Москва"),
            "code": ("код склада", "МСК"),
            "public_name": ("название для клиента", "склад МСК"),
            "website_url": ("ссылку на сайт", "https://example.ru/"),
            "adapter_type": ("технический adapter", "orangeatv"),
            "priority": ("приоритет", "10"),
            "notes": ("заметку", "Любой внутренний комментарий"),
        }
        if field not in prompts:
            await query.answer("Поле нельзя редактировать.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        label, example = prompts[field]
        context.user_data["admin_warehouse_edit"] = {
            "warehouse_id": warehouse_id,
            "field": field,
        }

        await query.edit_message_text(
            "✏️ <b>Редактирование склада</b>\n\n"
            f"Введи {escape(label)}.\n\n"
            f"Текущее значение: <code>{escape(str(warehouse.get(field) or '—'))}</code>\n"
            f"Пример: <code>{escape(example)}</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К редактированию",
                        callback_data=f"admin:warehouse:edit:{warehouse_id}",
                    )
                ]
            ]),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:delete:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        await query.edit_message_text(
            "🗑 <b>Удалить склад?</b>\n\n"
            f"<b>{escape(warehouse['internal_name'])}</b>\n"
            f"{escape(warehouse['city'])} · {escape(warehouse['public_name'])}\n\n"
            "История данных сохранится, но склад перестанет быть активным.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🗑 Да, удалить",
                        callback_data=f"admin:warehouse:deleteconfirm:{warehouse_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Отмена",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ],
            ]),
        )
        return

    if data.startswith("admin:warehouse:deleteconfirm:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        if not warehouse_store.soft_delete_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer("Не удалось удалить склад.", show_alert=True)
            return

        clear_pending_warehouse_import(context)
        context.user_data.pop("admin_warehouse_edit", None)
        context.user_data.pop("admin_warehouse_add", None)
        await query.edit_message_text(
            "✅ <b>Склад удалён.</b>\n\n" + format_admin_warehouses(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouses_keyboard(),
        )
        return

    if data.startswith("admin:warehouse:stockcheck:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        context.user_data["admin_warehouse_stockcheck_id"] = warehouse_id
        await query.edit_message_text(
            "🔎 <b>Проверка остатка по OEM</b>\n\n"
            f"Склад: <b>{escape(warehouse['internal_name'])}</b>\n"
            f"Для клиента: <b>{escape(warehouse['public_name'])}</b>\n\n"
            "Отправь OEM / каталожный номер.\n\n"
            "Например: <code>417300574</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К складу",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ]
            ]),
        )
        return

    if data.startswith("admin:warehouse:syncmode:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        await query.edit_message_text(
            "🔄 <b>Учёт наших заказов</b>\n\n"
            f"Склад: <b>{escape(warehouse['internal_name'])}</b>\n\n"
            "<b>Ручной режим</b> — наши подтверждённые брони "
            "продолжают вычитаться даже после нового файла.\n\n"
            "<b>Авто по новому снимку</b> — если заказ уже был "
            "передан складу, следующий более новый снимок считается "
            "уже учитывающим этот заказ.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🛡 Ручной режим",
                        callback_data=f"admin:warehouse:setsync:{warehouse_id}:manual",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⚡ Авто по новому снимку",
                        callback_data=(
                            f"admin:warehouse:setsync:{warehouse_id}:"
                            "snapshot_absorbs_committed"
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К складу",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ],
            ]),
        )
        return

    if data.startswith("admin:warehouse:setsync:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Некорректная команда.", show_alert=True)
            return

        try:
            warehouse_id = int(parts[3])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        mode = parts[4]
        if not stock_engine.set_stock_sync_mode(
            warehouse_id,
            mode,
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer("Не удалось сохранить режим.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        await query.edit_message_text(
            format_admin_warehouse(warehouse_id) or "Склад не найден.",
            parse_mode=ParseMode.HTML,
            reply_markup=(
                admin_warehouse_keyboard(
                    warehouse_id,
                    bool(warehouse["active"]),
                )
                if warehouse else admin_warehouses_keyboard()
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:toggle:"):
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        warehouse_store.set_warehouse_active(
            warehouse_id,
            not bool(warehouse["active"]),
            db_file=ORDERS_DB_FILE,
        )
        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        await query.edit_message_text(
            format_admin_warehouse(warehouse_id),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_keyboard(
                warehouse_id,
                bool(warehouse["active"]),
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:file:"):
        context.user_data.pop("admin_warehouse_stockcheck_id", None)
        try:
            warehouse_id = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный склад.", show_alert=True)
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            await query.answer("Склад не найден.", show_alert=True)
            return

        clear_pending_warehouse_import(context)
        context.user_data["admin_warehouse_file_upload_id"] = warehouse_id

        await query.edit_message_text(
            "📄 <b>Остатки из файла</b>\n\n"
            f"Склад: <b>{escape(warehouse['internal_name'])}</b>\n"
            f"Для клиента: <b>{escape(warehouse['public_name'])}</b>\n\n"
            "Отправь сюда файл остатков.\n\n"
            "Поддерживаются: <b>XLSX, XLS, CSV</b>.\n"
            f"Размер файла: до <b>{warehouse_importer.MAX_FILE_BYTES / (1024 * 1024):.0f} MB</b>.\n"
            f"Строк на листе: до <b>{warehouse_importer.MAX_ROWS:,}</b>.\n"
            f"Колонок: до <b>{warehouse_importer.MAX_COLUMNS}</b>.\n\n"
            "Файл сначала будет проверен и показан в предпросмотре. "
            "Остатки не изменятся без подтверждения.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К складу",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ]
            ]),
        )
        return

    if data == "admin:warehouse:import:preview":
        pending = context.user_data.get("admin_warehouse_pending_import")
        if not pending:
            await query.answer("Нет файла для предпросмотра.", show_alert=True)
            return

        await query.edit_message_text(
            format_warehouse_file_preview(pending),
            parse_mode=ParseMode.HTML,
            reply_markup=warehouse_file_preview_keyboard(pending),
            disable_web_page_preview=True,
        )
        return

    if data == "admin:warehouse:sheetstart":
        pending = context.user_data.get("admin_warehouse_pending_import")
        if not pending:
            await query.answer("Сначала загрузи файл.", show_alert=True)
            return

        if len(pending.get("sheet_names") or []) <= 1:
            await query.answer("В файле только один лист.", show_alert=True)
            return

        await query.edit_message_text(
            "📑 <b>Выбери лист с остатками</b>",
            parse_mode=ParseMode.HTML,
            reply_markup=warehouse_sheet_keyboard(pending),
        )
        return

    if data.startswith("admin:warehouse:sheet:"):
        pending = context.user_data.get("admin_warehouse_pending_import")
        if not pending:
            await query.answer("Сначала загрузи файл.", show_alert=True)
            return

        try:
            index = int(data.rsplit(":", 1)[1])
        except ValueError:
            await query.answer("Некорректный лист.", show_alert=True)
            return

        sheet_names = pending.get("sheet_names") or []
        if index < 0 or index >= len(sheet_names):
            await query.answer("Лист не найден.", show_alert=True)
            return

        try:
            inspection = await asyncio.to_thread(
                warehouse_importer.inspect_stock_file,
                pending["path"],
                sheet_name=sheet_names[index],
            )
        except Exception as exc:
            log.exception("Could not inspect selected warehouse sheet")
            await query.answer(
                f"Не удалось прочитать лист: {type(exc).__name__}",
                show_alert=True,
            )
            return

        warehouse_id = int(pending["warehouse_id"])
        profile = warehouse_store.find_mapping_profile(
            warehouse_id,
            inspection["file_format"],
            inspection["header_signature"],
            db_file=ORDERS_DB_FILE,
        )
        saved_mapping = (
            dict(profile.get("mapping") or {})
            if profile else {}
        )

        pending["inspection"] = inspection
        pending["mapping"] = (
            saved_mapping
            if (
                "oem" in saved_mapping
                and "quantity" in saved_mapping
            )
            else dict(inspection.get("mapping") or {})
        )
        pending["mapping_profile_id"] = (
            int(profile["id"]) if profile else None
        )
        await asyncio.to_thread(
            refresh_pending_duplicate_stats,
            pending,
        )

        await query.edit_message_text(
            format_warehouse_file_preview(pending),
            parse_mode=ParseMode.HTML,
            reply_markup=warehouse_file_preview_keyboard(pending),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:warehouse:mapstart:"):
        field = data.rsplit(":", 1)[1]
        if field not in {"oem", "quantity"}:
            await query.answer("Неизвестная колонка.", show_alert=True)
            return

        pending = context.user_data.get("admin_warehouse_pending_import")
        if not pending:
            await query.answer("Сначала загрузи файл.", show_alert=True)
            return

        title = "OEM / каталожный номер" if field == "oem" else "Количество"
        await query.edit_message_text(
            f"🧩 <b>Выбери колонку: {title}</b>\n\n"
            "Нажми на название колонки из файла:",
            parse_mode=ParseMode.HTML,
            reply_markup=warehouse_mapping_keyboard(pending, field),
        )
        return

    if data.startswith("admin:warehouse:map:"):
        parts = data.split(":")
        if len(parts) != 5:
            await query.answer("Некорректное сопоставление.", show_alert=True)
            return

        field = parts[3]
        if field not in {"oem", "quantity"}:
            await query.answer("Неизвестная колонка.", show_alert=True)
            return

        try:
            index = int(parts[4])
        except ValueError:
            await query.answer("Некорректная колонка.", show_alert=True)
            return

        pending = context.user_data.get("admin_warehouse_pending_import")
        if not pending:
            await query.answer("Сначала загрузи файл.", show_alert=True)
            return

        headers = pending["inspection"]["headers"]
        if index < 0 or index >= len(headers):
            await query.answer("Колонка не найдена.", show_alert=True)
            return

        pending.setdefault("mapping", {})[field] = index
        await asyncio.to_thread(
            refresh_pending_duplicate_stats,
            pending,
        )

        await query.edit_message_text(
            format_warehouse_file_preview(pending),
            parse_mode=ParseMode.HTML,
            reply_markup=warehouse_file_preview_keyboard(pending),
            disable_web_page_preview=True,
        )
        return

    if data == "admin:warehouse:import:cancel":
        pending = context.user_data.get("admin_warehouse_pending_import")
        warehouse_id = (
            pending.get("warehouse_id")
            if pending
            else context.user_data.get("admin_warehouse_file_upload_id")
        )
        clear_pending_warehouse_import(context)

        if warehouse_id:
            warehouse = warehouse_store.get_warehouse(
                int(warehouse_id),
                db_file=ORDERS_DB_FILE,
            )
            if warehouse:
                await query.edit_message_text(
                    format_admin_warehouse(int(warehouse_id)),
                    parse_mode=ParseMode.HTML,
                    reply_markup=admin_warehouse_keyboard(
                        int(warehouse_id),
                        bool(warehouse["active"]),
                    ),
                    disable_web_page_preview=True,
                )
                return

        await query.edit_message_text(
            format_admin_warehouses(),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouses_keyboard(),
        )
        return

    if data == "admin:warehouse:import:confirm":
        pending = context.user_data.get("admin_warehouse_pending_import")
        if not pending:
            await query.answer("Нет файла для импорта.", show_alert=True)
            return

        mapping = pending.get("mapping") or {}
        if "oem" not in mapping or "quantity" not in mapping:
            await query.answer(
                "Сначала укажи колонки OEM и количества.",
                show_alert=True,
            )
            return

        warehouse_id = int(pending["warehouse_id"])
        file_config = warehouse_store.get_source_config(
            warehouse_id,
            "file",
            db_file=ORDERS_DB_FILE,
        )
        import_mode = file_config.get(
            "import_mode",
            "full_snapshot",
        )
        missing_oem_policy = file_config.get(
            "missing_oem_policy",
            "unknown",
        )
        duplicate_oem_policy = file_config.get(
            "duplicate_oem_policy",
            "sum",
        )

        try:
            prepared = await asyncio.to_thread(
                warehouse_importer.prepare_stock_rows,
                pending["path"],
                mapping=mapping,
                sheet_name=pending["inspection"]["sheet_name"],
                duplicate_oem_policy=duplicate_oem_policy,
            )
            import_id = await asyncio.to_thread(
                warehouse_store.import_prepared_stock,
                warehouse_id=warehouse_id,
                filename=pending["filename"],
                file_format=prepared["file_format"],
                items=prepared["items"],
                rows_total=prepared["rows_total"],
                rows_error=prepared["rows_error"],
                mapping=prepared["mapping"],
                import_mode=import_mode,
                missing_oem_policy=missing_oem_policy,
                duplicate_oem_policy=duplicate_oem_policy,
                db_file=ORDERS_DB_FILE,
            )

            mapping_profile_id = warehouse_store.save_mapping_profile(
                warehouse_id=warehouse_id,
                file_format=prepared["file_format"],
                sheet_name=prepared.get("sheet_name"),
                header_signature=prepared["header_signature"],
                header_row=prepared["header_row"],
                mapping=prepared["mapping"],
                import_mode=import_mode,
                missing_oem_policy=missing_oem_policy,
                db_file=ORDERS_DB_FILE,
            )

            latest_import = warehouse_store.latest_import(
                warehouse_id,
                db_file=ORDERS_DB_FILE,
            )
            absorbed_reservations = 0
            if latest_import:
                absorbed_reservations = await asyncio.to_thread(
                    stock_engine.reconcile_snapshot,
                    warehouse_id=warehouse_id,
                    observed_at=latest_import["imported_at"],
                    oems=(
                        None
                        if import_mode == "full_snapshot"
                        else [item["oem"] for item in prepared["items"]]
                    ),
                    db_file=ORDERS_DB_FILE,
                )
        except warehouse_importer.WarehouseFileError as exc:
            log.warning("Warehouse file import rejected: %s", exc)
            await query.answer(
                f"Импорт не выполнен: {str(exc)}",
                show_alert=True,
            )
            return
        except Exception as exc:
            log.exception("Warehouse file import failed")
            await query.answer(
                f"Импорт не выполнен: {type(exc).__name__}",
                show_alert=True,
            )
            return

        success = prepared["rows_success"]
        errors = prepared["rows_error"]
        duplicate_oems = int(
            prepared.get("duplicate_oems") or 0
        )
        duplicate_rows = int(
            prepared.get("duplicate_rows") or 0
        )
        clear_pending_warehouse_import(context)

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        await query.edit_message_text(
            "✅ <b>Остатки импортированы</b>\n\n"
            f"Импорт: <code>#{import_id}</code>\n"
            f"Успешно: <b>{success}</b>\n"
            f"Пропущено/ошибок: <b>{errors}</b>\n"
            f"Режим: <b>{'полный снимок' if import_mode == 'full_snapshot' else 'delta'}</b>\n"
            f"Нет OEM в файле: <b>{'неизвестно' if missing_oem_policy == 'unknown' else 'считать 0'}</b>\n"
            f"Дубли OEM: <b>{duplicate_oems}</b> · лишних строк <b>{duplicate_rows}</b>\n"
            f"Политика дублей: <b>{duplicate_oem_policy}</b>\n"
            f"Шаблон файла: <code>#{mapping_profile_id}</code> сохранён\n\n"
            + (format_admin_warehouse(warehouse_id) or ""),
            parse_mode=ParseMode.HTML,
            reply_markup=(
                admin_warehouse_keyboard(warehouse_id, bool(warehouse["active"]))
                if warehouse else admin_warehouses_keyboard()
            ),
            disable_web_page_preview=True,
        )
        return

    if data == "admin:search":
        context.user_data["admin_search_mode"] = True

        await query.edit_message_text(
            "🔎 <b>Поиск запросов</b>\n\n"
            "Отправь номер запроса, OEM, имя клиента, username "
            "или Telegram ID.\n\n"
            "Например:\n"
            "<code>E01J-2973</code>\n"
            "<code>E29I-3893</code>\n"
            "<code>@dimych_msk</code>",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ К запросам",
                        callback_data="admin:orders",
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

    if data.startswith("admin:deliverytariff:"):
        context.user_data.pop("admin_delivery_edit", None)

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
    if data.startswith("admin:stock:"):
        order_id = data.split(":", 2)[2]
        await query.edit_message_text(
            format_admin_order_stock(order_id),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_order_stock_keyboard(order_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:stockitem:"):
        parts = data.split(":", 3)
        if len(parts) != 4:
            await query.answer("Некорректная позиция.", show_alert=True)
            return
        order_id = parts[2]
        try:
            order_item_id = int(parts[3])
        except ValueError:
            await query.answer("Некорректная позиция.", show_alert=True)
            return

        text = format_admin_stock_item(order_id, order_item_id)
        if text is None:
            await query.answer("Позиция не найдена.", show_alert=True)
            return

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=admin_stock_item_keyboard(order_id, order_item_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:stockrefresh:"):
        parts = data.split(":", 3)
        if len(parts) != 4:
            await query.answer("Некорректная команда.", show_alert=True)
            return

        order_id = parts[2]
        try:
            order_item_id = int(parts[3])
        except ValueError:
            await query.answer("Некорректная позиция.", show_alert=True)
            return

        item = get_order_item_for_stock(order_id, order_item_id)
        if not item:
            await query.answer("Позиция не найдена.", show_alert=True)
            return

        if get_item_stock_assignment(order_item_id):
            await query.answer(
                "Позиция уже привязана к складу.",
                show_alert=True,
            )
            return

        _id, _position, _manufacturer, oem, _name, _quantity = item
        warehouses = warehouse_store.list_warehouses(
            include_inactive=False,
            db_file=ORDERS_DB_FILE,
        )

        await query.edit_message_text(
            "🌐 <b>Обновляю остатки складов</b>\n\n"
            f"OEM: <code>{escape(str(oem or '—'))}</code>\n"
            f"Проверяю: <b>{len(warehouses)}</b>",
            parse_mode=ParseMode.HTML,
        )

        tasks = [
            asyncio.to_thread(
                warehouse_stock_service.refresh_warehouse_oem,
                int(warehouse["id"]),
                str(oem or ""),
                ORDERS_DB_FILE,
            )
            for warehouse in warehouses
        ]
        refresh_results = await asyncio.gather(
            *tasks,
            return_exceptions=True,
        )

        summary_lines = ["", "<b>Проверка источников:</b>"]
        for warehouse, refresh_result in zip(warehouses, refresh_results):
            if isinstance(refresh_result, Exception):
                status_text = f"ошибка {type(refresh_result).__name__}"
            else:
                status_text = refresh_result.status
                if refresh_result.quantity is not None:
                    status_text += f" · {refresh_result.quantity:g} шт."
            summary_lines.append(
                f"• {escape(warehouse['internal_name'])}: "
                f"{escape(status_text)}"
            )

        stock_engine.cleanup_expired_holds(ORDERS_DB_FILE)
        item_text = format_admin_stock_item(order_id, order_item_id)
        await query.edit_message_text(
            (item_text or "Позиция не найдена.")
            + "\n"
            + "\n".join(summary_lines),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_stock_item_keyboard(
                order_id,
                order_item_id,
            ),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:stockreserve:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Некорректная команда резерва.", show_alert=True)
            return

        order_id = parts[2]
        try:
            order_item_id = int(parts[3])
            warehouse_id = int(parts[4])
        except ValueError:
            await query.answer("Некорректные данные.", show_alert=True)
            return

        item = get_order_item_for_stock(order_id, order_item_id)
        if not item:
            await query.answer("Позиция не найдена.", show_alert=True)
            return

        if get_item_stock_assignment(order_item_id):
            await query.answer(
                "Позиция уже привязана к складу.",
                show_alert=True,
            )
            return

        _id, _position, manufacturer, oem, _name, quantity = item

        try:
            result = stock_engine.reserve_stock(
                warehouse_id=warehouse_id,
                oem=str(oem or ""),
                quantity=float(quantity),
                order_id=order_id,
                order_item_id=order_item_id,
                manufacturer=str(manufacturer or "") or None,
                notes="Reserved from admin order stock screen",
                require_fresh=True,
                db_file=ORDERS_DB_FILE,
            )
        except sqlite3.IntegrityError:
            await query.answer(
                "Позиция уже зарезервирована.",
                show_alert=True,
            )
            return
        except Exception:
            log.exception("Could not reserve warehouse stock")
            await query.answer(
                "Не удалось создать резерв.",
                show_alert=True,
            )
            return

        if not result.get("ok"):
            reason = result.get("reason")
            messages = {
                "stock_unknown": "Остаток по этой позиции неизвестен.",
                "stock_stale": "Данные склада устарели. Сначала обнови остаток.",
                "insufficient_stock": (
                    "Недостаточно остатка на выбранном складе."
                ),
            }
            await query.answer(
                messages.get(reason, "Не удалось создать резерв."),
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_stock_item(order_id, order_item_id)
            or "Позиция не найдена.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_stock_item_keyboard(order_id, order_item_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:stockcommit:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Некорректная команда.", show_alert=True)
            return
        order_id = parts[2]
        try:
            order_item_id = int(parts[3])
            reservation_id = int(parts[4])
        except ValueError:
            await query.answer("Некорректные данные.", show_alert=True)
            return

        if not stock_engine.commit_reservation(
            reservation_id,
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Не удалось отметить передачу складу.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_stock_item(order_id, order_item_id)
            or "Позиция не найдена.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_stock_item_keyboard(order_id, order_item_id),
            disable_web_page_preview=True,
        )
        return

    if data.startswith("admin:stockrelease:"):
        parts = data.split(":", 4)
        if len(parts) != 5:
            await query.answer("Некорректная команда.", show_alert=True)
            return
        order_id = parts[2]
        try:
            order_item_id = int(parts[3])
            reservation_id = int(parts[4])
        except ValueError:
            await query.answer("Некорректные данные.", show_alert=True)
            return

        if not stock_engine.release_reservation(
            reservation_id,
            reason="Released from admin order stock screen",
            db_file=ORDERS_DB_FILE,
        ):
            await query.answer(
                "Не удалось снять резерв.",
                show_alert=True,
            )
            return

        await query.edit_message_text(
            format_admin_stock_item(order_id, order_item_id)
            or "Позиция не найдена.",
            parse_mode=ParseMode.HTML,
            reply_markup=admin_stock_item_keyboard(order_id, order_item_id),
            disable_web_page_preview=True,
        )
        return


async def handle_text(update, context, raw_text):
    message = update.effective_message
    user = update.effective_user
    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_warehouse_manual_stock")
    ):
        state = context.user_data["admin_warehouse_manual_stock"]
        warehouse_id = int(state["warehouse_id"])
        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            context.user_data.pop("admin_warehouse_manual_stock", None)
            await safe_reply_text(message, "⚠️ Склад не найден.")
            return

        if state.get("step") == "oem":
            oem = re.sub(r"\s+", "", raw_text.strip()).upper()
            if not oem or len(oem) > 64:
                await safe_reply_text(
                    message,
                    "⚠️ Введи корректный OEM / каталожный номер.",
                )
                return

            state["oem"] = oem
            state["step"] = "quantity"
            source = warehouse_store.get_source(
                warehouse_id,
                "manual",
                db_file=ORDERS_DB_FILE,
            )
            ttl = source.get("ttl_minutes") if source else None
            ttl_text = (
                "без ограничения срока"
                if ttl is None
                else f"{ttl} мин."
            )

            await safe_reply_text(
                message,
                "✏️ <b>Ручной остаток</b>\n\n"
                f"Склад: <b>{escape(warehouse['internal_name'])}</b>\n"
                f"OEM: <code>{escape(oem)}</code>\n\n"
                "Теперь отправь количество:\n"
                "• <code>12</code> — в наличии 12 шт.\n"
                "• <code>0</code> — нет в наличии\n"
                "• <code>?</code> — количество неизвестно\n"
                "• <code>-</code> — снять ручную корректировку\n\n"
                f"Текущий TTL ручных данных: <b>{escape(ttl_text)}</b>",
                parse_mode=ParseMode.HTML,
            )
            return

        oem = str(state.get("oem") or "").strip()
        value = raw_text.strip()

        if value in {"-", "—"}:
            removed = warehouse_store.clear_current_source_stock(
                warehouse_id,
                oem,
                "manual",
                db_file=ORDERS_DB_FILE,
            )
            context.user_data.pop("admin_warehouse_manual_stock", None)
            await safe_reply_text(
                message,
                (
                    "✅ <b>Ручная корректировка снята.</b>"
                    if removed
                    else "ℹ️ Ручной корректировки для этого OEM не было."
                )
                + "\n\n"
                + format_admin_warehouse_stockcheck(
                    warehouse_id,
                    oem,
                ),
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton(
                            "✏️ Другой OEM",
                            callback_data=f"admin:warehouse:manual:{warehouse_id}",
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "⬅️ К складу",
                            callback_data=f"admin:warehouse:view:{warehouse_id}",
                        )
                    ],
                ]),
            )
            return

        if value in {"?", "неизвестно", "unknown"}:
            quantity = None
            status = "quantity_unknown"
        else:
            try:
                quantity = int(value)
                if quantity < 0:
                    raise ValueError
            except ValueError:
                await safe_reply_text(
                    message,
                    "⚠️ Введи целое количество ≥ 0, "
                    "<code>?</code> или <code>-</code>.",
                    parse_mode=ParseMode.HTML,
                )
                return
            status = "in_stock" if quantity > 0 else "out_of_stock"

        warehouse_store.record_source_stock(
            warehouse_id=warehouse_id,
            oem=oem,
            status=status,
            quantity=quantity,
            source_type="manual",
            source_record={
                "entered_by": int(user.id),
                "entry_mode": "admin_manual",
            },
            db_file=ORDERS_DB_FILE,
        )
        context.user_data.pop("admin_warehouse_manual_stock", None)

        await safe_reply_text(
            message,
            "✅ <b>Ручной остаток сохранён.</b>\n\n"
            + format_admin_warehouse_stockcheck(
                warehouse_id,
                oem,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "✏️ Другой OEM",
                        callback_data=f"admin:warehouse:manual:{warehouse_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К складу",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ],
            ]),
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_warehouse_stockcheck_id")
    ):
        warehouse_id = int(context.user_data["admin_warehouse_stockcheck_id"])
        oem = raw_text.strip()
        if not oem:
            await safe_reply_text(message, "⚠️ Отправь OEM / каталожный номер.")
            return

        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        if not warehouse:
            context.user_data.pop("admin_warehouse_stockcheck_id", None)
            await safe_reply_text(message, "⚠️ Склад не найден.")
            return

        stock_engine.cleanup_expired_holds(ORDERS_DB_FILE)

        refresh_result = await asyncio.to_thread(
            warehouse_stock_service.refresh_warehouse_oem,
            warehouse_id,
            oem,
            ORDERS_DB_FILE,
        )
        stock_text = format_admin_warehouse_stockcheck(
            warehouse_id,
            oem,
        )
        if refresh_result.status == "check_failed":
            error_name = refresh_result.details.get("error") or "check_failed"
            stock_text += (
                "\n\n⚠️ <b>Живая проверка сайта не удалась.</b>"
                f"\n<code>{escape(str(error_name))}</code>"
                "\nПоказаны последние сохранённые данные, если они есть."
            )
        else:
            stock_text += (
                "\n\n🌐 Живая проверка сайта: "
                f"<b>{escape(refresh_result.status)}</b>"
            )

        await safe_reply_text(
            message,
            stock_text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "🔎 Проверить другой OEM",
                        callback_data=f"admin:warehouse:stockcheck:{warehouse_id}",
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ К складу",
                        callback_data=f"admin:warehouse:view:{warehouse_id}",
                    )
                ],
            ]),
            disable_web_page_preview=True,
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_warehouse_source_edit")
    ):
        edit = context.user_data["admin_warehouse_source_edit"]
        warehouse_id = int(edit["warehouse_id"])
        source_type = str(edit["source_type"])
        field = str(edit["field"])
        value = raw_text.strip()

        source = warehouse_store.get_source(
            warehouse_id,
            source_type,
            db_file=ORDERS_DB_FILE,
        )
        if not source:
            context.user_data.pop("admin_warehouse_source_edit", None)
            await safe_reply_text(message, "⚠️ Источник не найден.")
            return

        if field == "ttl":
            if value in {"-", "—"}:
                new_value = None
            else:
                try:
                    new_value = int(value)
                    if new_value <= 0 or new_value > 10080:
                        raise ValueError
                except ValueError:
                    await safe_reply_text(
                        message,
                        "⚠️ TTL должен быть целым числом от 1 до 10080 минут "
                        "или <code>-</code> для режима без TTL.",
                        parse_mode=ParseMode.HTML,
                    )
                    return
            db_field = "ttl_minutes"
        elif field == "priority":
            try:
                new_value = int(value)
                if new_value < 0 or new_value > 999:
                    raise ValueError
            except ValueError:
                await safe_reply_text(
                    message,
                    "⚠️ Приоритет должен быть целым числом от 0 до 999.",
                )
                return
            db_field = "priority"
        else:
            context.user_data.pop("admin_warehouse_source_edit", None)
            await safe_reply_text(message, "⚠️ Неизвестное поле источника.")
            return

        if not warehouse_store.update_source_field(
            warehouse_id,
            source_type,
            db_field,
            new_value,
            db_file=ORDERS_DB_FILE,
        ):
            await safe_reply_text(
                message,
                "⚠️ Не удалось сохранить настройку источника.",
            )
            return

        context.user_data.pop("admin_warehouse_source_edit", None)
        await safe_reply_text(
            message,
            "✅ <b>Настройка источника сохранена.</b>\n\n"
            + format_admin_warehouse_source(
                warehouse_id,
                source_type,
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_source_keyboard(
                warehouse_id,
                source_type,
            ),
            disable_web_page_preview=True,
        )
        return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_warehouse_add")
    ):
        state = context.user_data["admin_warehouse_add"]
        step = state["step"]
        data = state["data"]
        value = raw_text.strip()

        if not value:
            await safe_reply_text(message, "⚠️ Значение не может быть пустым.")
            return

        if step == "internal_name":
            data["internal_name"] = value
            state["step"] = "city"
            await safe_reply_text(
                message,
                "➕ <b>Новый склад</b>\n\n"
                "Шаг 2/5. Введи город.\n\n"
                "Например: <code>Москва</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "city":
            data["city"] = value
            state["step"] = "code"
            await safe_reply_text(
                message,
                "➕ <b>Новый склад</b>\n\n"
                "Шаг 3/5. Введи короткий код склада.\n\n"
                "Например: <code>МСК</code> или <code>МСК2</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "code":
            code = value.upper().replace(" ", "")
            if not re.fullmatch(r"[A-Za-zА-Яа-я0-9-]{2,12}", code):
                await safe_reply_text(
                    message,
                    "⚠️ Код должен содержать 2–12 букв/цифр без пробелов.\n"
                    "Например: <code>МСК2</code>",
                    parse_mode=ParseMode.HTML,
                )
                return
            data["code"] = code
            state["step"] = "public_name"
            await safe_reply_text(
                message,
                "➕ <b>Новый склад</b>\n\n"
                "Шаг 4/5. Введи название, которое разрешено видеть клиенту.\n\n"
                f"Например: <code>склад {escape(code)}</code>",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "public_name":
            data["public_name"] = value
            state["step"] = "website_url"
            await safe_reply_text(
                message,
                "➕ <b>Новый склад</b>\n\n"
                "Шаг 5/5. Отправь адрес сайта.\n\n"
                "Например: <code>https://example.ru/</code>\n"
                "Если сайта нет — отправь <code>-</code>.",
                parse_mode=ParseMode.HTML,
            )
            return

        if step == "website_url":
            website_url = None if value in {"-", "—"} else value
            if website_url and not website_url.startswith(("http://", "https://")):
                website_url = "https://" + website_url

            try:
                warehouse_id = warehouse_store.add_warehouse(
                    internal_name=data["internal_name"],
                    city=data["city"],
                    code=data["code"],
                    public_name=data["public_name"],
                    website_url=website_url,
                    adapter_type=None,
                    priority=100,
                    db_file=ORDERS_DB_FILE,
                )
                warehouse_store.ensure_source(
                    warehouse_id,
                    "manual",
                    enabled=True,
                    priority=10,
                    ttl_minutes=60,
                    config={"manual_ttl_policy_version": 1},
                    db_file=ORDERS_DB_FILE,
                )
                warehouse_store.ensure_source(
                    warehouse_id,
                    "website",
                    enabled=False,
                    priority=20,
                    ttl_minutes=30,
                    db_file=ORDERS_DB_FILE,
                )
                warehouse_store.ensure_source(
                    warehouse_id,
                    "file",
                    enabled=False,
                    priority=30,
                    ttl_minutes=240,
                    config={
                        "import_mode": "full_snapshot",
                        "missing_oem_policy": "unknown",
                    },
                    db_file=ORDERS_DB_FILE,
                )
            except sqlite3.IntegrityError:
                state["step"] = "code"
                await safe_reply_text(
                    message,
                    "⚠️ Склад с таким названием, кодом или клиентским именем "
                    "уже существует.\n\n"
                    "Введи другой код склада, например <code>МСК2</code>.",
                    parse_mode=ParseMode.HTML,
                )
                return
            except Exception:
                log.exception("Could not create warehouse")
                await safe_reply_text(message, "⚠️ Не удалось создать склад.")
                return

            context.user_data.pop("admin_warehouse_add", None)
            warehouse = warehouse_store.get_warehouse(
                warehouse_id,
                db_file=ORDERS_DB_FILE,
            )
            await safe_reply_text(
                message,
                "✅ <b>Склад создан.</b>\n\n"
                + (format_admin_warehouse(warehouse_id) or ""),
                parse_mode=ParseMode.HTML,
                reply_markup=admin_warehouse_keyboard(
                    warehouse_id,
                    bool(warehouse["active"]) if warehouse else True,
                ),
                disable_web_page_preview=True,
            )
            return

    if (
        user
        and user.id == RATE_ADMIN_USER_ID
        and context.user_data.get("admin_warehouse_edit")
    ):
        edit = context.user_data["admin_warehouse_edit"]
        warehouse_id = int(edit["warehouse_id"])
        field = edit["field"]
        value: str | int | None = raw_text.strip()

        if field == "priority":
            try:
                value = int(str(value))
                if value < 0:
                    raise ValueError
            except ValueError:
                await safe_reply_text(
                    message,
                    "⚠️ Приоритет должен быть целым неотрицательным числом.",
                )
                return
        elif field == "code":
            value = str(value).upper().replace(" ", "")
            if not re.fullmatch(r"[A-Za-zА-Яа-я0-9-]{2,12}", value):
                await safe_reply_text(
                    message,
                    "⚠️ Код должен содержать 2–12 букв/цифр без пробелов.",
                )
                return
        elif field == "website_url":
            if str(value) in {"-", "—"}:
                value = None
            elif not str(value).startswith(("http://", "https://")):
                value = "https://" + str(value)
        elif field in {"adapter_type", "notes"} and str(value) in {"-", "—"}:
            value = None
        elif not str(value).strip():
            await safe_reply_text(message, "⚠️ Значение не может быть пустым.")
            return

        try:
            changed = warehouse_store.update_warehouse_field(
                warehouse_id,
                field,
                value,
                db_file=ORDERS_DB_FILE,
            )
        except sqlite3.IntegrityError:
            await safe_reply_text(
                message,
                "⚠️ Такое значение уже используется другим складом.",
            )
            return
        except Exception:
            log.exception("Could not edit warehouse")
            await safe_reply_text(message, "⚠️ Не удалось изменить склад.")
            return

        if not changed:
            await safe_reply_text(message, "⚠️ Склад не найден.")
            return

        context.user_data.pop("admin_warehouse_edit", None)
        warehouse = warehouse_store.get_warehouse(
            warehouse_id,
            db_file=ORDERS_DB_FILE,
        )
        await safe_reply_text(
            message,
            "✅ <b>Изменение сохранено.</b>\n\n"
            + (format_admin_warehouse(warehouse_id) or ""),
            parse_mode=ParseMode.HTML,
            reply_markup=admin_warehouse_keyboard(
                warehouse_id,
                bool(warehouse["active"]) if warehouse else True,
            ),
            disable_web_page_preview=True,
        )
        return


async def _admin_document_message_impl(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message

    if not user or user.id != RATE_ADMIN_USER_ID or _is_group_chat(update):
        return

    warehouse_id = context.user_data.get("admin_warehouse_file_upload_id")
    if not warehouse_id:
        return

    document = message.document
    if not document:
        return

    filename = document.file_name or "warehouse_stock"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".xlsx", ".xls", ".csv"}:
        await safe_reply_text(
            message,
            "⚠️ Поддерживаются только файлы <b>XLSX, XLS и CSV</b>.",
            parse_mode=ParseMode.HTML,
        )
        return

    file_size = getattr(document, "file_size", None)
    if (
        file_size is not None
        and int(file_size) > warehouse_importer.MAX_FILE_BYTES
    ):
        await safe_reply_text(
            message,
            "⚠️ <b>Файл слишком большой.</b>\n\n"
            f"Размер: <b>{int(file_size) / (1024 * 1024):.1f} MB</b>\n"
            f"Лимит: <b>{warehouse_importer.MAX_FILE_BYTES / (1024 * 1024):.1f} MB</b>\n\n"
            "Попроси поставщика прислать файл меньшего размера "
            "или увеличь системный лимит склада.",
            parse_mode=ParseMode.HTML,
        )
        return

    warehouse = warehouse_store.get_warehouse(
        int(warehouse_id),
        db_file=ORDERS_DB_FILE,
    )
    if not warehouse:
        clear_pending_warehouse_import(context)
        await safe_reply_text(message, "⚠️ Склад не найден.")
        return

    old_pending = context.user_data.pop("admin_warehouse_pending_import", None)
    if old_pending and old_pending.get("path"):
        try:
            Path(old_pending["path"]).unlink(missing_ok=True)
        except OSError:
            pass

    upload_dir = Path(__file__).with_name("_warehouse_uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)

    safe_name = re.sub(r"[^A-Za-zА-Яа-я0-9._-]+", "_", filename)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    target = upload_dir / f"{warehouse_id}_{stamp}_{safe_name}"

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        await telegram_file.download_to_drive(custom_path=target)
        inspection = await asyncio.to_thread(
            warehouse_importer.inspect_stock_file,
            target,
        )
    except warehouse_importer.WarehouseFileError as exc:
        log.warning(
            "Warehouse stock file rejected: %s",
            exc,
        )
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        await safe_reply_text(
            message,
            "⚠️ <b>Файл остатков отклонён.</b>\n\n"
            f"{escape(str(exc))}\n\n"
            "Остатки склада не изменены.",
            parse_mode=ParseMode.HTML,
        )
        return
    except Exception as exc:
        log.exception("Could not inspect warehouse stock file")
        try:
            target.unlink(missing_ok=True)
        except OSError:
            pass
        await safe_reply_text(
            message,
            "⚠️ <b>Не удалось прочитать файл остатков.</b>\n\n"
            f"Ошибка: <code>{escape(type(exc).__name__)}</code>\n\n"
            "Остатки склада не изменены.",
            parse_mode=ParseMode.HTML,
        )
        return

    profile = warehouse_store.find_mapping_profile(
        int(warehouse_id),
        inspection["file_format"],
        inspection["header_signature"],
        db_file=ORDERS_DB_FILE,
    )
    saved_mapping = (
        dict(profile.get("mapping") or {})
        if profile else {}
    )

    pending = {
        "warehouse_id": int(warehouse_id),
        "path": str(target),
        "filename": filename,
        "sheet_names": list(
            inspection.get("available_sheets")
            or [inspection["sheet_name"]]
        ),
        "inspection": inspection,
        "mapping": (
            saved_mapping
            if (
                "oem" in saved_mapping
                and "quantity" in saved_mapping
            )
            else dict(inspection.get("mapping") or {})
        ),
        "mapping_profile_id": (
            int(profile["id"]) if profile else None
        ),
    }
    await asyncio.to_thread(
        refresh_pending_duplicate_stats,
        pending,
    )
    context.user_data["admin_warehouse_pending_import"] = pending

    await safe_reply_text(
        message,
        format_warehouse_file_preview(pending),
        parse_mode=ParseMode.HTML,
        reply_markup=warehouse_file_preview_keyboard(pending),
        disable_web_page_preview=True,
    )


def _warehouse_file_work_lock(
    context: ContextTypes.DEFAULT_TYPE,
) -> asyncio.Lock:
    key = "warehouse_file_work_lock"
    lock = context.application.bot_data.get(key)
    if lock is None:
        lock = asyncio.Lock()
        context.application.bot_data[key] = lock
    return lock


async def admin_document_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    user = update.effective_user
    message = update.effective_message
    if (
        not user
        or user.id != RATE_ADMIN_USER_ID
        or _is_group_chat(update)
        or not context.user_data.get(
            "admin_warehouse_file_upload_id"
        )
    ):
        return

    lock = _warehouse_file_work_lock(context)
    if lock.locked():
        await safe_reply_text(
            message,
            "⏳ <b>Файл склада уже обрабатывается.</b>\n\n"
            "Дождись результата текущего файла и затем отправь следующий.",
            parse_mode=ParseMode.HTML,
        )
        return

    async with lock:
        await _admin_document_message_impl(update, context)


async def handle_file_callback_nonblocking(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    data = str(query.data or "") if query else ""
    user = update.effective_user

    is_warehouse_callback = (
        data == "admin:warehouses"
        or data.startswith("admin:warehouse:")
        or data.startswith("admin:stock")
    )
    if (
        query is None
        or not user
        or user.id != RATE_ADMIN_USER_ID
        or not is_warehouse_callback
    ):
        return

    lock = _warehouse_file_work_lock(context)
    if lock.locked():
        await query.answer(
            "Файл склада уже обрабатывается.",
            show_alert=True,
        )
        return

    await query.answer()
    async with lock:
        await handle_callback(update, context, data)
