"""Complete synthetic mf callback against app handler, no Telegram network."""
import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock,patch

import oemixibot_app as app

def ctx(nonce="abcdef01", uid=51, dealer="d1"):
    gateway=Mock()
    return SimpleNamespace(
        user_data={"unknown_manufacturer_pending":{"oem":"3211070","nonce":nonce,
                    "dealer_id":dealer,"user_id":uid}},
        application=SimpleNamespace(bot_data={
            app._SHARED_PRICE_ADAPTER_KEY:gateway,
            "oemixi_verified_category_bootstrap":Mock()}),
    )

def upd(data="mf:abcdef01:8",uid=51):
    q=SimpleNamespace(data=data,answer=AsyncMock(),edit_message_text=AsyncMock())
    return SimpleNamespace(callback_query=q,effective_user=SimpleNamespace(id=uid))

class CallbackEnrollmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.dealer={"id":"d1","price_coefficient":1.25}
        self.p=patch.object(app,"_dealer",return_value=self.dealer)
        self.p.start()
    async def asyncTearDown(self):
        self.p.stop()
    async def test_nonce_wrong_never_calls_stage_a_or_stage_b(self):
        context=ctx();update=upd("mf:wrong000:8")
        with patch.object(app,"validate_selected_manufacturer") as verifier:
            await app.callback(update,context)
            verifier.assert_not_called()
        self.assertNotIn("unknown_manufacturer_pending",context.user_data)
    async def test_wrong_user_fails_closed(self):
        context=ctx();update=upd(uid=52)
        with patch.object(app,"validate_selected_manufacturer") as verifier:
            await app.callback(update,context)
            verifier.assert_not_called()
    async def test_wrong_dealer_fails_closed(self):
        context=ctx(dealer="other");update=upd()
        with patch.object(app,"validate_selected_manufacturer") as verifier:
            await app.callback(update,context)
            verifier.assert_not_called()
    async def test_stage_a_failure_no_stage_b_or_identity(self):
        context=ctx();update=upd()
        with patch.object(app,"validate_selected_manufacturer",return_value={"status":"CATEGORY_UNVERIFIED"}):
            with patch.object(app,"_resolve_runtime_offer",new_callable=AsyncMock) as lookup:
                await app.callback(update,context)
                lookup.assert_not_called()
        self.assertNotIn("offer:3211070",context.user_data)
    async def test_polaris_success_to_price_card(self):
        choices=app.manufacturer_choices()
        ix=choices.index("Polaris")
        context=ctx(); update=upd(f"mf:abcdef01:{ix}")
        offer={"status":"FOUND","manufacturer":"Polaris","oem":"3211070",
               "name":"DRIVE BELT","dl_usd":89.0,"item_type":"Parts",
               "actual_weight_kg":None,"volume_weight_kg":None}
        with patch.object(app,"validate_selected_manufacturer",return_value={
            "status":"VERIFIED","manufacturer":"Polaris","oem":"3211070"
        }) as validate:
            with patch.object(app,"_resolve_runtime_offer",new_callable=AsyncMock,return_value=offer) as lookup:
                with patch.object(app,"price_card",return_value="TEST PRICE CARD") as card:
                    await app.callback(update,context)
                    validate.assert_called_once()
                    self.assertEqual(validate.call_args.kwargs["category_bootstrap"],
                                     context.application.bot_data["oemixi_verified_category_bootstrap"])
                    lookup.assert_awaited_once_with("3211070",context)
                    card.assert_called_once()
                    update.callback_query.edit_message_text.assert_awaited_once()
                    self.assertEqual(context.user_data["offer:3211070"],offer)
if __name__=="__main__":
    unittest.main()
