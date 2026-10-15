from __future__ import annotations

import asyncio
from contextlib import ExitStack
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
import urllib.error
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

import dcp_price_gateway as gateway
import dp_live_bridge as bridge
import dp_live_health as health
import oemixibot_app
import oemixibot_catalog
from oemixi_secure_gateway_transport import (
    PATH,
    HTTPSPriceTransport,
    build_router,
    canonical_body,
    sign_request,
)
from test_dp_result_completion import (
    CompletionFixture,
    headers as producer_headers,
)


ROOT_SECRET = "isolated-oemixi-gateway-secret-2026"


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


class SecureGatewayFixture(CompletionFixture):
    def setUp(self):
        super().setUp()
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM dealer_price_cache")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS oem_catalog_cache (
                       manufacturer TEXT NOT NULL,
                       current_oem TEXT NOT NULL,
                       name TEXT,
                       catalog TEXT,
                       msrp_usd REAL,
                       msrp_verified INTEGER NOT NULL DEFAULT 0,
                       previous_oems_json TEXT NOT NULL DEFAULT '[]',
                       source_kind TEXT,
                       source_ref TEXT,
                       first_seen_at TEXT NOT NULL,
                       last_verified_at TEXT NOT NULL,
                       PRIMARY KEY(manufacturer,current_oem)
                   )"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS oem_catalog_aliases (
                       manufacturer TEXT NOT NULL,
                       alias_oem TEXT NOT NULL,
                       current_oem TEXT NOT NULL,
                       alias_kind TEXT NOT NULL,
                       PRIMARY KEY(manufacturer,alias_oem)
                   )"""
            )
            now = health._now()
            conn.execute(
                """INSERT OR REPLACE INTO oem_catalog_cache(
                       manufacturer,current_oem,name,catalog,msrp_usd,msrp_verified,
                       source_kind,source_ref,first_seen_at,last_verified_at
                   ) VALUES(?,?,?,?,?,1,?,?,?,?)""",
                (
                    "Arctic Cat",
                    "2670-265",
                    "Mock canonical item",
                    "Parts",
                    99.99,
                    "validated_public_msrp",
                    "isolated-fixture",
                    now,
                    now,
                ),
            )

        def shared_gateway(manufacturer, oem, *, max_age_hours, wait_seconds=5):
            return gateway.request_price(
                self.db,
                manufacturer,
                oem,
                max_age_hours=max_age_hours,
                wait_seconds=wait_seconds,
            )

        api = FastAPI()
        api.include_router(
            build_router(
                db_file=self.db,
                root_secret=ROOT_SECRET,
                shared_gateway=shared_gateway,
                server_max_age_hours=24,
                msrp_max_age_hours=48,
                wait_seconds=4,
            )
        )
        self.secure_client = TestClient(api)
        self.addCleanup(self.secure_client.close)
        self.transport = HTTPSPriceTransport(
            "https://gateway.test",
            ROOT_SECRET,
            timeout_seconds=5,
            opener=local_opener(self.secure_client),
        )

    def producer_once(self, *, status="FOUND", dealer_price=31.25):
        errors = []

        def work():
            try:
                deadline = time.monotonic() + 3
                claim = None
                while claim is None and time.monotonic() < deadline:
                    r = self.client.get(
                        "/internal/dp-sync/next",
                        headers=producer_headers("GET", "/internal/dp-sync/next"),
                    )
                    if r.status_code != 200:
                        raise AssertionError(r.text)
                    claim = r.json()["request"]
                    if claim is None:
                        time.sleep(0.01)
                if claim is None:
                    raise AssertionError("secure request did not enqueue one claim")
                payload = self.payload(
                    claim,
                    status=status,
                    dealer_price_usd=(dealer_price if status == "FOUND" else None),
                )
                r = self.post(payload)
                if r.status_code != 200:
                    raise AssertionError(r.text)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=work)
        thread.start()
        return thread, errors


class SecureTransportE2ETests(SecureGatewayFixture):
    def test_windows_like_transport_to_real_oem_message_one_claim(self):
        thread, errors = self.producer_once()

        with ExitStack() as stack:
            stack.enter_context(patch.object(oemixibot_app.STORE, "init"))
            stack.enter_context(patch.object(oemixibot_app.STORE, "ensure_cart_schema"))
            stack.enter_context(
                patch.object(
                    oemixibot_app.STORE,
                    "cart_view",
                    return_value={"items": [], "total_usd": 0},
                )
            )
            stack.enter_context(
                patch.object(
                    oemixibot_app,
                    "_dealer",
                    return_value={"id": "isolated-dealer", "price_coefficient": 1.0},
                )
            )
            stack.enter_context(
                patch.object(
                    oemixibot_catalog,
                    "resolve_identity",
                    return_value={
                        "oem": "2670-265",
                        "manufacturer": "Arctic Cat",
                        "item_type": "parts",
                        "source": "isolated",
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    oemixibot_catalog.oem_reference_service,
                    "lookup_oem",
                    return_value={},
                )
            )
            stack.enter_context(patch.object(oemixibot_catalog, "confirm_identity"))
            direct = stack.enter_context(
                patch.object(
                    oemixibot_catalog.oemixibot_dcp_price,
                    "get_dealer_price_dict",
                    side_effect=AssertionError("direct browser fallback forbidden"),
                )
            )

            application = oemixibot_app.build_application(
                token="123456:TEST_TOKEN_FOR_ISOLATED_RUNTIME",
                shared_price_gateway=self.transport,
                dealer_max_age_hours=24,
            )
            reply = AsyncMock()
            update = SimpleNamespace(
                effective_user=None,
                effective_message=SimpleNamespace(
                    text="2670-265",
                    reply_text=reply,
                ),
            )
            context = SimpleNamespace(application=application, user_data={})
            asyncio.run(oemixibot_app.oem_message(update, context))

        thread.join(timeout=6)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        direct.assert_not_called()

        offer = context.user_data["offer:2670-265"]
        self.assertEqual(offer["dl_usd"], 31.25)
        self.assertEqual(offer["msrp_usd"], 99.99)
        self.assertEqual(offer["price_contract_version"], "canonical-price-v1")
        self.assertIn("$31.25", reply.await_args.args[0])

        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM dp_live_requests").fetchone()[0],
                1,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT dealer_price_usd FROM dealer_price_cache "
                    "WHERE manufacturer='Arctic Cat' AND oem='2670-265'"
                ).fetchone()[0],
                31.25,
            )

    def test_verified_cache_hit_uses_no_new_live_request(self):
        with sqlite3.connect(self.db) as conn:
            from dp_result_completion import _write_cache
            _write_cache(
                conn,
                "Arctic Cat",
                "2670-265",
                31.25,
                "verified_local_dcp:isolated_cache",
                health._now(),
            )
        before = self.snapshot()
        result = self.transport("Arctic Cat", "2670-265", max_age_hours=999999)
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["dealer_price_usd"], 31.25)
        self.assertEqual(result["msrp_usd"], 99.99)
        after = self.snapshot()
        self.assertEqual(
            len(after["dp_live_requests"]),
            len(before["dp_live_requests"]),
        )

    def test_admin_search_works_over_same_transport_without_dealer_or_finance(self):
        with sqlite3.connect(self.db) as conn:
            from dp_result_completion import _write_cache
            _write_cache(
                conn,
                "Arctic Cat",
                "2670-265",
                31.25,
                "verified_local_dcp:isolated_cache",
                health._now(),
            )

        with ExitStack() as stack:
            stack.enter_context(
                patch.object(
                    oemixibot_catalog,
                    "resolve_identity",
                    return_value={
                        "oem": "2670-265",
                        "manufacturer": "Arctic Cat",
                        "item_type": "parts",
                        "source": "isolated",
                    },
                )
            )
            stack.enter_context(
                patch.object(
                    oemixibot_catalog.oem_reference_service,
                    "lookup_oem",
                    return_value={},
                )
            )
            stack.enter_context(patch.object(oemixibot_catalog, "confirm_identity"))
            stack.enter_context(patch.object(oemixibot_app.STORE, "init"))
            stack.enter_context(patch.object(oemixibot_app.STORE, "ensure_cart_schema"))
            cart_add = stack.enter_context(
                patch.object(
                    oemixibot_app.STORE,
                    "cart_add",
                    side_effect=AssertionError("admin cart mutation forbidden"),
                )
            )
            checkout = stack.enter_context(
                patch.object(
                    oemixibot_app.STORE,
                    "checkout_cart",
                    side_effect=AssertionError("admin checkout forbidden"),
                )
            )
            finance = stack.enter_context(
                patch.object(
                    oemixibot_app.FINANCE,
                    "payment",
                    side_effect=AssertionError("admin finance mutation forbidden"),
                )
            )
            stack.enter_context(patch.object(oemixibot_app, "is_telegram_admin", return_value=True))

            application = oemixibot_app.build_application(
                token="123456:TEST_TOKEN_FOR_ISOLATED_RUNTIME",
                shared_price_gateway=self.transport,
                dealer_max_age_hours=24,
            )
            reply = AsyncMock()
            update = SimpleNamespace(
                effective_user=SimpleNamespace(id=1),
                effective_message=SimpleNamespace(
                    text="2670-265",
                    reply_text=reply,
                ),
            )
            context = SimpleNamespace(
                application=application,
                user_data={"admin_oem_search": True},
            )
            asyncio.run(oemixibot_app.oem_message(update, context))

        text = reply.await_args.args[0]
        self.assertIn("Дилерская цена: $31.25", text)
        self.assertIn("MSRP: $99.99", text)
        cart_add.assert_not_called()
        checkout.assert_not_called()
        finance.assert_not_called()


class SecureTransportSecurityTests(SecureGatewayFixture):
    def _signed_headers(self, *, secret=ROOT_SECRET, nonce="nonce_1234567890123456"):
        body = canonical_body("Arctic Cat", "2670-265")
        ts = str(int(time.time()))
        return body, {
            "Content-Type": "application/json",
            "X-OEMixi-Gateway-TS": ts,
            "X-OEMixi-Gateway-Nonce": nonce,
            "X-OEMixi-Gateway-Sig": sign_request(
                secret,
                method="POST",
                path=PATH,
                timestamp=ts,
                nonce=nonce,
                body=body,
            ),
        }

    def test_auth_failure_is_fail_closed_and_enqueues_nothing(self):
        bad = HTTPSPriceTransport(
            "https://gateway.test",
            "wrong-isolated-secret-xxxxxxxxxxxx",
            opener=local_opener(self.secure_client),
        )
        result = bad("Arctic Cat", "2670-265", max_age_hours=1)
        self.assertEqual(result["status"], "TECHNICAL_ERROR")
        self.assertEqual(result["error_code"], "gateway_auth_failed")
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM dp_live_requests").fetchone()[0],
                0,
            )

    def test_signed_request_is_single_use_replay_rejected(self):
        body, headers = self._signed_headers()
        # Avoid waiting for a producer; admission closed yields an immediate
        # server result after auth/nonce validation.
        from dcp_admission_test_support import approve_mode
        approve_mode(self.db, "CLOSED")
        first = self.secure_client.post(PATH, content=body, headers=headers)
        self.assertEqual(first.status_code, 200, first.text)
        second = self.secure_client.post(PATH, content=body, headers=headers)
        self.assertEqual(second.status_code, 409, second.text)
        self.assertEqual(second.json()["detail"]["code"], "replay_rejected")

    def test_payload_cannot_supply_price_freshness_or_producer_controls(self):
        body, headers = self._signed_headers(nonce="nonce_abcdefghijklmnop")
        payload = json.loads(body)
        payload.update(
            dealer_price_usd=1,
            max_age_hours=999999,
            producer_id="attacker",
            claim_token="attacker",
        )
        raw = json.dumps(payload).encode()
        # Signature for the canonical two-field body cannot authorize a mutated body.
        response = self.secure_client.post(PATH, content=raw, headers=headers)
        self.assertIn(response.status_code, (401, 422))
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM dp_live_requests").fetchone()[0],
                0,
            )

    def test_transport_timeout_429_5xx_invalid_json_fail_closed(self):
        cases = [
            (
                lambda *a, **k: (_ for _ in ()).throw(TimeoutError("timeout")),
                "TECHNICAL_ERROR",
                "gateway_transport_error",
            ),
            (
                lambda req, timeout: (_ for _ in ()).throw(
                    urllib.error.HTTPError(req.full_url, 429, "rate", None, None)
                ),
                "RATE_LIMITED",
                "gateway_rate_limited",
            ),
            (
                lambda req, timeout: (_ for _ in ()).throw(
                    urllib.error.HTTPError(req.full_url, 503, "down", None, None)
                ),
                "TECHNICAL_ERROR",
                "gateway_5xx",
            ),
            (
                lambda *a, **k: _LocalResponse(b"not-json"),
                "TECHNICAL_ERROR",
                "invalid_gateway_json",
            ),
        ]
        for opener, status, code in cases:
            with self.subTest(code=code):
                transport = HTTPSPriceTransport(
                    "https://gateway.test",
                    ROOT_SECRET,
                    opener=opener,
                )
                out = transport("Arctic Cat", "2670-265")
                self.assertEqual(out["status"], status)
                self.assertEqual(out["error_code"], code)

    def test_https_required_outside_explicit_test_mode(self):
        with self.assertRaisesRegex(ValueError, "https_required"):
            HTTPSPriceTransport(
                "http://gateway.example",
                ROOT_SECRET,
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
