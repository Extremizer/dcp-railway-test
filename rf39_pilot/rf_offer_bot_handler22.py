"""RF OFFER existing handler-22 -> RF39 website-first quote/recheck.

Exactly the already-registered /rf command and rf:offer:* callback entrypoints.
No new bot, router, polling, DB adapter, API or feed. Telegram stays LIVE HOLD.
Supplier/site identifiers below are ISOLATED PRESENTATION KEYS, not verified
production warehouse DB IDs; never transfer them into V2 or Orders.
"""
from __future__ import annotations

import re
import asyncio
from .rf_supplier_data39 import preview_lookup, recheck_before_order, RF39Stop
from .rf_offer_cart_preview21 import Screen, OfferPreviewHold

LIVE_HOLD = True
PREFLIGHT_VERIFIED = False
TELEGRAM_WRITE_ENABLED = False
V2_WRITE_ENABLED = False
ORDERS_WRITE_ENABLED = False
OUTBOX_WRITE_ENABLED = False

TEST_CLIENT_ID = 7005635854
_PILOT_IDS = frozenset((TEST_CLIENT_ID, 52637605))
RF39_TG_PILOT = True

# These are locally scoped DISPLAY identifiers, NOT real warehouse database IDs.
# Labels for ЯРС and КРС are taken from existing warehouse_store seed source.
_SITE_PREVIEW_KEYS = {"orangeatv": 1, "motoservice76": 2, "vladextremelife": 3}
_SITE_CHOICES = {
    "orangeatv:main": ("orangeatv", "Основной склад", "OrangeATV / Основной склад"),
    "motoservice76:main": ("motoservice76", "склад ЯРС", "Ярославль / Мотосервис 76"),
    "vladextremelife:main": ("vladextremelife", "склад КРС", "Красноярск / Vlad Extreme Life"),
}


def _require_live_permission():
    if not RF39_TG_PILOT:
        raise OfferPreviewHold("STOP: RF Telegram handler not authorized")


def _authorized_private_user(update) -> int:
    user = getattr(update, "effective_user", None)
    chat = getattr(update, "effective_chat", None)
    uid = getattr(user, "id", None)
    if (type(uid) is not int or uid not in _PILOT_IDS
        or getattr(chat, "type", None) != "private"
        or getattr(chat, "id", None) != uid):
        raise OfferPreviewHold("RF private client scope not authorized")
    return uid


def _identity(manufacturer: str, oem: str):
    if (not isinstance(manufacturer, str) or not manufacturer.strip()
        or not isinstance(oem, str)):
        raise OfferPreviewHold("manufacturer and OEM required")
    manufacturer = manufacturer.strip()
    oem = oem.strip().upper().replace(" ", "")
    if (len(manufacturer) > 80
        or not re.fullmatch(r"[A-Z0-9._/-]{2,80}", oem)):
        raise OfferPreviewHold("manufacturer or OEM invalid")
    return manufacturer, oem


def _quote_all(manufacturer: str, oem: str):
    """Call existing RF39 site adapters; no legacy/fake offers."""
    quotes = {}
    for key, (adapter, internal, _label) in _SITE_CHOICES.items():
        try:
            quotes[key] = preview_lookup(
                adapter_type=adapter, manufacturer=manufacturer, oem=oem,
                warehouse_id=_SITE_PREVIEW_KEYS[adapter],
                supplier_internal_warehouse=internal,
                verified_warehouse_map=_SITE_PREVIEW_KEYS)
        except (RF39Stop, ValueError, KeyError, OSError, TimeoutError) as exc:
            quotes[key] = {"site_evidence_status": "SITE_CHECK_FAILED",
                           "can_add_to_cart_preview": False}
    return quotes


