"""Synthetic-only HTTPS receiver for an isolated Railway staging service.

Railway terminates HTTPS; this app accepts bounded HTTP from its proxy.
Never handles production credentials or actual DCP traffic. No DB or files.
Its replay ledger is intentionally process-local and NOT production-grade.
"""
from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from oem_cache_bridge_v1 import BridgeRejected, MAX_BODY_BYTES
from oem_bridge_stage_receiver import SyntheticReceiver

STAGING_PATH = "/internal/oem-cache-bridge/v1"


def make_handler(receiver: SyntheticReceiver):
    nonce_lock = threading.Lock()  # Serialize in-memory replay verification.

    class StagingHandler(BaseHTTPRequestHandler):
        server_version = "OEMBridgeStage/1"
        sys_version = ""

        def log_message(self, fmt, *args):
            return  # Never log URLs, signatures, headers or request bodies.

        def _reply(self, status: int, result: dict):
            body = json.dumps(result, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path != "/health":
                self._reply(404, {"status": "NOT_FOUND"})
                return
            self._reply(200, {"status": "SYNTHETIC_STAGE_READY"})

        def do_POST(self):
            if self.path != STAGING_PATH:
                self._reply(404, {"status": "NOT_FOUND"})
                return
            raw_length = self.headers.get("Content-Length", "")
            try:
                length = int(raw_length)
            except (TypeError, ValueError):
                self._reply(400, {"status": "INVALID_LENGTH"})
                return
            if length <= 0 or length > MAX_BODY_BYTES:
                self._reply(413, {"status": "INVALID_LENGTH"})
                return
            body = self.rfile.read(length)
            if len(body) != length:
                self._reply(400, {"status": "INVALID_BODY"})
                return
            signature_headers = {
                name: self.headers.get(name, "") for name in
                ("X-OEM-Bridge-Timestamp", "X-OEM-Bridge-Nonce",
                 "X-OEM-Bridge-Signature")
            }
            try:
                from datetime import datetime, timezone
                with nonce_lock:
                    outcome = receiver.deliver(
                        body, signature_headers, now=datetime.now(timezone.utc)
                    )
            except BridgeRejected:
                self._reply(403, {"status": "REJECTED"})
                return
            except Exception:
                self._reply(500, {"status": "REJECTED"})
                return
            self._reply(200, outcome)

    return StagingHandler


def main():
    if os.getenv("OEM_BRIDGE_STAGE_SYNTHETIC_ONLY") != "1":
        raise SystemExit("STAGE_NOT_ENABLED")
    key = os.getenv("OEM_BRIDGE_STAGE_KEY", "")
    if len(key.encode("utf-8")) < 24:
        raise SystemExit("MISSING_SEPARATE_STAGE_KEY")
    port = int(os.environ["PORT"])
    if not (1 <= port <= 65535):
        raise SystemExit("INVALID_PORT")
    receiver = SyntheticReceiver(key.encode("utf-8"))
    server = ThreadingHTTPServer(("0.0.0.0", port), make_handler(receiver))
    server.serve_forever()


if __name__ == "__main__":
    main()
