"""RF-first router for a FUTURE single existing @Extremizer_bot Application.

PREP-05 only: no Telegram connection, polling, DB creation, Orders/outbox writes.
This module is inert unless a future independent GO deliberately replaces
the fixed HOLD and provides verified, injected RF/V2 dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, Any

LIVE_HOLD = True
PREFLIGHT_VERIFIED = False
REAL_ORDERS_WRITE_ENABLED = False
REAL_OUTBOX_WRITE_ENABLED = False
TELEGRAM_WRITE_ENABLED = False
AUTHORIZED_TEST_CLIENT_ID = 7005635854
ADMIN_USER_ID = 52637605
RF_CALLBACK_PREFIX = "rf:"


class RFRouterHold(RuntimeError):
    pass


@dataclass(frozen=True)
class ExistingCart:
    warehouse_id: int
    manufacturer: str
    oem: str
    quantity: int
    scope: str

    def verified(self) -> bool:
        return (
            type(self.warehouse_id) is int and self.warehouse_id > 0
            and self.scope == f"rf:{self.warehouse_id}"
            and bool(self.manufacturer.strip()) and bool(self.oem.strip())
            and type(self.quantity) is int and self.quantity >= 1
        )


class ExistingCartReader(Protocol):
    def __call__(self, actor_id: int) -> ExistingCart | None: ...


class RFBindingPort(Protocol):
    """Injected existing RF binding using the authorized V2/stock ports."""
    def search(self, user_id: int, user_data: dict,
               manufacturer: str, oem: str) -> Any: ...
    async def callback(self, user_id: int, user_data: dict, value: str) -> Any: ...
    def text(self, user_id: int, user_data: dict, value: str) -> Any: ...


@dataclass
class RFRouter:
    binding: RFBindingPort
    existing_cart: ExistingCartReader

    @staticmethod
    def client_id(update) -> int | None:
        user = getattr(update, "effective_user", None)
        chat = getattr(update, "effective_chat", None)
        uid = getattr(user, "id", None)
        return (uid if type(uid) is int
                and uid == AUTHORIZED_TEST_CLIENT_ID
                and uid != ADMIN_USER_ID
                and getattr(chat, "id", None) == uid
                and getattr(chat, "type", None) == "private"
                else None)

    @staticmethod
    def _screen(text: str, buttons=()):
        from rf_tg_client_binding import Screen
        return Screen(text, list(buttons))

    def entry(self, update, user_data: dict):
        """Read-only cart discovery; never select/add/create a V2 cart here."""
        uid = self.client_id(update)
        if uid is None:
            raise RFRouterHold("client identity not authorized")
        cart = self.existing_cart(uid)
        if cart is not None:
            if not isinstance(cart, ExistingCart) or not cart.verified():
                raise RFRouterHold("unverified RF cart; STOP")
            user_data.pop("rf_entry_stage", None)
            return self._screen(
                f"Корзина РФ: {cart.manufacturer} / {cart.oem}, {cart.quantity} шт.",
                [("Продолжить оформление", f"rf:checkout:{cart.warehouse_id}")],
            )
        user_data["rf_entry_stage"] = "manufacturer"
        return self._screen("Введи производителя для поиска запчастей РФ.")

    def on_text(self, update, user_data: dict, text: str):
        """Receives text only in explicitly activated RF state."""
        uid = self.client_id(update)
        if uid is None:
            return None
        if (user_data.get("rf_synthetic_order", {}).get("checkout")
                or user_data.get("rf_readonly_checkout", {}).get("step")):
            return self.binding.text(uid, user_data, text)
        stage = user_data.get("rf_entry_stage")
        value = str(text or "").strip()
        if not value:
            raise ValueError("empty RF input")
        if stage == "manufacturer":
            if len(value) > 80:
                raise ValueError("manufacturer too long")
            user_data["rf_search_manufacturer"] = value
            user_data["rf_entry_stage"] = "oem"
            return self._screen("Введи OEM для проверки на складе РФ.")
        if stage == "oem":
            manufacturer = user_data.get("rf_search_manufacturer")
            if not manufacturer:
                raise RFRouterHold("RF manufacturer not selected")
            screen = self.binding.search(uid, user_data, manufacturer, value)
            user_data["rf_entry_stage"] = "offers"
            return screen
        return None

    async def on_callback(self, update, user_data: dict, value: str):
        uid = self.client_id(update)
        if uid is None:
            raise RFRouterHold("client identity not authorized")
        if not isinstance(value, str) or not value.startswith(RF_CALLBACK_PREFIX):
            raise RFRouterHold("not an RF callback")
        if value.startswith("rf:confirm:"):
            raise RFRouterHold("Orders and outbox remain HOLD under PREP-05")
        # All RF operations remain inaccessible to live Telegram until
        # an independent OWNER GO supplies production ports and unlocks this module.
        return await self.binding.callback(uid, user_data, value)


# No global bot, DB, token or Application instance.
_router: RFRouter | None = None


def configure_router(*, binding: RFBindingPort, existing_cart: ExistingCartReader):
    """Factory only: does not access a DB, network or Telegram."""
    if not callable(existing_cart):
        raise RFRouterHold("explicit read-only existing-cart reader required")
    return RFRouter(binding=binding, existing_cart=existing_cart)


def _require_live_authorization():
    if (LIVE_HOLD or not PREFLIGHT_VERIFIED or
            not TELEGRAM_WRITE_ENABLED):
        raise RFRouterHold("STOP: RF handler wiring / Telegram WRITE not authorized")


async def route_rf_text(update, context):
    """Hook called BEFORE existing text handler only if future GO enables it."""
    _require_live_authorization()
    if _router is None:
        raise RFRouterHold("STOP: no approved RF dependency binding")
    screen = _router.on_text(update, context.user_data,
                             getattr(update.effective_message, "text", ""))
    if screen is None:
        return False
    await _send_screen(update.effective_message, screen)
    return True


async def route_rf_entry(update, context):
    _require_live_authorization()
    if _router is None:
        raise RFRouterHold("STOP: no approved RF dependency binding")
    await _send_screen(update.effective_message,
                       _router.entry(update, context.user_data))


async def route_rf_callback(update, context):
    _require_live_authorization()
    if _router is None:
        raise RFRouterHold("STOP: no approved RF dependency binding")
    query = update.callback_query
    await query.answer()
    screen = await _router.on_callback(update, context.user_data, query.data)
    await query.edit_message_text(screen.text, reply_markup=_keyboard(screen))


def _keyboard(screen):
    if not screen.buttons:
        return None
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data=callback)]
        for label, callback in screen.buttons
    ])


async def _send_screen(message, screen):
    await message.reply_text(screen.text, reply_markup=_keyboard(screen))


def attach_to_working_bot(application):
    """Single authorized insertion point; unconditionally STOP in PREP-05.

    Future GO: register CommandHandler("rf", route_rf_entry) and
    CallbackQueryHandler(route_rf_callback, pattern=r"^rf:")
    ON the existing Application BEFORE its unfiltered callback handler.
    Never register another Application or run_polling.
    """
    _require_live_authorization()
    raise RFRouterHold("STOP: attachment requires separate OWNER GO and approved ports")
