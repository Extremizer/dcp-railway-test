import os
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from dp_cloud_producer_client import CloudV2Producer
import dcp_phone_dryrun_worker as worker
import dcp_phone_staging_app as staging


BOT_TOKEN = "123456:AA" + "b" * 35
OWNER_ID = 12345
DP_TOKEN = "stage-worker-token-" + "x" * 48
WRONG_TOKEN = "stage-worker-token-" + "y" * 48


def init_data(owner_id=OWNER_ID):
    import hashlib
    import hmac
    import time
    import urllib.parse

    fields = {
        "auth_date": str(int(time.time())),
        "query_id": "worker-test-query",
        "user": '{"id":%d,"first_name":"Owner"}' % owner_id,
    }
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    sig = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urllib.parse.urlencode({**fields, "hash": sig})


class PhoneDryRunWorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
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
        self.tmp.cleanup()

    def bearer(self):
        ticket = staging.app.state.grants.invite()
        response = self.client.post("/recovery/exchange", json={
            "ticket": ticket,
            "init_data": init_data(),
        })
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["bearer"]

    def send_for(self, token):
        key = worker.signing_key(token)

        def send(method, path, body, headers):
            response = self.client.request(method, path, content=body, headers=headers)
            if response.status_code >= 400:
                raise worker.WorkerBlocked(f"http_{response.status_code}")
            return response.json()

        return key, send

    def producer(self, token=DP_TOKEN):
        key, send = self.send_for(token)
        return CloudV2Producer(worker.DEFAULT_PRODUCER_ID, key, send)

    def enqueue_safe(self):
        response = self.client.post(
            "/recovery/dry-run",
            headers={"Authorization": "Bearer " + self.bearer()},
            json={"manufacturer": worker.SAFE_MANUFACTURER, "oem": worker.SAFE_OEM},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["request_id"]

    def test_worker_completes_safe_fixture_with_hmac_and_ownership(self):
        request_id = self.enqueue_safe()
        outcome = worker.run_once(self.producer())
        self.assertEqual(outcome["status"], worker.SAFE_STATUS)
        self.assertEqual(outcome["request_id"], request_id)
        result = self.client.get(
            "/recovery/dry-run/" + request_id,
            headers={"Authorization": "Bearer " + self.bearer()},
        )
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["result"]["dcp_enabled"], False)
        self.assertEqual(result.json()["result"]["admission"], False)

    def test_invalid_token_fails_closed(self):
        self.enqueue_safe()
        with self.assertRaisesRegex(worker.WorkerBlocked, "http_401"):
            worker.run_once(self.producer(WRONG_TOKEN))

    def test_timeout_fails_closed(self):
        def timeout_send(method, path, body, headers):
            raise TimeoutError("synthetic timeout")

        producer = CloudV2Producer(worker.DEFAULT_PRODUCER_ID, worker.signing_key(DP_TOKEN), timeout_send)
        with self.assertRaisesRegex(worker.WorkerBlocked, "producer_fail_closed"):
            worker.run_once(producer)

    def test_unsafe_claim_rejected_and_not_completed(self):
        def send(method, path, body, headers):
            if path.endswith("/acquire"):
                return {"status": "ACQUIRED", "owner_epoch": "epoch"}
            if path.endswith("/next"):
                return {"request": {
                    "request_id": "bad",
                    "manufacturer": "LIVE-MFR",
                    "oem": "LIVE-OEM",
                    "claim_token": "dryrun-bad",
                    "claim_generation": 1,
                }}
            if path.endswith("/release"):
                return {"status": "RELEASED"}
            self.fail("unsafe claim must not be completed")

        producer = CloudV2Producer(worker.DEFAULT_PRODUCER_ID, worker.signing_key(DP_TOKEN), send)
        with self.assertRaisesRegex(worker.WorkerBlocked, "unsafe_claim_rejected"):
            worker.run_once(producer)

    def test_secret_file_loader_rejects_short_or_missing_secret(self):
        token_path = os.path.join(self.tmp.name, "dp_sync_token")
        with open(token_path, "w", encoding="utf-8") as stream:
            stream.write("short")
        with self.assertRaisesRegex(worker.WorkerBlocked, "dedicated_dp_signing_token_required"):
            worker.load_token_file(token_path)


if __name__ == "__main__":
    unittest.main()
