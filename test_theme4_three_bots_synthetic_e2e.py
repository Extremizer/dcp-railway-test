# -*- coding: utf-8 -*-
"""EXTREMIZER / 3 bot handler E2E, synthetic transport only.

Exercises actual production Telegram handler functions with deterministic fake
updates and controlled DP/stock outputs. Never calls Telegram, Railway, DCP,
live SQLite, cart mutation, or external networks.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import manufacturer_first_ux as ux
import extremizer_bot as pricing
import probnik_app as probnik
import oemixibot_app as dealerbot


class Message:
    def __init__(self, raw):
        self.text = raw
        self.answers = []

    async def reply_text(self, text, **kwargs):
        self.answers.append(("reply", text))
        return self

    async def edit_text(self, text, **kwargs):
        self.answers.append(("edit", text))
        return self


def fixture(oem="417300574", manufacturer="Ski-Doo"):
    message = Message(oem)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(id=103, type="private"),
        effective_user=SimpleNamespace(id=104, username=None, first_name=None, last_name=None),
    )
    context = SimpleNamespace(
        user_data={},
        args=[],
        application=SimpleNamespace(
            bot_data={},
            create_task=lambda coro: coro.close(),
        ),
    )
    assert ux.choose(context.user_data, manufacturer)
    return message, update, context


def test_probnik_real_handler_price_miss_preserves_rf_stock():
    message, update, ctx = fixture()
    info = {
        "manufacturer": "Ski-Doo", "oem": "417300574",
        "customer_rub": None, "rrp_rub": None,
    }
    stocks = [
        {"public_name": "Склад 1", "is_fresh": True,
         "available_quantity": 26, "price_rub": 34700},
        {"public_name": "Склад 3", "is_fresh": True,
         "available_quantity": 6, "price_rub": 38700},
        {"public_name": "Склад 2", "is_fresh": False,
         "available_quantity": 8, "price_rub": 9999},
    ]
    with (
        patch.object(probnik, "_identity_and_price", return_value=info) as price,
        patch.object(probnik, "_stock_with_refresh", new_callable=AsyncMock, return_value=stocks) as stock,
        patch.object(probnik.pricing_analytics, "begin_request_from_update", return_value="synthetic"),
        patch.object(probnik.pricing_analytics, "complete_request") as analytics,
    ):
        asyncio.run(probnik.oem_message(update, ctx))
    price.assert_called_once_with("417300574", "Ski-Doo")
    stock.assert_awaited_once_with("417300574")
    final = [text for kind, text in message.answers if kind == "edit"]
    assert len(final) == 1
    result = final[0]
    assert "Цена сейчас недоступна" in result
    assert "Склад 1" in result and "26 шт." in result
    assert "Склад 3" in result and "6 шт." in result
    assert "Склад 2" not in result  # stale stock is never displayed
    assert "9999" not in result
    assert analytics.call_args.kwargs["price_status"] == "UNAVAILABLE"
    assert analytics.call_args.kwargs["stock_rf_status"] == "FOUND"


def test_probnik_real_handler_verified_price_and_stock():
    message, update, ctx = fixture()
    info = {
        "manufacturer": "Ski-Doo", "oem": "417300574",
        "customer_rub": 29200, "rrp_rub": None,
    }
    stocks = [{"public_name": "Склад 1", "is_fresh": True,
               "available_quantity": 1, "price_rub": 34700}]
    with (
        patch.object(probnik, "_identity_and_price", return_value=info),
        patch.object(probnik, "_stock_with_refresh", new_callable=AsyncMock, return_value=stocks),
        patch.object(probnik.pricing_analytics, "begin_request_from_update", return_value="synthetic"),
        patch.object(probnik.pricing_analytics, "complete_request") as analytics,
    ):
        asyncio.run(probnik.oem_message(update, ctx))
    final = [text for kind, text in message.answers if kind == "edit"]
    assert len(final) == 1
    assert "29 200* ₽" in final[0]
    assert "доставки" in final[0]
    assert "Склад 1" in final[0]
    assert "РРЦ" not in final[0]  # MSRP never substituted for DP
    assert analytics.call_args.kwargs["price_status"] == "FOUND"


def test_oemixi_real_handler_gateway_miss_fails_closed():
    message, update, ctx = fixture()
    result = {
        "status": "PRICE_UNAVAILABLE", "manufacturer": "Ski-Doo",
        "oem": "417300574", "dl_usd": None,
    }
    with (
        patch.object(dealerbot, "_dealer", return_value={"id": 1, "price_coefficient": 1.0}),
        patch.object(dealerbot, "_resolve_runtime_offer", new_callable=AsyncMock, return_value=result) as gateway,
        patch.object(dealerbot, "send_oem_request_async", new_callable=AsyncMock),
        patch.object(dealerbot.STORE, "cart_view") as cart,
    ):
        asyncio.run(dealerbot.oem_message(update, ctx))
    gateway.assert_awaited_once_with("417300574", ctx, manufacturer="Ski-Doo")
    cart.assert_not_called()
    assert any("Цена из США сейчас недоступна" in ans
               for _, ans in message.answers)
    assert "offer:417300574" not in ctx.user_data


def test_oemixi_real_handler_verified_cart_dealer_price_no_cart_mutation():
    message, update, ctx = fixture()
    verified = {
        "status": "FOUND", "manufacturer": "Ski-Doo",
        "oem": "417300574", "dl_usd": 88.19, "name": "Drive Belt",
        "item_type": "OEM", "actual_weight_kg": None,
        "volume_weight_kg": None,
    }
    with (
        patch.object(dealerbot, "_dealer", return_value={"id": 1, "price_coefficient": 1.0}),
        patch.object(dealerbot, "_resolve_runtime_offer", new_callable=AsyncMock, return_value=verified) as gateway,
        patch.object(dealerbot, "price_card", return_value="SYNTHETIC VERIFIED CART PRICE") as card,
        patch.object(dealerbot, "send_oem_request_async", new_callable=AsyncMock),
        patch.object(dealerbot.STORE, "cart_view", return_value={"items": [], "total_usd": 0}) as cart,
    ):
        asyncio.run(dealerbot.oem_message(update, ctx))
    gateway.assert_awaited_once()
    cart.assert_called_once_with(1)
    assert card.call_args.args[3] == 88.19
    assert "offer:417300574" in ctx.user_data
    assert any("SYNTHETIC VERIFIED CART PRICE" in ans for _, ans in message.answers)
    # The last reply includes a Telegram inline keyboard; inspect the
    # reply markup from a separate captured call, not the message text.
    assert any("SYNTHETIC VERIFIED CART PRICE" in answer
               for kind, answer in message.answers if kind == "reply")
    assert ctx.user_data["offer:417300574"]["dl_usd"] == 88.19


def test_procenka_p0_handler_uses_manufacturer_first_without_crash():
    from test_procenka_mixed_mode_p0 import _run
    message, context, finder, analytics = _run("417300574")
    finder.assert_awaited_once_with("Ski-Doo", "417300574")
    assert context.user_data["last_search_was_mixed"] is False
    assert any(kind == "edit" and "FOUND SYNTHETIC" in answer
               for kind, answer in message.events)
    analytics.assert_called_once()
