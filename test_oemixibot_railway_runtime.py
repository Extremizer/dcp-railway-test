from __future__ import annotations

import io
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.testclient import TestClient

from oemixibot_finance import FinanceEngine
from oemixibot_store import OemixiStore
from oemixibot_windows_preflight import inspect as inspect_windows_db
from oemixibot_railway_migration import audit_database, backup_database, backup_bundle
from oemixibot_poller_fence import (
    OWNER_RAILWAY,
    OWNER_WINDOWS,
    PollerFenceClient,
    build_fence_router,
    read_fence,
    transition_to_railway,
)
from oemixibot_railway_runtime import (
    DEALER_MAX_AGE_HOURS,
    MSRP_MAX_AGE_HOURS,
    RuntimeState,
    SingletonFileLock,
    build_railway_application,
    start_health_server,
    validate_runtime_config,
    verify_persistent_store,
    run,
)
from oemixi_secure_gateway_transport import (
    HTTPSPriceTransport,
    PATH,
    build_router,
)
from unified_price_contract_v1 import (
    STATUS_STALE,
    STATUS_VERIFIED,
    build_contract,
    client_price_inputs,
)


SECRET = "railway-runtime-isolated-secret-2026"


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


def config_env(tmp: Path, *, gateway="http://dcp-railway-test.railway.internal:8080"):
    return {
        "OEMIXIBOT_TOKEN": "123456:ISOLATED_TEST_TOKEN",
        "OEMIXIBOT_DB_PATH": str(tmp / "oemixibot.db"),
        "OEMIXIBOT_IDENTITY_DB": str(tmp / "oemixibot_identity.db"),
        "EXTREMIZER_OEM_REFERENCE_DB_FILE": str(tmp / "oem_reference.db"),
        "OEMIXIBOT_SHARED_IDENTITY_ONLY": "1",
        "OEMIXIBOT_GATEWAY_BASE_URL": gateway,
        "OEMIXIBOT_GATEWAY_SECRET": SECRET,
        "OEMIXIBOT_DEALER_MAX_AGE_HOURS": "24",
        "OEMIXIBOT_MSRP_MAX_AGE_HOURS": "720",
        "PORT": "8080",
    }


