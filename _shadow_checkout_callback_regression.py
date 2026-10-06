#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Callback-level regression for the unregistered shadow checkout path."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace

from client_checkout_callback_adapter import (
    CheckoutAdapterResult,
    CheckoutPostCommitError,
)


ROOT = Path(__file__).resolve().parent
BOT_SOURCE = ROOT / "extremizer_bot.py"


def require(value, message):
    if not value:
        raise AssertionError(message)


def compile_shadow():
    source = BOT_SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(BOT_SOURCE))
    target = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.AsyncFunctionDef)
            and node.name == "_shadow_checkout_confirm"
        ),
        None,
    )
    require(target is not None, "shadow checkout function missing")
    live_start = source.index('    if data == "checkout_confirm":')
    live_end = source.index('\n    if data == "change_mfg":', live_start)
    live_block = source[live_start:live_end]
    require(
        "_shadow_checkout_confirm(" not in live_block,
        "live checkout path calls shadow function",
    )
    require(
        source.count("_shadow_checkout_confirm(") == 1,
        "shadow function must remain definition-only",
    )
    module = ast.Module(body=[target], type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module, str(BOT_SOURCE), "exec")


class DummyButton:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class DummyMarkup:
    def __init__(self, rows):
        self.rows = rows


class DummyParseMode:
    HTML = "HTML"


class DummyContextTypes:
    DEFAULT_TYPE = object


class DummyQuery:
    def __init__(self):
        self.answers = []
        self.edits = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.answers.append((text, show_alert))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append(text)


class DummyUser:
    id = 7005635854
    full_name = "Shadow Client"
    username = "shadow"


class DummyUpdate:
    def __init__(self, query):
        self.callback_query = query
        self.effective_user = DummyUser()


class DummyBot:
    async def send_message(self, **kwargs):
        return {"ok": True}


class DummyContext:
    def __init__(self, cart):
        self.user_data = {
            "cart": cart,
            "warehouse_recipient": {"name": "Test"},
        }
        self.bot = DummyBot()


class WarehouseRecipientStub:
    PROMPTS = {"name": "name"}

    @staticmethod
    def has_warehouse(cart):
        return any(
            str(x.get("offer_source") or "usa").lower() == "warehouse"
            for x in cart.values()
        )

    @staticmethod
    def complete(recipient):
        return bool(recipient)

    @staticmethod
    def next_field(recipient):
        return "name"

    @staticmethod
    def normalize(recipient):
        return dict(recipient)


class SupplierStub:
    def create_from_client_order(self, order_id, recipient):
        return [{"order_id": order_id}]


class WebStub:
    @staticmethod
    def link_handoff_order(*args, **kwargs):
        return True


class LogStub:
    @staticmethod
    def exception(*args, **kwargs):
        return None


def base_cart():
    return {
        "usa": {
            "manufacturer": "Ski-Doo",
            "oem": "417224332",
            "qty": 1,
            "offer_source": "usa",
            "selected_delivery_tariff": "comfort",
        },
        "warehouse": {
            "manufacturer": "Test",
            "oem": "WH-1",
            "qty": 1,
            "offer_source": "warehouse",
            "warehouse_id": 1,
            "warehouse_public_name": "склад МСК",
        },
    }


