# -*- coding: utf-8 -*-
"""P0 OEM text handler: execute production callback with synthetic Telegram updates.

No live Telegram, DCP, Railway or database calls. This isolates the bug where
text_message used mixed_mode before defining it for a manufacturer-first OEM.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import extremizer_bot as pricing


class FakeMessage:
    def __init__(self, raw):
        self.text = raw
        self.events = []

    async def reply_text(self, text, **kwargs):
        self.events.append(("reply", text))
        return self

    async def edit_text(self, text, **kwargs):
        self.events.append(("edit", text))
        return self


def _run(oem_text):
    message = FakeMessage(oem_text)
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type="private", id=1234),
        effective_user=SimpleNamespace(id=4321, username=None, first_name=None, last_name=None),
    )
    context = SimpleNamespace(
        user_data={"manufacturer": "Ski-Doo"},
        args=[],
        application=SimpleNamespace(bot_data={}, create_task=lambda task: task.close()),
    )

    async def reply(msg, text, **kwargs):
        return await msg.reply_text(text, **kwargs)

    async def search(manufacturer, oem):
        assert manufacturer == "Ski-Doo"
        return {"status": "FOUND", "manufacturer": manufacturer, "oem": oem}

    async def enrich(result):
        return {**result, "_dealer_price_usd": None}

    with (
        patch.object(pricing, "RATE_ADMIN_USER_ID", -1000),
        patch.object(pricing, "_is_group_chat", return_value=False),
        patch.object(pricing, "safe_reply_text", side_effect=reply),
        patch.object(pricing.finder_service, "search", side_effect=search) as finder,
        patch.object(pricing, "enrich_found_result_with_dealer_price", side_effect=enrich),
        patch.object(pricing.pricing_analytics, "begin_request_from_update", return_value="synthetic"),
        patch.object(pricing.pricing_analytics, "complete_request") as analytics_done,
        patch.object(pricing, "customer_rub_price_from_dp", return_value=None),
        patch.object(pricing.warehouse_stock_service, "client_stock_summary", return_value=[]),
        patch.object(pricing, "_format_client_stock_message", return_value="known"),
        patch.object(pricing, "format_client_offer_card", return_value="FOUND SYNTHETIC"),
        patch.object(pricing, "keyboard_for_result", return_value=None),
        patch.object(pricing, "cache_client_offer_result"),
        patch.object(pricing, "batch_summary", return_value="BATCH COMPLETE"),
        patch.object(pricing, "result_keyboard", return_value=None),
    ):
        asyncio.run(pricing.text_message(update, context))
    return message, context, finder, analytics_done


def test_one_oem_reaches_search_without_nameerror():
    message, context, finder, analytics = _run("417300574")
    finder.assert_awaited_once_with("Ski-Doo", "417300574")
    assert context.user_data["last_search_was_mixed"] is False
    assert context.user_data["last_jobs"] == [("Ski-Doo", "417300574")]
    assert any(kind == "edit" and "FOUND SYNTHETIC" in text
               for kind, text in message.events)
    analytics.assert_called_once()


def test_two_oems_reach_batch_path_without_nameerror():
    message, context, finder, analytics = _run("417300574 860200987")
    assert finder.await_count == 2
    assert [call.args for call in finder.await_args_list] == [
        ("Ski-Doo", "417300574"), ("Ski-Doo", "860200987")
    ]
    assert context.user_data["last_search_was_mixed"] is False
    assert len(context.user_data["last_batch"]) == 2
    assert any("BATCH COMPLETE" in text for kind, text in message.events)
    assert analytics.call_count == 2


def test_no_selected_manufacturer_denies_search():
    message = FakeMessage("417300574")
    update = SimpleNamespace(
        effective_message=message,
        effective_chat=SimpleNamespace(type="private", id=1234),
        effective_user=SimpleNamespace(id=4321),
    )
    context = SimpleNamespace(user_data={}, args=[])
    async def reply(msg, text, **kwargs):
        return await msg.reply_text(text, **kwargs)
    with (
        patch.object(pricing, "RATE_ADMIN_USER_ID", -1000),
        patch.object(pricing, "_is_group_chat", return_value=False),
        patch.object(pricing, "safe_reply_text", side_effect=reply),
        patch.object(pricing.finder_service, "search", new_callable=AsyncMock) as finder,
    ):
        asyncio.run(pricing.text_message(update, context))
    finder.assert_not_awaited()
    assert any("производителя" in text for kind, text in message.events)
