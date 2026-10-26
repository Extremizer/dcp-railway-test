"""M3 PHONE-FIRST staging dry-run integration tests.

No live DCP, no Telegram network, no Railway deployment, no inbound VPS/CDP.
"""
from __future__ import annotations

import hashlib
import hmac
import gc
import json
import os
import tempfile
import time
import urllib.parse
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from dp_cloud_producer_client import CloudV2Producer
import dcp_phone_staging_app as staging


BOT_TOKEN = "test:fake"
OWNER_ID = 12345
DP_TOKEN = "x" * 40


def signed_data(*, token: str = BOT_TOKEN, user_id: int = OWNER_ID, auth_date: int | None = None) -> str:
    if auth_date is None:
        auth_date = int(time.time())
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": user_id}, separators=(",", ":")),
    }
    check = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": sig})


class PhoneM3DryRunTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "DCP_PHONE_STAGING_MOCK_ONLY": "1",
            "DCP_PHONE_STAGING_DRY_RUN": "1",
            "DCP_RECOVERY_TELEGRAM_BOT_TOKEN": BOT_TOKEN,
            "DCP_RECOVERY_OWNER_ID": str(OWNER_ID),
            "DCP_RECOVERY_PUBLIC_URL": "https://example.com",
            "DCP_STAGING_TEST_NOTICE_ID": "",
            "DCP_CLOUD_STATE_DIR": self.tmp.name,
            "DP_SYNC_TOKEN": DP_TOKEN,
            "EXTREMIZER_DP_CLOUD_OWNERSHIP_ENABLED": "1",
        }, clear=True)
        self.env.start()
        self.old = {
            "grants": staging.app.state.grants,
            "browser": staging.app.state.browser,
            "coordinator": staging.app.state.coordinator,
            "dry_run_db": staging.app.state.dry_run_db,
            "dry_run_key": staging.app.state.dry_run_key,
        }
        staging.app.state.grants = None
        staging.app.state.browser = None
        staging.app.state.coordinator = None
        staging.app.state.dry_run_db = None
        staging.app.state.dry_run_key = None
        with patch("threading.Thread"):
            staging.configure()
        self.client = TestClient(staging.app)

    def tearDown(self):
        self.client.close()
        for key, value in self.old.items():
            setattr(staging.app.state, key, value)
        self.env.stop()
        gc.collect()
        self.tmp.cleanup()

    def bearer(self) -> str:
        now = int(time.time())
        ticket = staging.app.state.grants.invite()
        response = self.client.post(
            "/recovery/exchange",
            json={"ticket": ticket, "init_data": signed_data(auth_date=now)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        replay = self.client.post(
            "/recovery/exchange",
            json={"ticket": ticket, "init_data": signed_data(auth_date=1001)},
        )
        self.assertEqual(replay.status_code, 401)
        return response.json()["bearer"]

    def producer(self) -> CloudV2Producer:
        def send(method, path, body, headers):
            response = self.client.request(method, path, content=body, headers=headers)
            if response.status_code >= 400:
                raise AssertionError(f"{method} {path} failed {response.status_code}: {response.text}")
            return response.json()
        return CloudV2Producer("cloud-vps-dryrun-0001", staging.signing_key(DP_TOKEN), send)

    def test_health_stays_mock_only_and_unauthorized_is_401(self):
        health = self.client.get("/health").json()
        self.assertEqual(health["mode"], "MOCK_ONLY")
        self.assertFalse(health["dcp_enabled"])
        self.assertFalse(health["admission"])
        self.assertTrue(health["dry_run_enabled"])
        for method, path, body in (
            ("POST", "/recovery/dry-run", {"manufacturer": "SAFE-MFR", "oem": "SAFE-OEM-000"}),
            ("GET", "/recovery/dry-run/dryrun-safe-mfr-safe-oem-000", None),
        ):
            response = self.client.request(method, path, json=body)
            self.assertEqual(response.status_code, 401)

    def test_ui_to_signed_producer_worker_to_ui_dry_run(self):
        bearer = self.bearer()
        queued = self.client.post(
            "/recovery/dry-run",
            headers={"Authorization": "Bearer " + bearer},
            json={"manufacturer": "SAFE-MFR", "oem": "SAFE-OEM-000"},
        )
        self.assertEqual(queued.status_code, 200, queued.text)
        request_id = queued.json()["request_id"]

        producer = self.producer()
        acquired = producer.acquire()
        self.assertEqual(acquired["status"], "ACQUIRED")
        claim = producer.next()
        self.assertEqual(claim["request_id"], request_id)
        self.assertEqual(claim["manufacturer"], "SAFE-MFR")
        self.assertEqual(claim["oem"], "SAFE-OEM-000")
        ack = producer.complete(claim, {
            "status": "DRY_RUN_OK",
            "manufacturer": "SAFE-MFR",
            "oem": "SAFE-OEM-000",
        })
        self.assertEqual(ack["status"], "DRY_RUN_OK")
        self.assertFalse(ack["server_cache_committed"])
        self.assertEqual(producer.release()["status"], "RELEASED")

        result = self.client.get(
            "/recovery/dry-run/" + request_id,
            headers={"Authorization": "Bearer " + bearer},
        )
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["status"], "DRY_RUN_OK")
        self.assertEqual(payload["result"]["source"], "vps-dry-run-no-dcp")
        self.assertFalse(payload["result"]["dcp_enabled"])
        self.assertFalse(payload["result"]["admission"])

    def test_signed_queue_safe_without_telegram_and_duplicate_guard(self):
        producer = self.producer()
        path = "/internal/dp-cloud/dry-run/queue-safe"
        payload = {"manufacturer": "SAFE-MFR", "oem": "SAFE-OEM-000"}
        denied = self.client.post(path, json=payload)
        self.assertEqual(denied.status_code, 401)
        first = producer.request("POST", path, payload)
        self.assertEqual(first["status"], "QUEUED")
        self.assertTrue(first["created"])
        second = producer.request("POST", path, payload)
        self.assertEqual(second["status"], "PENDING")
        self.assertFalse(second["created"])
        acquired = producer.acquire()
        self.assertEqual(acquired["status"], "ACQUIRED")
        claim = producer.next()
        self.assertEqual(claim["manufacturer"], "SAFE-MFR")
        self.assertEqual(claim["oem"], "SAFE-OEM-000")
        ack = producer.complete(claim, {
            "status": "DRY_RUN_OK", "manufacturer": "SAFE-MFR", "oem": "SAFE-OEM-000",
        })
        self.assertFalse(ack["server_cache_committed"])
        self.assertEqual(producer.release()["status"], "RELEASED")
        after = producer.request("POST", path, payload)
        self.assertEqual(after["status"], "FINISHED")
        self.assertFalse(after["created"])

    def test_safe_fixture_and_owner_fences_fail_closed(self):
        bearer = self.bearer()
        bad = self.client.post(
            "/recovery/dry-run",
            headers={"Authorization": "Bearer " + bearer},
            json={"manufacturer": "Ski-Doo", "oem": "417300574"},
        )
        self.assertEqual(bad.status_code, 400)
        producer = self.producer()
        with self.assertRaises(Exception):
            producer.next()


if __name__ == "__main__":
    unittest.main()
