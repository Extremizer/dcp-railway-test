"""Railway 24/7 OEMixiBOT worker candidate.

This is an isolated entrypoint only. Nothing imports or starts it from the
current production Railway service.

Required runtime topology:
  OEMixiBOT worker -> secure Shared Gateway -> existing server coordination DB

The worker never opens DCP/browser paths directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import fcntl
import json
import math
import os
from pathlib import Path
import signal
import sqlite3
import threading
from typing import Any

DEALER_MAX_AGE_HOURS = 24.0
MSRP_MAX_AGE_HOURS = 720.0


@dataclass
class RuntimeState:
    started: bool = False
    ready: bool = False
    shutting_down: bool = False
    last_error: str | None = None
    db_path: str | None = None
    gateway_url: str | None = None

    def health_payload(self) -> dict[str, Any]:
        return {
            "service": "oemixibot",
            "started": self.started,
            "ready": self.ready,
            "shutting_down": self.shutting_down,
            "last_error": self.last_error,
        }


class SingletonFileLock:
    """Single-process guard on the OEMixi persistent volume."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self._fh = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = self.path.open("a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            fh.close()
            raise RuntimeError("oemixibot_singleton_already_running")
        fh.seek(0)
        fh.truncate()
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        if self._fh is None:
            return
        try:
            fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()


def _validate_positive_hours(value: Any, expected: float, name: str) -> float:
    if type(value) not in (int, float):
        raise ValueError(f"{name}_must_be_numeric")
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name}_must_be_positive")
    if value != expected:
        raise ValueError(f"{name}_must_equal_approved_policy_{expected:g}")
    return value


def validate_runtime_config(env: dict[str, str] | None = None) -> dict[str, Any]:
    env = dict(os.environ if env is None else env)
    token = str(env.get("OEMIXIBOT_TOKEN") or "").strip()
    if not token:
        raise ValueError("OEMIXIBOT_TOKEN_required")

    db_path = Path(str(env.get("OEMIXIBOT_DB_PATH") or "").strip())
    if not str(db_path):
        raise ValueError("OEMIXIBOT_DB_PATH_required")
    if not db_path.is_absolute():
        raise ValueError("OEMIXIBOT_DB_PATH_must_be_absolute")

    gateway_url = str(env.get("OEMIXIBOT_GATEWAY_BASE_URL") or "").strip()
    if not gateway_url.startswith("https://"):
        raise ValueError("OEMIXIBOT_GATEWAY_BASE_URL_https_required")

    gateway_secret = str(env.get("OEMIXIBOT_GATEWAY_SECRET") or "").strip()
    if len(gateway_secret) < 16:
        raise ValueError("OEMIXIBOT_GATEWAY_SECRET_required")

    dealer_hours = _validate_positive_hours(
        float(env.get("OEMIXIBOT_DEALER_MAX_AGE_HOURS", DEALER_MAX_AGE_HOURS)),
        DEALER_MAX_AGE_HOURS,
        "dealer_freshness",
    )
    msrp_hours = _validate_positive_hours(
        float(env.get("OEMIXIBOT_MSRP_MAX_AGE_HOURS", MSRP_MAX_AGE_HOURS)),
        MSRP_MAX_AGE_HOURS,
        "msrp_freshness",
    )

    port = int(env.get("PORT", "8080"))
    if not (1 <= port <= 65535):
        raise ValueError("PORT_invalid")

    return {
        "token": token,
        "db_path": db_path,
        "gateway_url": gateway_url,
        "gateway_secret": gateway_secret,
        "dealer_max_age_hours": dealer_hours,
        "msrp_max_age_hours": msrp_hours,
        "port": port,
    }


def verify_persistent_store(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Initialization is intentionally left to the existing OemixiStore.
    # This check proves the target directory is writable and SQLite can open it.
    with sqlite3.connect(path, timeout=10) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS railway_runtime_probe_v1(
                   id INTEGER PRIMARY KEY CHECK(id=1),
                   checked_at TEXT NOT NULL
               )"""
        )
        conn.execute(
            """INSERT INTO railway_runtime_probe_v1(id,checked_at)
               VALUES(1,strftime('%Y-%m-%dT%H:%M:%fZ','now'))
               ON CONFLICT(id) DO UPDATE SET checked_at=excluded.checked_at"""
        )
        conn.commit()
        if str(conn.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
            raise RuntimeError("oemixibot_store_integrity_check_failed")


def build_railway_application(config: dict[str, Any]):
    """Build OEMixiBOT with explicit secure Gateway binding before polling."""
    # Delayed imports preserve environment/path configuration before globals
    # in the existing OEMixi modules are constructed.
    from oemixi_secure_gateway_transport import (
        HTTPSPriceTransport,
        OEMixiCanonicalSharedPriceAdapter,
    )
    import oemixibot_app

    transport = HTTPSPriceTransport(
        config["gateway_url"],
        config["gateway_secret"],
        timeout_seconds=7,
    )
    application = oemixibot_app.build_application(
        token=config["token"],
        shared_price_gateway=transport,
        dealer_max_age_hours=config["dealer_max_age_hours"],
        shared_price_adapter_cls=OEMixiCanonicalSharedPriceAdapter,
    )
    return application


def _health_handler(state: RuntimeState):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ("/health", "/ready"):
                self.send_response(404)
                self.end_headers()
                return
            payload = state.health_payload()
            code = 200
            if self.path == "/ready" and not state.ready:
                code = 503
            body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            return

    return Handler


def start_health_server(state: RuntimeState, port: int):
    server = ThreadingHTTPServer(("0.0.0.0", int(port)), _health_handler(state))
    thread = threading.Thread(
        target=server.serve_forever,
        name="oemixibot-health",
        daemon=True,
    )
    thread.start()
    return server, thread


def run(env: dict[str, str] | None = None) -> None:
    config = validate_runtime_config(env)
    state = RuntimeState(
        started=True,
        ready=False,
        db_path=str(config["db_path"]),
        gateway_url=config["gateway_url"],
    )
    lock = SingletonFileLock(str(config["db_path"]) + ".polling.lock")
    health_server = None
    application = None

    try:
        lock.acquire()
        verify_persistent_store(config["db_path"])
        application = build_railway_application(config)
        health_server, _thread = start_health_server(state, config["port"])
        state.ready = True

        # PTB owns SIGINT/SIGTERM handling in the main thread and performs a
        # graceful updater/application shutdown. Railway restart policy can
        # restart the worker after an abnormal exit.
        application.run_polling(
            drop_pending_updates=False,
            stop_signals=(signal.SIGINT, signal.SIGTERM),
        )
    except BaseException as exc:
        state.last_error = type(exc).__name__
        raise
    finally:
        state.shutting_down = True
        state.ready = False
        if health_server is not None:
            health_server.shutdown()
            health_server.server_close()
        lock.release()


def main() -> None:
    run()


if __name__ == "__main__":
    main()
