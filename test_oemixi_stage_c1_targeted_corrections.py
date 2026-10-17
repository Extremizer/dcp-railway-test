from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import io
import sqlite3
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
import urllib.error
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

import oemixibot_app
import oemixibot_catalog
from oemixi_secure_gateway_transport import (
    HTTPSPriceTransport,
    OEMixiCanonicalSharedPriceAdapter,
    _sanitize_gateway_result,
    build_router,
)


ROOT_SECRET = "isolated-stage-c1-corrections-secret"


class _LocalResponse:
    def __init__(self, data: bytes):
        self._stream = io.BytesIO(data)
    def read(self, size=-1):
        return self._stream.read(size)
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


def local_opener(client: TestClient):
    def open_request(req, timeout):
        parsed = urlparse(req.full_url)
        response = client.request(
            req.get_method(),
            parsed.path,
            content=req.data,
            headers=dict(req.header_items()),
        )
        if response.status_code >= 400:
            raise urllib.error.HTTPError(
                req.full_url,
                response.status_code,
                response.text,
                hdrs=None,
                fp=io.BytesIO(response.content),
            )
        return _LocalResponse(response.content)
    return open_request


class StageC1TargetedCorrectionBaseFailTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "isolated.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE marker(id INTEGER PRIMARY KEY, value TEXT)")
            conn.execute("INSERT INTO marker VALUES(1,'unchanged')")

    def _transport(self, dealer_result, msrp_result):
        api = FastAPI()
        api.include_router(
            build_router(
                db_file=self.db,
                root_secret=ROOT_SECRET,
                shared_gateway=lambda m, o, **kw: dict(dealer_result),
                server_max_age_hours=24,
                msrp_max_age_hours=48,
                wait_seconds=1,
                msrp_reader=lambda m, o, **kw: dict(msrp_result),
            )
        )
        client = TestClient(api)
        self.addCleanup(client.close)
        return HTTPSPriceTransport(
            "https://gateway.test",
            ROOT_SECRET,
            opener=local_opener(client),
        )

    def test_base_fail_msrp_only_survives_full_secure_transport_to_admin_card(self):
        """BASE FAIL on PR #41: MSRP is lost before the admin information card."""
        transport = self._transport(
            {
                "status": "NO_PRICE",
                "manufacturer": "Polaris",
                "query_oem": "2884109",
                "current_oem": "2884109",
                "error_code": "dealer_price_missing",
            },
            {
                "gateway_status": "FOUND",
                "manufacturer": "Polaris",
                "current_oem": "2884109",
                "item_category": "accessories",
                "msrp_usd": 10.99,
                "source": "verified_public_msrp:isolated_fixture",
                "last_verified_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        with patch.object(
            oemixibot_catalog,
            "resolve_identity",
            return_value={
                "oem": "2884109",
                "manufacturer": "Polaris",
                "item_type": "accessories",
                "source": "isolated",
            },
        ), patch.object(
            oemixibot_catalog.oem_reference_service,
            "lookup_oem",
            return_value={},
        ), patch.object(
            oemixibot_app.STORE,
            "init",
        ), patch.object(
            oemixibot_app.STORE,
            "ensure_cart_schema",
        ), patch.object(
            oemixibot_app,
            "is_telegram_admin",
            return_value=True,
        ):
            application = oemixibot_app.build_application(
                token="123456:TEST_TOKEN_FOR_ISOLATED_RUNTIME",
                shared_price_gateway=transport,
                dealer_max_age_hours=24,
                shared_price_adapter_cls=OEMixiCanonicalSharedPriceAdapter,
            )
            reply = AsyncMock()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=1),
                effective_chat=SimpleNamespace(id=1001),
                effective_message=SimpleNamespace(
                    text="2884109",
                    reply_text=reply,
                ),
            )
            sent = AsyncMock()

            async def run_admin_probe():
                tasks = []
                def schedule(coro, *, name=None):
                    task = asyncio.create_task(coro, name=name)
                    tasks.append(task)
                    return task
                context = SimpleNamespace(
                    application=application,
                    user_data={"admin_oem_search": True},
                    bot=SimpleNamespace(send_message=sent),
                )
                with patch.object(application, "create_task", side_effect=schedule):
                    await oemixibot_app.oem_message(update, context)
                    if tasks:
                        await asyncio.gather(*tasks)

            asyncio.run(run_admin_probe())

        text = sent.await_args.kwargs["text"]
        self.assertIn("Дилерская цена недоступна", text)
        self.assertIn("MSRP: $10.99", text)
        self.assertNotIn("Дилерская цена: $10.99", text)

    def test_gateway_error_keeps_independent_msrp_without_masking_error(self):
        verified_at = datetime.now(timezone.utc).isoformat()
        transport = self._transport(
            {
                "status": "TECHNICAL_ERROR",
                "manufacturer": "Polaris",
                "query_oem": "2884109",
                "current_oem": "2884109",
                "error_code": "shared_gateway_unavailable",
            },
            {
                "gateway_status": "FOUND",
                "manufacturer": "Polaris",
                "current_oem": "2884109",
                "item_category": "accessories",
                "msrp_usd": 10.99,
                "source": "verified_public_msrp:isolated_fixture",
                "last_verified_at": verified_at,
            },
        )
        result = transport("Polaris", "2884109")
        self.assertEqual(result["status"], "TECHNICAL_ERROR")
        self.assertEqual(result["msrp_usd"], 10.99)
        self.assertEqual(
            result["msrp_source"],
            "verified_public_msrp:isolated_fixture",
        )
        self.assertEqual(result["msrp_verified_at"], verified_at)
        self.assertNotIn("dealer_price_usd", result)

    def test_found_without_atomic_completion_proof_stays_unproven(self):
        result = _sanitize_gateway_result(
            {
                "status": "FOUND",
                "manufacturer": "Arctic Cat",
                "query_oem": "2670-265",
                "current_oem": "2670-265",
                "dealer_price_usd": 31.25,
                "source": "verified_local_dcp:live",
                "last_verified_at": datetime.now(timezone.utc).isoformat(),
                "currency": "USD",
            }
        )
        self.assertEqual(result["status"], "FOUND")
        self.assertNotIn("server_cache_committed", result)
        self.assertNotIn("verified_cache_hit", result)

    def test_base_fail_cache_hit_is_not_atomic_live_commit(self):
        """BASE FAIL on PR #41: verified_cache_hit is promoted to commit=True."""
        cache = _sanitize_gateway_result(
            {
                "status": "FOUND",
                "manufacturer": "Arctic Cat",
                "query_oem": "2670-265",
                "current_oem": "2670-265",
                "dealer_price_usd": 31.25,
                "source": "verified_local_dcp:cache",
                "last_verified_at": datetime.now(timezone.utc).isoformat(),
                "currency": "USD",
                "verified_cache_hit": True,
                "server_cache_committed": False,
            }
        )
        self.assertIs(cache.get("verified_cache_hit"), True)
        self.assertIs(cache.get("server_cache_committed"), False)

        live = _sanitize_gateway_result(
            {
                "status": "FOUND",
                "manufacturer": "Arctic Cat",
                "query_oem": "2670-265",
                "current_oem": "2670-265",
                "dealer_price_usd": 31.25,
                "source": "verified_local_dcp:live",
                "last_verified_at": datetime.now(timezone.utc).isoformat(),
                "currency": "USD",
                "verified_cache_hit": False,
                "server_cache_committed": True,
            }
        )
        self.assertIs(live.get("verified_cache_hit"), False)
        self.assertIs(live.get("server_cache_committed"), True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