def build_shadow(mode):
    code = compile_shadow()
    calls = []

    async def revalidate(item):
        return {"status": "fresh"}

    async def execute_prepared_checkout(**kwargs):
        calls.append(kwargs)
        if mode == "core_failure":
            raise ValueError("core failed")
        if mode in {
            "manager_failure",
            "supplier_failure",
            "reserve_failure",
            "quote_failure",
        }:
            action = {
                "manager_failure": "manager_notification",
                "supplier_failure": "supplier_orders",
                "reserve_failure": "warehouse_reservation",
                "quote_failure": "auto_quote_promotion",
            }[mode]
            completed = {
                "manager_failure": (),
                "supplier_failure": ("manager_notification",),
                "reserve_failure": (
                    "manager_notification",
                    "supplier_orders",
                ),
                "quote_failure": (
                    "manager_notification",
                    "supplier_orders",
                    "warehouse_reservation",
                    "web_handoff_link",
                ),
            }[mode]
            raise CheckoutPostCommitError(
                action=action,
                core_result={
                    "order_id": "E-SHADOW-1",
                    "client_id": "7005635854",
                    "finance_event_id": 1,
                },
                completed_actions=completed,
                cause=RuntimeError(action),
            )

        warnings = (
            ("web_handoff_link_failed",)
            if mode == "web_warning"
            else ()
        )
        if kwargs.get("finalize_session"):
            kwargs["finalize_session"]()
        return CheckoutAdapterResult(
            order_id="E-SHADOW-1",
            client_id="7005635854",
            finance_event_id=1,
            completed_actions=(
                "manager_notification",
                "supplier_orders",
                "warehouse_reservation",
                "auto_quote_promotion",
                "finalize_session",
            ),
            warnings=warnings,
            auto_quote="QUOTE",
        )

    namespace = {
        "Update": DummyUpdate,
        "ContextTypes": DummyContextTypes,
        "ParseMode": DummyParseMode,
        "InlineKeyboardButton": DummyButton,
        "InlineKeyboardMarkup": DummyMarkup,
        "escape": lambda x: str(x),
        "cart_keyboard": lambda cart: DummyMarkup([]),
        "warehouse_recipient": WarehouseRecipientStub,
        "_revalidate_warehouse_cart_item": revalidate,
        "prepare_cart_delivery_choices": lambda cart: None,
        "cart_delivery_choice_complete": lambda cart: True,
        "common_cart_delivery_preference": lambda cart: "comfort",
        "format_cart": lambda cart, checkout=False: "CART",
        "format_checkout_delivery_choices": lambda cart: "DELIVERY",
        "checkout_delivery_keyboard": lambda cart: DummyMarkup([]),
        "MANAGER_CHAT_ID": "manager-chat",
        "generate_order_id": lambda: "E-SHADOW-1",
        "format_manager_order": lambda *args, **kwargs: "MANAGER",
        "supplier_order_service": SupplierStub(),
        "reserve_local_order_items": lambda order_id: {"ok": True},
        "web_handoff": WebStub,
        "prepare_auto_quote_after_checkout": lambda *args: "QUOTE",
        "DELIVERY_SEPARATE_NOTICE": "delivery separate",
        "ORDERS_DB_FILE": Path("shadow.db"),
        "save_order_to_history": lambda *args, **kwargs: None,
        "CheckoutPostCommitError": CheckoutPostCommitError,
        "execute_prepared_checkout": execute_prepared_checkout,
        "log": LogStub,
    }
    exec(code, namespace)
    return namespace["_shadow_checkout_confirm"], calls


async def run_case(mode):
    shadow, calls = build_shadow(mode)
    query = DummyQuery()
    context = DummyContext(base_cart())
    update = DummyUpdate(query)
    result = await shadow(
        update,
        context,
        finance_adapter=object(),
    )
    return result, context, query, calls


async def run_async():
    # Success.
    result, context, query, calls = await run_case("success")
    require(result["status"] == "success", "success status")
    require(context.user_data["cart"] == {}, "success cart not cleared")
    require(len(calls) == 1, "adapter not called exactly once")
    require(
        calls[0]["order_id"] == "E-SHADOW-1",
        "wrong order id passed to adapter",
    )

    # Core failure: cart stays, no committed replay marker.
    result, context, query, calls = await run_case("core_failure")
    require(result["status"] == "core_failed", "core failure status")
    require(context.user_data["cart"], "core failure cleared cart")
    require(
        "checkout_committed_order_id" not in context.user_data,
        "core failure created committed marker",
    )

    # Post-commit failures: durable marker + cart preserved.
    mapping = {
        "manager_failure": "manager_notification",
        "supplier_failure": "supplier_orders",
        "reserve_failure": "warehouse_reservation",
        "quote_failure": "auto_quote_promotion",
    }
    for mode, action in mapping.items():
        result, context, query, calls = await run_case(mode)
        require(
            result["status"] == "committed_post_action_failed",
            f"{mode} status",
        )
        require(result["failed_action"] == action, f"{mode} action")
        require(context.user_data["cart"], f"{mode} cleared cart")
        require(
            context.user_data["checkout_committed_order_id"]
            == "E-SHADOW-1",
            f"{mode} missing replay marker",
        )

        # Replay guard must not call adapter again.
        shadow, replay_calls = build_shadow("success")
        replay_query = DummyQuery()
        replay_update = DummyUpdate(replay_query)
        replay = await shadow(
            replay_update,
            context,
            finance_adapter=object(),
        )
        require(replay["status"] == "committed_pending", f"{mode} replay")
        require(replay_calls == [], f"{mode} replay called adapter")

    # WEB warning remains success and finalizes session.
    result, context, query, calls = await run_case("web_warning")
    require(result["status"] == "success", "web warning blocked success")
    require(
        result["warnings"] == ("web_handoff_link_failed",),
        "web warning missing",
    )
    require(context.user_data["cart"] == {}, "web warning did not finalize")

    print("PASS shadow checkout callback-level regression")
    print(
        "success / core failure / manager failure / supplier failure / "
        "reserve failure / web warning / quote failure"
    )
    print("replay guard blocks duplicate adapter invocation after committed failure")
    print("live checkout_confirm remains unconnected to shadow path")


def run():
    asyncio.run(run_async())


if __name__ == "__main__":
    run()