class RailwayRuntimeConfigTests(unittest.TestCase):
    def test_approved_runtime_config_accepts_private_railway_network(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = validate_runtime_config(config_env(Path(td)))
        self.assertEqual(cfg["dealer_max_age_hours"], 24.0)
        self.assertEqual(cfg["msrp_max_age_hours"], 720.0)
        self.assertTrue(str(cfg["gateway_url"]).startswith("http://"))
        self.assertEqual(cfg["db_path"].name, "oemixibot.db")
        self.assertEqual(cfg["identity_db"].name, "oemixibot_identity.db")
        self.assertEqual(cfg["reference_db"].name, "oem_reference.db")

    def test_public_plain_http_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            env = config_env(Path(td), gateway="http://example.com:8080")
            with self.assertRaisesRegex(ValueError, "railway_private_http"):
                validate_runtime_config(env)

    def test_unapproved_freshness_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            env = config_env(Path(td))
            env["OEMIXIBOT_DEALER_MAX_AGE_HOURS"] = "25"
            with self.assertRaisesRegex(ValueError, "approved_policy_24"):
                validate_runtime_config(env)

            env = config_env(Path(td))
            env["OEMIXIBOT_MSRP_MAX_AGE_HOURS"] = "719"
            with self.assertRaisesRegex(ValueError, "approved_policy_720"):
                validate_runtime_config(env)

    def test_shared_identity_only_is_mandatory(self):
        with tempfile.TemporaryDirectory() as td:
            env = config_env(Path(td))
            env["OEMIXIBOT_SHARED_IDENTITY_ONLY"] = "0"
            with self.assertRaisesRegex(ValueError, "SHARED_IDENTITY_ONLY"):
                validate_runtime_config(env)

    def test_incomplete_volume_is_rejected_before_polling(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            cfg = validate_runtime_config(config_env(root))
            # Only the main DB exists; identity/reference were not migrated.
            with sqlite3.connect(cfg["db_path"]) as conn:
                conn.execute("CREATE TABLE dealers(id TEXT PRIMARY KEY)")
                conn.commit()
            with self.assertRaisesRegex(RuntimeError, "required_persistent_db_missing"):
                verify_persistent_store(cfg)

    def test_runtime_entrypoint_has_no_local_browser_or_agent_dependency(self):
        source = Path("oemixibot_railway_runtime.py").read_text(encoding="utf-8")
        lowered = source.lower()
        self.assertNotIn("playwright", lowered)
        self.assertNotIn("dp_sync_agent", lowered)
        self.assertNotIn("oemixibot_dcp_price", lowered)
        self.assertNotIn("chromium", lowered)

    def test_explicit_application_composition_binds_before_polling(self):
        with tempfile.TemporaryDirectory() as td:
            cfg = validate_runtime_config(config_env(Path(td)))
        seen = {}

        class StubAppModule:
            @staticmethod
            def build_application(**kwargs):
                seen.update(kwargs)
                return SimpleNamespace(bot_data={}, running=False)

        def transport_factory(base_url, root_secret):
            seen["transport_base_url"] = base_url
            seen["transport_secret"] = root_secret
            return lambda manufacturer, oem, **kw: {"status": "NO_PRICE"}

        app = build_railway_application(
            cfg,
            transport_factory=transport_factory,
            app_module=StubAppModule,
        )
        self.assertFalse(app.running)
        self.assertEqual(seen["dealer_max_age_hours"], 24.0)
        self.assertEqual(
            seen["shared_price_adapter_cls"].__name__,
            "OEMixiCanonicalSharedPriceAdapter",
        )
        self.assertEqual(
            seen["transport_base_url"],
            "http://dcp-railway-test.railway.internal:8080",
        )


class RailwaySingletonHealthTests(unittest.TestCase):
    def test_singleton_prevents_second_polling_process(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "oemixibot.db.polling.lock"
            one = SingletonFileLock(path)
            two = SingletonFileLock(path)
            one.acquire()
            try:
                with self.assertRaisesRegex(RuntimeError, "singleton_already_running"):
                    two.acquire()
            finally:
                one.release()
            two.acquire()
            two.release()

    def test_ready_is_not_advertised_before_polling_start(self):
        import oemixibot_railway_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = config_env(root)
            for path in (
                root / "oemixibot.db",
                root / "oemixibot_identity.db",
                root / "oem_reference.db",
            ):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE seed(id INTEGER PRIMARY KEY)")
                    conn.commit()

            observed = {}

            class FakeApplication:
                running = False
                updater = SimpleNamespace(running=False)

                def run_polling(self, **kwargs):
                    observed["ready_at_polling_entry"] = observed["state"].ready
                    # Simulate a polling lifecycle only after the assertion point.
                    self.updater.running = True
                    self.running = True

            class FakeHealthServer:
                def shutdown(self):
                    pass
                def server_close(self):
                    pass

            def fake_health_server(state, port):
                observed["state"] = state
                return FakeHealthServer(), SimpleNamespace()

            with patch.object(
                runtime,
                "build_railway_application",
                return_value=FakeApplication(),
            ), patch.object(
                runtime,
                "start_health_server",
                side_effect=fake_health_server,
            ):
                runtime.run(env, cutover_fence_check=lambda config: {"active_owner": "RAILWAY", "generation": 2})

            self.assertFalse(
                observed["ready_at_polling_entry"],
                "BASE FAIL: /ready is advertised before Telegram polling starts",
            )

    def test_graceful_run_releases_singleton_and_allows_restart(self):
        import oemixibot_railway_runtime as runtime

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            env = config_env(root)
            for path in (
                root / "oemixibot.db",
                root / "oemixibot_identity.db",
                root / "oem_reference.db",
            ):
                with sqlite3.connect(path) as conn:
                    conn.execute("CREATE TABLE seed(id INTEGER PRIMARY KEY)")
                    conn.commit()

            polling_calls = []

            class FakeApplication:
                def run_polling(self, **kwargs):
                    polling_calls.append(kwargs)

            class FakeHealthServer:
                def __init__(self):
                    self.shutdown_called = False
                    self.close_called = False
                def shutdown(self):
                    self.shutdown_called = True
                def server_close(self):
                    self.close_called = True

            fake_server = FakeHealthServer()
            lock_path = Path(env["OEMIXIBOT_DB_PATH"] + ".polling.lock")

            with patch.object(
                runtime,
                "build_railway_application",
                return_value=FakeApplication(),
            ), patch.object(
                runtime,
                "start_health_server",
                return_value=(fake_server, SimpleNamespace()),
            ):
                run(env, cutover_fence_check=lambda config: {"active_owner": "RAILWAY", "generation": 2})

            self.assertEqual(len(polling_calls), 1)
            self.assertFalse(polling_calls[0]["drop_pending_updates"])
            self.assertTrue(fake_server.shutdown_called)
            self.assertTrue(fake_server.close_called)

            # Graceful exit released the same singleton lock, so a simulated
            # Railway restart can acquire it immediately.
            restart_lock = SingletonFileLock(lock_path)
            restart_lock.acquire()
            restart_lock.release()

    def test_readiness_monitor_waits_for_real_ptb_running_flags(self):
        import oemixibot_railway_runtime as runtime

        state = RuntimeState(started=True, ready=False)
        app = SimpleNamespace(
            running=False,
            updater=SimpleNamespace(running=False),
        )
        stop, thread = runtime.start_polling_readiness_monitor(
            state,
            app,
            interval_seconds=0.005,
        )
        try:
            self.assertFalse(state.ready)
            app.running = True
            self.assertFalse(state.ready)
            app.updater.running = True
            for _ in range(100):
                if state.ready:
                    break
                import time as _time
                _time.sleep(0.005)
            self.assertTrue(state.ready)
        finally:
            stop.set()
            thread.join(timeout=1)

    def test_ready_returns_false_again_when_polling_stops(self):
        import oemixibot_railway_runtime as runtime
        import time as _time

        state = RuntimeState(started=True, ready=False)
        app = SimpleNamespace(
            running=False,
            updater=SimpleNamespace(running=False),
        )
        stop, thread = runtime.start_polling_readiness_monitor(
            state,
            app,
            interval_seconds=0.005,
        )
        try:
            app.running = True
            app.updater.running = True
            for _ in range(100):
                if state.ready:
                    break
                _time.sleep(0.005)
            self.assertTrue(state.ready)

            # Telegram updater later stops while the worker process remains.
            app.updater.running = False
            for _ in range(100):
                if not state.ready:
                    break
                _time.sleep(0.005)

            self.assertFalse(
                state.ready,
                "BASE FAIL: /ready remains true after Telegram polling stops",
            )
        finally:
            stop.set()
            thread.join(timeout=1)

    def test_readiness_monitor_uses_bounded_wait_and_stays_continuous(self):
        import oemixibot_railway_runtime as runtime
        import time as _time

        class CountingStop:
            def __init__(self):
                self.calls = []
                self.done = False

            def is_set(self):
                return self.done

            def wait(self, timeout):
                self.calls.append(timeout)
                _time.sleep(0.001)
                if len(self.calls) >= 3:
                    self.done = True
                return self.done

            def set(self):
                self.done = True

        state = RuntimeState(started=True, ready=False)
        app = SimpleNamespace(
            running=True,
            updater=SimpleNamespace(running=True),
        )
        stop = CountingStop()
        returned_stop, thread = runtime.start_polling_readiness_monitor(
            state,
            app,
            stop_event=stop,
            interval_seconds=0.02,
        )
        thread.join(timeout=1)
        self.assertIs(returned_stop, stop)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(len(stop.calls), 3)
        self.assertTrue(all(value == 0.02 for value in stop.calls))
        self.assertFalse(state.ready)

    def test_health_and_readiness(self):
        state = RuntimeState(started=True, ready=False)
        server, thread = start_health_server(state, 0)
        self.addCleanup(server.server_close)
        port = server.server_address[1]
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r:
            self.assertEqual(r.status, 200)
            self.assertFalse(json.loads(r.read())["ready"])

        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=2)
        self.assertEqual(cm.exception.code, 503)

        state.ready = True
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/ready", timeout=2) as r:
            self.assertEqual(r.status, 200)
            self.assertTrue(json.loads(r.read())["ready"])
        server.shutdown()
        thread.join(timeout=2)


class ApprovedFreshnessBoundaryTests(unittest.TestCase):
    NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)

    def _contract(self, dealer_age, msrp_age):
        return build_contract(
            manufacturer="Polaris",
            requested_oem="2206328",
            matched_oem="2206328",
            item_category="parts",
            dealer_price_usd=184.07,
            dealer_price_source="owner_evidence:dealer",
            dealer_verified_at=(self.NOW - dealer_age).isoformat(),
            msrp_usd=239.99,
            msrp_source="owner_evidence:msrp",
            msrp_verified_at=(self.NOW - msrp_age).isoformat(),
            dealer_max_age_hours=DEALER_MAX_AGE_HOURS,
            msrp_max_age_hours=MSRP_MAX_AGE_HOURS,
            gateway_status="FOUND",
            expected_manufacturer="Polaris",
            expected_requested_oem="2206328",
            now=self.NOW,
        )

    def test_dealer_235959_valid_exact_24h_stale(self):
        valid = self._contract(
            timedelta(hours=23, minutes=59, seconds=59),
            timedelta(hours=1),
        )
        self.assertEqual(valid["dealer_price_status"], STATUS_VERIFIED)
        self.assertEqual(valid["dealer_price_usd"], 184.07)

        stale = self._contract(timedelta(hours=24), timedelta(hours=1))
        self.assertEqual(stale["dealer_price_status"], STATUS_STALE)
        self.assertIsNone(stale["dealer_price_usd"])
        self.assertIsNone(client_price_inputs(stale)["dealer_price_usd"])

    def test_msrp_7195959_valid_exact_720h_stale(self):
        valid = self._contract(
            timedelta(hours=1),
            timedelta(hours=719, minutes=59, seconds=59),
        )
        self.assertEqual(valid["msrp_status"], STATUS_VERIFIED)
        self.assertEqual(valid["msrp_usd"], 239.99)

        stale = self._contract(timedelta(hours=1), timedelta(hours=720))
        self.assertEqual(stale["msrp_status"], STATUS_STALE)
        self.assertIsNone(stale["msrp_usd"])
        self.assertIsNone(client_price_inputs(stale)["msrp_usd"])


class RailwayPrivateGatewayTests(unittest.TestCase):
    def test_private_http_hmac_gateway_is_allowed_and_server_authoritative(self):
        now = datetime.now(timezone.utc).isoformat()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        replay_db = Path(tmp.name) / "gateway.db"
        api = FastAPI()
        api.include_router(
            build_router(
                db_file=replay_db,
                root_secret=SECRET,
                shared_gateway=lambda manufacturer, oem, **kw: {
                    "status": "FOUND",
                    "manufacturer": manufacturer,
                    "query_oem": oem,
                    "current_oem": oem,
                    "dealer_price_usd": 85.51,
                    "source": "owner_evidence:cache",
                    "last_verified_at": now,
                    "currency": "USD",
                    "verified_cache_hit": True,
                    "server_cache_committed": False,
                },
                server_max_age_hours=24,
                msrp_max_age_hours=720,
                msrp_reader=lambda manufacturer, oem, **kw: {
                    "gateway_status": "FOUND",
                    "manufacturer": manufacturer,
                    "current_oem": oem,
                    "msrp_usd": 99.99,
                    "source": "owner_evidence:msrp",
                    "last_verified_at": now,
                },
            )
        )
        client = TestClient(api)
        self.addCleanup(client.close)
        transport = HTTPSPriceTransport(
            "http://dcp-railway-test.railway.internal:8080",
            SECRET,
            opener=local_opener(client),
            allow_railway_private_http=True,
        )
        result = transport("Arctic Cat", "0627-048", max_age_hours=999999)
        self.assertEqual(result["status"], "FOUND")
        self.assertEqual(result["dealer_price_usd"], 85.51)
        self.assertEqual(result["msrp_usd"], 99.99)
        self.assertIs(result["verified_cache_hit"], True)
        self.assertIs(result["server_cache_committed"], False)

    def test_public_http_transport_stays_forbidden(self):
        with self.assertRaises(ValueError):
            HTTPSPriceTransport(
                "http://public.example.com",
                SECRET,
                allow_railway_private_http=True,
            )


class PollerFenceTests(unittest.TestCase):
    def test_default_fence_blocks_railway_until_explicit_transition(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "coordination.db"
            initial = read_fence(db)
            self.assertEqual(initial["active_owner"], OWNER_WINDOWS)
            self.assertEqual(initial["generation"], 1)

            api = FastAPI()
            api.include_router(build_fence_router(db_file=db, root_secret=SECRET))
            client = TestClient(api)
            self.addCleanup(client.close)

            fence_client = PollerFenceClient(
                "http://dcp-railway-test.railway.internal:8080",
                SECRET,
                opener=local_opener(client),
                allow_railway_private_http=True,
            )
            with self.assertRaisesRegex(RuntimeError, "blocks_railway"):
                fence_client.require_railway_owner()

            transitioned = transition_to_railway(
                db,
                expected_generation=1,
                windows_stop_evidence="windows-poller-stopped-proof-20261015",
                actor="release-control",
            )
            self.assertEqual(transitioned["active_owner"], OWNER_RAILWAY)
            self.assertEqual(transitioned["generation"], 2)
            self.assertEqual(
                fence_client.require_railway_owner()["active_owner"],
                OWNER_RAILWAY,
            )

    def test_transition_requires_stop_evidence_and_compare_and_swap(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "coordination.db"
            initial = read_fence(db)
            with self.assertRaisesRegex(ValueError, "windows_stop_evidence_required"):
                transition_to_railway(
                    db,
                    expected_generation=initial["generation"],
                    windows_stop_evidence="too-short",
                    actor="release-control",
                )

            first = transition_to_railway(
                db,
                expected_generation=initial["generation"],
                windows_stop_evidence="verified-windows-stop-evidence",
                actor="release-control",
            )
            self.assertEqual(first["generation"], initial["generation"] + 1)

            with self.assertRaisesRegex(ValueError, "generation_mismatch|not_owned_by_windows"):
                transition_to_railway(
                    db,
                    expected_generation=initial["generation"],
                    windows_stop_evidence="second-stale-transition-proof",
                    actor="release-control",
                )


class MigrationAndRestartTests(unittest.TestCase):
    def _seed(self, db: Path):
        store = OemixiStore(str(db))
        store.init()
        store.ensure_cart_schema()
        store.add_dealer("D1", 700000001, "Railway Dealer", coefficient=1.20)

        finance = FinanceEngine(str(db))
        finance.payment(
            buyer_type="dealer",
            buyer_id="D1",
            currency="USD",
            amount=500,
            payment_id="PAY-SEED-1",
            actor="isolated-test",
            reason="migration seed",
            payment_method="cash",
            source_amount=500,
            source_currency="USD",
        )
        store.cart_add(
            "D1", "0627-048", 100.00,
            brand="Arctic Cat", name="Seed part", item_type="part", qty=2,
        )
        total = store.checkout_cart("D1", "E15J-0001")
        self.assertEqual(total, 240.00)
        store.cart_add(
            "D1", "2884109", 10.00,
            brand="Polaris", name="Future cart", item_type="accessory", qty=3,
        )
        tracking = store.create_tracking("TRACK-RAILWAY-1", "UPS")
        with store.db() as conn:
            item = conn.execute(
                "SELECT id FROM item_slices WHERE order_id='E15J-0001' ORDER BY id LIMIT 1"
            ).fetchone()[0]
        store.assign_tracking(item, tracking)
        store.set_status(item, "Едет на склад США", "migration rehearsal", "test")
        return store, finance

    def test_online_backup_preserves_wal_finance_orders_cart_and_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "source.db"
            dst = root / "railway-volume" / "oemixibot.db"
            store, finance = self._seed(src)
            self.assertEqual(finance.balance("D1"), 260.00)

            wal = sqlite3.connect(src)
            wal.execute("PRAGMA journal_mode=WAL")
            wal.execute("CREATE TABLE migration_wal_marker(id INTEGER PRIMARY KEY, note TEXT)")
            wal.execute("INSERT INTO migration_wal_marker VALUES(1,'committed')")
            wal.commit()
            self.assertTrue(Path(str(src) + "-wal").exists())
            wal.execute("BEGIN IMMEDIATE")
            wal.execute("INSERT INTO migration_wal_marker VALUES(2,'uncommitted')")

            audit = backup_database(src, dst)
            self.assertTrue(audit.source_wal_exists)
            self.assertEqual(audit.dealer_balances["D1"], "260.00")

            with sqlite3.connect(dst) as conn:
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM migration_wal_marker").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM dealer_orders").fetchone()[0],
                    1,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM dealer_cart_items").fetchone()[0],
                    1,
                )
                self.assertGreaterEqual(
                    conn.execute("SELECT COUNT(*) FROM item_status_history").fetchone()[0],
                    2,
                )
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM supplier_trackings").fetchone()[0],
                    1,
                )
            wal.rollback()
            wal.close()

            # Simulated restart: new Store/Finance objects open only the copied DB.
            restarted_store = OemixiStore(str(dst))
            restarted_finance = FinanceEngine(str(dst))
            self.assertEqual(restarted_finance.balance("D1"), 260.00)
            self.assertEqual(
                restarted_store.order_for_buyer("E15J-0001", "D1")["sell_total"],
                240.00,
            )
            cart = restarted_store.cart_view("D1")
            self.assertEqual(len(cart["items"]), 1)
            self.assertEqual(cart["items"][0]["oem"], "2884109")
            self.assertEqual(cart["items"][0]["qty"], 3)

            # Source remains intact and independently usable.
            self.assertEqual(FinanceEngine(str(src)).balance("D1"), 260.00)
            src_audit = audit_database(src)
            self.assertEqual(src_audit.finance_ledger_sha256, audit.finance_ledger_sha256)

    def test_three_database_bundle_rehearsal(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            main = root / "source-oemixibot.db"
            identity = root / "source-identity.db"
            reference = root / "source-reference.db"
            dest = root / "railway-volume"

            self._seed(main)
            with sqlite3.connect(identity) as conn:
                conn.execute(
                    "CREATE TABLE oem_identity(oem TEXT PRIMARY KEY, manufacturer TEXT)"
                )
                conn.execute("INSERT INTO oem_identity VALUES('2206328','Polaris')")
                conn.commit()
            with sqlite3.connect(reference) as conn:
                conn.execute(
                    "CREATE TABLE oem_reference(oem TEXT PRIMARY KEY, item_type TEXT)"
                )
                conn.execute("INSERT INTO oem_reference VALUES('2206328','part')")
                conn.commit()

            result = backup_bundle(
                oemixibot_db=main,
                identity_db=identity,
                reference_db=reference,
                destination_dir=dest,
            )
            self.assertEqual(
                set(result),
                {"oemixibot.db", "oemixibot_identity.db", "oem_reference.db"},
            )
            for name in result:
                self.assertTrue((dest / name).exists())
            self.assertEqual(
                result["oemixibot_identity.db"].row_counts["oem_identity"],
                1,
            )
            self.assertEqual(
                result["oem_reference.db"].row_counts["oem_reference"],
                1,
            )

            cfg = validate_runtime_config(
                {
                    **config_env(dest),
                    "OEMIXIBOT_DB_PATH": str(dest / "oemixibot.db"),
                    "OEMIXIBOT_IDENTITY_DB": str(dest / "oemixibot_identity.db"),
                    "EXTREMIZER_OEM_REFERENCE_DB_FILE": str(dest / "oem_reference.db"),
                }
            )
            verify_persistent_store(cfg)

    def test_identity_db_can_be_migrated_with_same_verified_copy_mechanism(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            src = root / "oemixibot_identity.db"
            dst = root / "volume" / "oemixibot_identity.db"
            with sqlite3.connect(src) as conn:
                conn.execute(
                    """CREATE TABLE oem_identity(
                           oem TEXT PRIMARY KEY,
                           manufacturer TEXT NOT NULL,
                           item_type TEXT,
                           source TEXT NOT NULL,
                           confirmed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                       )"""
                )
                conn.execute(
                    "INSERT INTO oem_identity(oem,manufacturer,item_type,source) VALUES(?,?,?,?)",
                    ("0627-048", "Arctic Cat", "part", "dcp_dl_confirmed"),
                )
                conn.commit()
            audit = backup_database(src, dst)
            self.assertEqual(audit.row_counts["oem_identity"], 1)
            with sqlite3.connect(dst) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT manufacturer FROM oem_identity WHERE oem='0627-048'"
                    ).fetchone()[0],
                    "Arctic Cat",
                )


