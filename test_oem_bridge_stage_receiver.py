"""Isolated in-memory delivery tests; no network or real storage."""
from datetime import datetime, timezone
import unittest

from oem_cache_bridge_v1 import (
    BridgeRejected, envelope, identity_from_dcp, sign_for_future_https,
)
from oem_bridge_stage_receiver import SyntheticReceiver

NOW = datetime(2026, 11, 9, 19, 0, tzinfo=timezone.utc)
KEY = b"stage-synthetic-key-not-for-production-000001"
NONCE = "stage_0627_084_nonce_01"

def synthetic():
    return {
        "status": "FOUND", "manufacturer": "Arctic Cat",
        "query_oem": "0627-084", "matched_sku": "0627-084",
        "current_oem": "0627-084",
        "source": "server_html:current_oem_0627-084",
        "authorized": True, "http_status": 200,
        "dealer_price_usd": 142.15,
        "session_cookie": "DO_NOT_SEND",
    }

def payload(nonce=NONCE):
    identity = identity_from_dcp(synthetic(), now=NOW)
    return sign_for_future_https(
        envelope(identity, now=NOW), KEY,
        nonce=nonce, timestamp=int(NOW.timestamp()),
    )

class StagingReceiverTests(unittest.TestCase):
    def test_one_synthetic_delivery_identity_only(self):
        body, headers = payload()
        self.assertNotIn(b"142.15", body)
        self.assertNotIn(b"DO_NOT_SEND", body)
        r = SyntheticReceiver(KEY).deliver(body, headers, now=NOW)
        self.assertEqual(r, {
            "accepted": True, "identity_only": True,
            "cache_write": False, "data_persisted": False
        })

    def test_duplicate_rejected(self):
        rx = SyntheticReceiver(KEY)
        body, headers = payload()
        rx.deliver(body, headers, now=NOW)
        with self.assertRaises(BridgeRejected):
            rx.deliver(body, headers, now=NOW)

    def test_tampered_message_rejected(self):
        body, headers = payload()
        with self.assertRaises(BridgeRejected):
            SyntheticReceiver(KEY).deliver(body.replace(b"0627-084",b"0627-111",1), headers, now=NOW)

    def test_other_oem_rejected(self):
        r=synthetic()
        r["query_oem"] = "SYNTHETIC-999"
        r["matched_sku"] = "SYNTHETIC-999"
        with self.assertRaises(BridgeRejected):
            identity_from_dcp(r, now=NOW)

if __name__ == "__main__":
    unittest.main()
