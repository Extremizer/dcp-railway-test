from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

import oemixibot_app as app


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.reply_text = AsyncMock()


class FakeQuery:
    def __init__(self, data):
        self.data = data
        self.answer = AsyncMock()
        self.edit_message_text = AsyncMock()


class AdminOemSearchTests(unittest.TestCase):
    def _context(self, *, admin_search=False):
        return SimpleNamespace(
            user_data={"admin_oem_search": True} if admin_search else {},
            application=SimpleNamespace(bot_data={}, create_task=Mock()),
        )

    def _update(self, *, user_id=1001, text="", callback_data=None):
        user = SimpleNamespace(
            id=user_id,
            username="admin",
            first_name="Admin",
            last_name="User",
        )
        message = FakeMessage(text)
        query = FakeQuery(callback_data) if callback_data is not None else None
        return SimpleNamespace(
            effective_user=user,
            effective_chat=SimpleNamespace(id=4242),
            effective_message=message,
            callback_query=query,
        )

    def test_admin_menu_contains_oem_search_and_does_not_require_dealer(self):
        update = self._update()
        context = self._context()
        with patch.object(app, "is_telegram_admin", return_value=True),              patch.object(app, "_admin_dealers", return_value=[]),              patch.object(app, "_dealer", side_effect=AssertionError("dealer lookup forbidden")):
            asyncio.run(app.admin_command(update, context))

        update.effective_message.reply_text.assert_awaited_once()
        _text, kwargs = update.effective_message.reply_text.await_args
        keyboard = kwargs["reply_markup"]
        labels = [button.text for row in keyboard.inline_keyboard for button in row]
        callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
        self.assertIn("🔎 Поиск по OEM", labels)
        self.assertIn("admsearch", callbacks)

    def test_non_admin_cannot_open_admin_search(self):
        update = self._update(callback_data="admsearch")
        context = self._context()
        with patch.object(app, "is_telegram_admin", return_value=False):
            asyncio.run(app.callback(update, context))
        update.callback_query.edit_message_text.assert_awaited_once_with(app.ACCESS_DENIED)
        self.assertNotIn("admin_oem_search", context.user_data)

    def test_admin_search_prompt_and_return_to_admin(self):
        context = self._context()
        update = self._update(callback_data="admsearch")
        with patch.object(app, "is_telegram_admin", return_value=True):
            asyncio.run(app.callback(update, context))
        self.assertNotIn("admin_oem_search", context.user_data)
        self.assertIn(
            "Выберите производителя",
            update.callback_query.edit_message_text.await_args.args[0],
        )

        back = self._update(callback_data="admhome")
        with patch.object(app, "is_telegram_admin", return_value=True),              patch.object(app, "_admin_dealers", return_value=[]):
            asyncio.run(app.callback(back, context))
        self.assertNotIn("admin_oem_search", context.user_data)
        self.assertEqual(
            back.callback_query.edit_message_text.await_args.args[0],
            "⚙️ OEMixiBOT ADMIN",
        )

    def test_admin_search_uses_runtime_offer_without_dealer_or_finance_side_effects(self):
        update = self._update(text="2206328")
        context = self._context(admin_search=True)
        context.user_data["manufacturer"] = "Polaris"
        offer = {
            "status": "FOUND",
            "manufacturer": "Polaris",
            "oem": "2206328",
            "item_type": "parts",
            "dl_usd": 184.07,
            "msrp_usd": 239.99,
            "source": "verified_shared_gateway",
            "last_verified_at": "2026-10-15T08:00:00Z",
        }
        sent = AsyncMock()
        context.bot = SimpleNamespace(send_message=sent)

        async def run_admin_probe():
            tasks = []
            def schedule(coro, *, name=None):
                task = asyncio.create_task(coro, name=name)
                tasks.append(task)
                return task
            with patch.object(context.application, "create_task", side_effect=schedule):
                await app.oem_message(update, context)
                if tasks:
                    await asyncio.gather(*tasks)

        with patch.object(app, "is_telegram_admin", return_value=True),              patch.object(app, "_resolve_admin_oem_offer", new=AsyncMock(return_value=offer)) as resolver,              patch.object(app, "_dealer", side_effect=AssertionError("dealer lookup forbidden")),              patch.object(app.STORE, "cart_add", side_effect=AssertionError("cart mutation forbidden")),              patch.object(app.STORE, "checkout_cart", side_effect=AssertionError("checkout forbidden")),              patch.object(app.FINANCE, "payment", side_effect=AssertionError("finance mutation forbidden")),              patch.object(app.FINANCE, "adjustment_minus", side_effect=AssertionError("finance mutation forbidden")):
            asyncio.run(run_admin_probe())

        resolver.assert_awaited_once()
        text = sent.await_args.kwargs["text"]
        self.assertIn("Дилерская цена: $184.07", text)
        self.assertIn("MSRP: $239.99", text)
        self.assertNotIn("➕ В корзину", text)

    def test_admin_search_denies_non_admin_even_without_dealer(self):
        update = self._update(user_id=2002, text="2206328")
        context = self._context(admin_search=True)
        with patch.object(app, "is_telegram_admin", return_value=False),              patch.object(app, "_resolve_admin_oem_offer", new=AsyncMock()) as resolver:
            asyncio.run(app.oem_message(update, context))
        resolver.assert_not_awaited()
        self.assertNotIn("admin_oem_search", context.user_data)
        update.effective_message.reply_text.assert_awaited_once_with(app.ACCESS_DENIED)

    def test_dealer_and_msrp_are_independent_fields(self):
        both = app._admin_offer_text(
            "2206328",
            {
                "status": "FOUND",
                "manufacturer": "Polaris",
                "oem": "2206328",
                "dl_usd": 184.07,
                "msrp_usd": 239.99,
            },
        )
        self.assertIn("Дилерская цена: $184.07", both)
        self.assertIn("MSRP: $239.99", both)

        dealer_only = app._admin_offer_text(
            "2206328",
            {
                "status": "FOUND",
                "manufacturer": "Polaris",
                "oem": "2206328",
                "dl_usd": 184.07,
                "msrp_usd": None,
            },
        )
        self.assertIn("Дилерская цена: $184.07", dealer_only)
        self.assertNotIn("MSRP:", dealer_only)

        msrp_only = app._admin_offer_text(
            "2206328",
            {
                "status": "FOUND",
                "manufacturer": "Polaris",
                "oem": "2206328",
                "dl_usd": None,
                "msrp_usd": 239.99,
                "price_error": "dealer_price_missing",
            },
        )
        self.assertIn("Дилерская цена недоступна", msrp_only)
        self.assertIn("MSRP не используется вместо неё", msrp_only)
        self.assertIn("MSRP: $239.99", msrp_only)

    def test_identity_retry_reuses_same_runtime_boundary(self):
        context = self._context(admin_search=True)
        calls = AsyncMock(side_effect=[
            {"status": "IDENTITY_REQUIRED", "oem": "2206328", "dl_usd": None},
            {"status": "FOUND", "oem": "2206328", "manufacturer": "Polaris", "dl_usd": 184.07},
        ])
        with patch.object(app, "_resolve_runtime_offer", new=calls),              patch.object(app, "lookup_shared_identity", return_value={"manufacturer": "Polaris", "item_type": "parts"}),              patch.object(app.oem_identity_service, "remember", return_value=True):
            result = asyncio.run(app._resolve_admin_oem_offer("2206328", context))
        self.assertEqual(result["dl_usd"], 184.07)
        self.assertEqual(calls.await_count, 2)
        for call in calls.await_args_list:
            self.assertEqual(call.args, ("2206328", context))


if __name__ == "__main__":
    unittest.main(verbosity=2)
