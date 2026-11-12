"""Offline full path: committed SQLite -> signed HTTP -> real OEMixi handler.

Run this file directly. No polling, Telegram delivery, DCP, or production DB.
The price is synthetic acceptance evidence, not a current production quote.
"""
import asyncio
from contextlib import ExitStack
from datetime import timedelta
from pathlib import Path
import socket
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import dcp_price_gateway as gateway
import oemixibot_app as bot
import oemixibot_catalog as catalog
import test_ski_doo_exact_gateway_release_candidate as fixture
from oemixi_secure_gateway_transport import (
    HTTPSPriceTransport, OEMixiCanonicalSharedPriceAdapter, build_router,
)
from test_oemixi_secure_gateway_transport import local_opener

SECRET = "synthetic-ski-doo-acceptance-secret"


class SkiDooBotAcceptance(unittest.TestCase):
    def setUp(self):
        fixture.ExactSkiDooGateway.setUp(self)
        self.addCleanup(lambda: fixture.ExactSkiDooGateway.tearDown(self))
        network = patch.object(socket.socket, "connect",
                               side_effect=AssertionError("external network forbidden"))
        network.start()
        self.addCleanup(network.stop)
        self.live = Mock(return_value={"status": "AUTH_REQUIRED"})
        self.core = Mock(side_effect=lambda m, o, **kw: gateway.request_price(
            self.path, m, o, live_request=self.live, **kw))
        api = FastAPI()
        api.include_router(build_router(
            db_file=self.path, root_secret=SECRET, shared_gateway=self.core,
            server_max_age_hours=24, msrp_max_age_hours=720, wait_seconds=1,
            msrp_reader=lambda *a, **kw: {},
        ))
        self.client = TestClient(api)
        self.addCleanup(self.client.close)

    def reply(self, *, manufacturer="Ski-Doo", secret=SECRET):
        transport = HTTPSPriceTransport(
            "https://synthetic.test", secret, opener=local_opener(self.client))
        adapter = OEMixiCanonicalSharedPriceAdapter(transport, max_age_hours=24)
        application = SimpleNamespace(bot_data={bot._SHARED_PRICE_ADAPTER_KEY: adapter})
        context = SimpleNamespace(
            application=application,
            user_data={"manufacturer": manufacturer} if manufacturer else {},
        )
        reply = AsyncMock()
        update = SimpleNamespace(
            effective_user=None,
            effective_message=SimpleNamespace(text=fixture.OEM, reply_text=reply),
        )
        with ExitStack() as stack:
            stack.enter_context(patch.object(bot, "_dealer", return_value={
                "id": "synthetic-authorized-dealer", "price_coefficient": 1}))
            stack.enter_context(patch.object(bot.STORE, "cart_view",
                                            return_value={"items": [], "total_usd": 0}))
            stack.enter_context(patch.object(catalog.oem_reference_service,
                                            "lookup_oem", return_value={}))
            infer = stack.enter_context(patch.object(
                catalog, "resolve_identity", side_effect=AssertionError("no inferred manufacturer")))
            direct = stack.enter_context(patch.object(
                catalog.oemixibot_dcp_price, "get_dealer_price_dict",
                side_effect=AssertionError("no direct browser")))
            write = stack.enter_context(patch.object(
                catalog, "confirm_identity", side_effect=AssertionError("no identity mutation")))
            asyncio.run(bot.oem_message(update, context))
            infer.assert_not_called()
            direct.assert_not_called()
            write.assert_not_called()
        self.assertEqual(reply.await_count, 1)
        return reply.await_args.args[0], context

    def assert_unavailable(self, **kw):
        text, context = self.reply(**kw)
        self.assertNotIn("offer:" + fixture.OEM, context.user_data)
        self.assertNotIn("$207.07", text)
        return text

    def test_committed_price_reaches_actual_bot_reply(self):
        text, context = self.reply()
        self.assertIn("$207.07", text)
        self.assertEqual(context.user_data["offer:" + fixture.OEM]["requested_oem"], fixture.OEM)
        self.assertEqual(context.user_data["offer:" + fixture.OEM]["manufacturer"], "Ski-Doo")
        self.assertEqual(context.user_data["offer:" + fixture.OEM]["dl_usd"], 207.07)
        self.live.assert_not_called()
        self.assertEqual(self.core.call_count, 1)
        print("OFFLINE_BOT_REPLY:", text.replace("\n", " | "))

    def test_stale_price_never_reaches_reply(self):
        fixture.ExactSkiDooGateway.update_both_stamp(
            self, (fixture.NOW - timedelta(hours=25)).isoformat())
        self.assertIn("недоступна", self.assert_unavailable())

    def test_uncommitted_price_never_reaches_reply(self):
        fixture.ExactSkiDooGateway.update_request(self, "status", "claimed")
        self.assertIn("недоступна", self.assert_unavailable())

    def test_msrp_source_never_becomes_dealer_price(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE dealer_price_cache SET source='public_msrp'")
            db.execute("UPDATE dp_live_requests SET result_source='public_msrp'")
        self.assertIn("недоступна", self.assert_unavailable())

    def test_invalid_signature_stops_before_core(self):
        self.assertIn("недоступна", self.assert_unavailable(secret="wrong-synthetic-secret-xxxxxxxx"))
        self.core.assert_not_called()
        self.live.assert_not_called()

    def test_missing_manufacturer_stops_before_core(self):
        self.assertIn("производителя", self.assert_unavailable(manufacturer=None))
        self.core.assert_not_called()
        self.live.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