class WindowsPreflightSafetyTests(unittest.TestCase):
    def test_read_only_preflight_does_not_change_source_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "live.db"
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "CREATE TABLE dealers(id TEXT PRIMARY KEY, name TEXT NOT NULL)"
                )
                conn.execute(
                    """CREATE TABLE finance_ledger(
                           id INTEGER PRIMARY KEY,
                           buyer_type TEXT,
                           buyer_id TEXT,
                           wallet_currency TEXT,
                           amount TEXT
                       )"""
                )
                conn.execute("INSERT INTO dealers VALUES('D1','Dealer')")
                conn.execute(
                    "INSERT INTO finance_ledger VALUES(1,'dealer','D1','USD','125.50')"
                )
                conn.commit()

            before = db.read_bytes()
            before_hash = hashlib.sha256(before).hexdigest()
            before_stat = db.stat()

            result = inspect_windows_db(db)

            after = db.read_bytes()
            after_hash = hashlib.sha256(after).hexdigest()
            after_stat = db.stat()

            self.assertEqual(result["preflight_status"], "READ_ONLY_OK")
            self.assertEqual(result["dealer_balances_usd"], {"D1": "125.50"})
            self.assertEqual(before_hash, after_hash)
            self.assertEqual(before, after)
            self.assertEqual(before_stat.st_size, after_stat.st_size)


class SharedIdentityIsolationTests(unittest.TestCase):
    def test_railway_mode_does_not_read_local_orders_identity(self):
        import oemixibot_catalog

        with tempfile.TemporaryDirectory() as td:
            fake_orders = Path(td) / "orders.db"
            with sqlite3.connect(fake_orders) as conn:
                conn.execute(
                    "CREATE TABLE dealer_price_cache(oem TEXT, manufacturer TEXT)"
                )
                conn.execute(
                    "INSERT INTO dealer_price_cache VALUES('2206328','Polaris')"
                )
                conn.commit()
            with patch.object(oemixibot_catalog, "ORDERS_DB", fake_orders), patch.dict(
                os.environ,
                {"OEMIXIBOT_SHARED_IDENTITY_ONLY": "1"},
                clear=False,
            ):
                self.assertEqual(
                    oemixibot_catalog._production_candidates("2206328"),
                    [],
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