def route_offers_or_preview(*, actor_id: int, manufacturer: str,
                            oem: str, user_data: dict, callback: str | None = None):
    """Direct in-process website-first path behind existing handler-22."""
    if type(actor_id) is not int or actor_id not in _PILOT_IDS:
        raise OfferPreviewHold("not approved private client")
    if not isinstance(user_data, dict):
        raise OfferPreviewHold("isolated in-memory session required")
    manufacturer, oem = _identity(manufacturer, oem)
    if callback is None:
        quotes = _quote_all(manufacturer, oem)
        user_data["rf39_site_quotes"] = {
            "manufacturer": manufacturer, "oem": oem, "offers": quotes}
        return _render_quotes(manufacturer, oem, quotes)
    record = user_data.get("rf39_site_quotes")
    if (not isinstance(record, dict)
        or record.get("manufacturer") != manufacturer
        or record.get("oem") != oem
        or not isinstance(record.get("offers"), dict)):
        raise OfferPreviewHold("RF39 session expired; repeat /rf")
    if callback == "rf:offer:show":
        fresh = _quote_all(manufacturer, oem)
        record["offers"] = fresh
        return _render_quotes(manufacturer, oem, fresh)
    is_add = isinstance(callback, str) and callback.startswith("rf:offer:add:")
    prefix = "rf:offer:add:" if is_add else "rf:offer:choose:"
    if not isinstance(callback, str) or not callback.startswith(prefix):
        raise OfferPreviewHold("unsupported rf callback; checkout remains HOLD")
    key = callback[len(prefix):]
    if key not in _SITE_CHOICES:
        raise OfferPreviewHold("unknown RF39 website source")
    previous = record["offers"].get(key)
    if not isinstance(previous, dict) or previous.get("can_add_to_cart_preview") is not True:
        raise OfferPreviewHold("no usable website offer; repeat /rf")
    adapter, internal, label = _SITE_CHOICES[key]
    legacy_key = f"{manufacturer}|{oem}"
    cart = user_data.get("cart", {})
    if not isinstance(cart, dict):
        raise OfferPreviewHold("existing cart is invalid")
    existing = cart.get(legacy_key)
    if is_add and existing is not None:
        if (not isinstance(existing, dict)
            or existing.get("_rf_supplier") != adapter
            or existing.get("_rf_supplier_internal_warehouse") != internal
            or existing.get("_rf_checkout_allowed") is not False
            or existing.get("manufacturer") != manufacturer
            or existing.get("oem") != oem
            or type(existing.get("qty")) is not int):
            raise OfferPreviewHold("OEM already belongs to a different cart source")
    requested_qty = (existing["qty"] + 1) if is_add and existing else 1
    if not 1 <= requested_qty <= 99:
        raise OfferPreviewHold("RF quantity limit reached")
    try:
        checked = recheck_before_order(
            previous_offer=previous, adapter_type=adapter,
            manufacturer=manufacturer, oem=oem, quantity=requested_qty,
            warehouse_id=_SITE_PREVIEW_KEYS[adapter],
            supplier_internal_warehouse=internal,
            verified_warehouse_map=_SITE_PREVIEW_KEYS)
    except RF39Stop:
        return Screen("Сайт поставщика не подтвердил предложение. Выполни /rf ещё раз.",
                      (("Обновить проценку", "rf:offer:show"),))
    latest = checked["current_offer"]
    if not checked["site_recheck_pass"]:
        return Screen(
            f"{manufacturer} {oem} / {label}\n"
            f"Изменилось предложение: {checked['recheck_status']}. "
            "Обнови проценку. Оформление заблокировано.",
            (("Обновить проценку", "rf:offer:show"),))
    if is_add:
        # Existing context.user_data['cart'] format; no DB, V2 or Orders.
        # The RF price is site-observed RUB, never a USA dealer/USD price.
        cart[legacy_key] = {
            "manufacturer": manufacturer, "oem": oem,
            "requested_oem": oem, "qty": requested_qty,
            "name": f"{manufacturer} OEM {oem}",
            "price": None, "catalog": None,
            "_rf_supplier": adapter,
            "_rf_supplier_internal_warehouse": internal,
            "_rf_price_rub_preview": latest["price_rub_preliminary"],
            "_rf_price_status": latest["price_status"],
            "_rf_site_stock_observed": latest["site_quantity_observed"],
            "_rf_site_product_url": latest["site_product_url"],
            "_rf_site_observed_at": latest["site_observed_at"],
            "_rf_price_source": latest["price_provenance"],
            "_rf_checkout_allowed": False,
        }
        user_data["cart"] = cart
        return Screen(
            f"В существующую корзину добавлено: {manufacturer} {oem}, "
            f"{requested_qty} шт. / {label}.\n"
            f"Цена сайта: {latest['price_rub_preliminary']} ₽/шт. "
            "Резерв не гарантирован; оформление заблокировано.",
            (("Обновить проценку", "rf:offer:show"),))
    return Screen(
        f"{manufacturer} {oem}\n{label}\n"
        f"Цена на сайте: {latest['price_rub_preliminary']} ₽\n"
        f"На сайте указано: {latest['site_quantity_observed']} шт.\n"
        "Повторная проверка сайта: совпало. Резерв не гарантирован.",
        (("Добавить в корзину", "rf:offer:add:" + key),
         ("Обновить проценку", "rf:offer:show")))


def _render_quotes(manufacturer, oem, quotes):
    lines = [f"Проценка по сайтам РФ: {manufacturer} {oem}."]
    buttons = []
    for key, (adapter, internal, label) in _SITE_CHOICES.items():
        item = quotes.get(key) or {}
        if item.get("can_add_to_cart_preview") is True:
            price = item["price_rub_preliminary"]
            qty = item["site_quantity_observed"]
            lines.append(f"{label}: {price} ₽ / {qty} шт. на сайте")
            buttons.append((f"{label} — {price} ₽",
                            "rf:offer:choose:" + key))
        else:
            state = item.get("site_evidence_status", "SITE_CHECK_FAILED")
            lines.append(f"{label}: нет проверяемого предложения ({state})")
    lines.append("Цены и наличие — наблюдения сайтов, не резерв. "
                 "Корзина и оформление пока на HOLD.")
    return Screen("\n".join(lines), tuple(buttons))


def _tg_keyboard(screen):
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=value)]
        for label, value in screen.buttons
    ]) if screen.buttons else None


async def rf_pilot_command(update, context):
    """Existing /rf handler only. Accept '/rf BRP 417300574'."""
    _require_live_permission()
    uid = _authorized_private_user(update)
    args = list(getattr(context, "args", None) or [])
    if len(args) != 2:
        await update.effective_message.reply_text(
            "Для проценки по сайтам введи: /rf BRP 417300574")
        return
    mfg, oem = _identity(*args)
    screen = await asyncio.to_thread(
        route_offers_or_preview, actor_id=uid, manufacturer=mfg,
        oem=oem, user_data=context.user_data)
    await update.effective_message.reply_text(
        screen.text, reply_markup=_tg_keyboard(screen))


async def rf_pilot_callback(update, context):
    """Existing manufacturer_callback already dispatches rf:offer:* here."""
    _require_live_permission()
    uid = _authorized_private_user(update)
    query = update.callback_query
    record = context.user_data.get("rf39_site_quotes") or {}
    await query.answer()
    screen = await asyncio.to_thread(
        route_offers_or_preview, actor_id=uid,
        manufacturer=record.get("manufacturer"),
        oem=record.get("oem"), user_data=context.user_data,
        callback=query.data)
    await query.edit_message_text(screen.text,
                                  reply_markup=_tg_keyboard(screen))
