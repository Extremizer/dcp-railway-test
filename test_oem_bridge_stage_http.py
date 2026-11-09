"""Pure synthetic local HTTP integration; no DCP, Railway or external traffic."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

from oem_bridge_stage_http import make_handler, STAGING_PATH
from oem_bridge_stage_receiver import SyntheticReceiver
from oem_cache_bridge_v1 import envelope, identity_from_dcp, sign_for_future_https

KEY = b"synthetic-test-key-not-for-real-use-00001"
OEM = "0627-084"


class StageHTTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
            make_handler(SyntheticReceiver(KEY)))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = "http://127.0.0.1:" + str(cls.server.server_address[1])

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=2)

    def post(self, body: bytes, headers: dict, path=STAGING_PATH):
        request = urllib.request.Request(self.base + path, body, headers=headers,
                                         method="POST")
        try:
            with urllib.request.urlopen(request, timeout=3) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as exc:
            try:
                return exc.code, json.loads(exc.read())
            finally:
                exc.close()

    def signed(self, nonce):
        now = datetime.now(timezone.utc)
        source = {
            "status": "FOUND", "manufacturer": "Arctic Cat",
            "query_oem": OEM, "matched_sku": OEM,
            "current_oem": OEM,
            "source": "server_html:current_oem_0627-084",
            "http_status": 200, "authorized": True,
            "dealer_price_usd": 1234.56,  # Synthetic sentinel; never sent.
            "session_cookie": "PRIVATE_SYNTHETIC",
        }
        ident = identity_from_dcp(source, now=now)
        body, headers = sign_for_future_https(
            envelope(ident, now=now), KEY,
            nonce=nonce, timestamp=int(now.timestamp()))
        self.assertNotIn(b"1234.56", body)
        self.assertNotIn(b"PRIVATE_SYNTHETIC", body)
        return body, headers

    def test_health(self):
        with urllib.request.urlopen(self.base + "/health", timeout=3) as res:
            self.assertEqual(json.loads(res.read())["status"], "SYNTHETIC_STAGE_READY")

    def test_one_identity_delivery_and_replay(self):
        body, headers = self.signed("stage_http_nonce_00000001")
        code, data = self.post(body, headers)
        self.assertEqual(code, 200)
        self.assertTrue(data["identity_only"])
        self.assertFalse(data["cache_write"])
        self.assertFalse(data["data_persisted"])
        code2, _ = self.post(body, headers)
        self.assertEqual(code2, 403)

    def test_tampered(self):
        body, headers = self.signed("stage_http_nonce_00000002")
        code, data = self.post(body.replace(b"0627-084", b"0627-111", 1), headers)
        self.assertEqual(code, 403)
        self.assertEqual(data["status"], "REJECTED")

    def test_oversize(self):
        body, headers = self.signed("stage_http_nonce_00000003")
        code, data = self.post(body + b"x" * 2500, headers)
        self.assertEqual(code, 413)
        self.assertEqual(data["status"], "INVALID_LENGTH")

    def test_priced_payload_denied_in_staging(self):
        now = datetime.now(timezone.utc)
        ident = identity_from_dcp({
            "status": "FOUND", "manufacturer": "Arctic Cat",
            "query_oem": OEM, "matched_sku": OEM, "current_oem": OEM,
            "source": "server_html:current_oem_0627-084",
            "http_status": 200, "authorized": True,
        }, now=now)
        proof = {
            "manufacturer": "Arctic Cat", "current_oem": OEM,
            "msrp_usd": 78.91,
            "source_kind": "validated_public_msrp_cache",
            "source_ref": "synthetic_fixture_only",
            "verified_at": now.isoformat(),
            "attestation": "SEPARATELY_VERIFIED_PUBLIC_MSRP",
        }
        body, headers = sign_for_future_https(
            envelope(ident, public_msrp=proof, now=now), KEY,
            nonce="stage_http_nonce_00000005", timestamp=int(now.timestamp()))
        code, data = self.post(body, headers)
        self.assertEqual(code, 403)
        self.assertEqual(data["status"], "REJECTED")

    def test_wrong_path(self):
        body, headers = self.signed("stage_http_nonce_00000004")
        code, _ = self.post(body, headers, "/unrecognized")
        self.assertEqual(code, 404)


if __name__ == "__main__":
    unittest.main()
