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

    raw_db_path = str(env.get("OEMIXIBOT_DB_PATH") or "").strip()
    if not raw_db_path:
        raise ValueError("OEMIXIBOT_DB_PATH_required")
    db_path = Path(raw_db_path)
    if not db_path.is_absolute():
        raise ValueError("OEMIXIBOT_DB_PATH_must_be_absolute")

    raw_identity_db = str(env.get("OEMIXIBOT_IDENTITY_DB") or "").strip()
    if not raw_identity_db:
        raise ValueError("OEMIXIBOT_IDENTITY_DB_required")
    identity_db = Path(raw_identity_db)
    if not identity_db.is_absolute():
        raise ValueError("OEMIXIBOT_IDENTITY_DB_must_be_absolute")

    raw_reference_db = str(
        env.get("EXTREMIZER_OEM_REFERENCE_DB_FILE") or ""
    ).strip()
    if not raw_reference_db:
        raise ValueError("EXTREMIZER_OEM_REFERENCE_DB_FILE_required")
    reference_db = Path(raw_reference_db)
    if not reference_db.is_absolute():
        raise ValueError("EXTREMIZER_OEM_REFERENCE_DB_FILE_must_be_absolute")

    if str(env.get("OEMIXIBOT_SHARED_IDENTITY_ONLY") or "").strip() != "1":
        raise ValueError("OEMIXIBOT_SHARED_IDENTITY_ONLY_must_equal_1")

    gateway_url = str(env.get("OEMIXIBOT_GATEWAY_BASE_URL") or "").strip()
    from urllib.parse import urlparse
    parsed_gateway = urlparse(gateway_url)
    private_http = (
        parsed_gateway.scheme == "http"
        and str(parsed_gateway.hostname or "").lower().endswith(".railway.internal")
    )
    if parsed_gateway.scheme != "https" and not private_http:
        raise ValueError(
            "OEMIXIBOT_GATEWAY_BASE_URL_https_or_railway_private_http_required"
        )

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
        "identity_db": identity_db,
        "reference_db": reference_db,
        "gateway_url": gateway_url,
        "gateway_secret": gateway_secret,
        "dealer_max_age_hours": dealer_hours,
        "msrp_max_age_hours": msrp_hours,
        "port": port,
    }


def _verify_sqlite(path: Path | str, *, foreign_keys: bool = False) -> None:
    p = Path(path)
    if not p.exists() or not p.is_file():
        raise RuntimeError(f"required_persistent_db_missing:{p.name}")
    with sqlite3.connect(p, timeout=10) as conn:
        if str(conn.execute("PRAGMA integrity_check").fetchone()[0]).lower() != "ok":
            raise RuntimeError(f"sqlite_integrity_check_failed:{p.name}")
        if foreign_keys:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError(
                    f"sqlite_foreign_key_check_failed:{p.name}:{len(violations)}"
                )


def verify_persistent_store(config: dict[str, Any]) -> None:
    """Fail closed unless the migrated OEMixi volume bundle is present."""
    main = Path(config["db_path"])
    parent = main.parent
    if not parent.exists() or not parent.is_dir():
        raise RuntimeError("oemixibot_volume_directory_missing")

    # Prove the mounted directory is writable without mutating business DBs.
    probe = parent / ".oemixibot-runtime-write-probe"
    try:
        with probe.open("xb") as fh:
            fh.write(b"ok")
            fh.flush()
            os.fsync(fh.fileno())
    except FileExistsError:
        probe.unlink()
        with probe.open("xb") as fh:
            fh.write(b"ok")
            fh.flush()
            os.fsync(fh.fileno())
    finally:
        if probe.exists():
            probe.unlink()

    _verify_sqlite(config["db_path"], foreign_keys=True)
    _verify_sqlite(config["identity_db"])
    _verify_sqlite(config["reference_db"])


def apply_runtime_environment(config: dict[str, Any]) -> None:
    """Pin import-time OEMixi modules to the migrated Railway volume."""
    os.environ["OEMIXIBOT_DB_PATH"] = str(config["db_path"])
    os.environ["OEMIXIBOT_IDENTITY_DB"] = str(config["identity_db"])
    os.environ["EXTREMIZER_OEM_REFERENCE_DB_FILE"] = str(config["reference_db"])
    os.environ["OEMIXIBOT_SHARED_IDENTITY_ONLY"] = "1"
    # Existing identity + analytics clients use this base URL. Keep them on
    # the same Railway private service boundary as the price gateway.
    os.environ["EXTREMIZER_ANALYTICS_BASE_URL"] = str(config["gateway_url"])




def build_railway_application(
    config: dict[str, Any],
    *,
    transport_factory=None,
    app_module=None,
):
    """Build OEMixiBOT with explicit secure Gateway binding before polling."""
    # Delayed imports preserve environment/path configuration before globals
    # in the existing OEMixi modules are constructed.
    from oemixi_secure_gateway_transport import (
        HTTPSPriceTransport,
        OEMixiCanonicalSharedPriceAdapter,
    )
    if app_module is None:
        import oemixibot_app as app_module

    factory = transport_factory or (
        lambda base_url, root_secret:
        HTTPSPriceTransport(
            base_url,
            root_secret,
            timeout_seconds=7,
            allow_railway_private_http=True,
        )
    )
    transport = factory(
        config["gateway_url"],
        config["gateway_secret"],
    )
    application = app_module.build_application(
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
        verify_persistent_store(config)
        apply_runtime_environment(config)
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
