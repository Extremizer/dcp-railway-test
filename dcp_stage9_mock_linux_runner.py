"""Stage 9 Linux-only mock producer entrypoint. NEVER use real supplier access.

This is an isolated harness around the EXISTING signed-v2 producer and broker.
No HTTP destination other than loopback is accepted; no Windows fallback.
No supplier browser, cookies or credentials are loaded by this entrypoint.
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

class UnsafeConfiguration(RuntimeError):
    pass

class SingletonHeld(RuntimeError):
    pass

def _required_path(env, name):
    value = env.get(name, "").strip()
    if not value or not Path(value).is_absolute():
        raise UnsafeConfiguration(name + " must be an explicit absolute path")
    return Path(value)

def configuration(env=None):
    env = dict(os.environ if env is None else env)
    if sys.platform != "linux":
        raise UnsafeConfiguration("Linux-only runner: Windows fallback prohibited")
    if env.get("DP_RUNNER_TRANSPORT") != "fake":
        raise UnsafeConfiguration("fake supplier transport required")
    url = env.get("DP_RUNNER_SERVER_URL", "").strip()
    parsed = urlsplit(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1", "localhost"} or not parsed.port or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise UnsafeConfiguration("server target must be explicitly configured loopback mock HTTP")
    state = _required_path(env, "DP_RUNNER_STATE_DIR")
    lock = _required_path(env, "DP_RUNNER_LOCK_FILE")
    journal = _required_path(env, "DP_RUNNER_JOURNAL_FILE")
    status = _required_path(env, "DP_RUNNER_STATUS_FILE")
    if len({str(lock), str(journal), str(status)}) != 3:
        raise UnsafeConfiguration("state paths must be distinct")
    if any(x.parent != state for x in (lock, journal, status)):
        raise UnsafeConfiguration("all state paths must live in the same explicit persistent directory")
    if not state.is_dir() or state.is_symlink():
        raise UnsafeConfiguration("persistent state directory must already exist and be non-symlink")
    if not os.access(state, os.W_OK):
        raise UnsafeConfiguration("persistent state directory not writable")
    return {"url": url.rstrip("/"), "state": state, "lock": lock, "journal": journal, "status": status}

class LinuxSingleton:
    """Advisory lock tied to an OS fd; old process and replacement cannot overlap
    while sharing a persistent lock inode. External deployment-level fencing is
    STILL REQUIRED across different hosts/volumes.
    """
    def __init__(self, path):
        self.path = path
        self.fd = None

    def __enter__(self):
        flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW
        self.fd = os.open(self.path, flags, 0o600)
        try:
            if not os.path.isfile(self.path):
                raise UnsafeConfiguration("lock path must be a regular file")
            fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            os.close(self.fd)
            self.fd = None
            raise SingletonHeld("another producer owns this state volume") from e
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise
        return self

    def __exit__(self, *_):
        if self.fd is not None:
            fcntl.flock(self.fd, fcntl.LOCK_UN)
            os.close(self.fd)
            self.fd = None

def persist_status(path, runner, supplier, detail):
    """Atomic/fsynced machine-readable provenance, never a READY inference."""
    payload = {"runner": runner, "supplier": supplier, "transport": "fake",
               "detail": detail, "updated_at": datetime.now(timezone.utc).isoformat(),
               "pid": os.getpid()}
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("x", encoding="utf-8") as output:
        json.dump(payload, output, sort_keys=True)
        output.flush()
        os.fsync(output.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)

def fake_lookup(manufacturer, oem):
    # Deterministic supplier-unavailable simulator; never synthesizes dealer
    # price nor converts MSRP to DP. Test fixtures can inject other fake outcomes.
    return {"status": "TECHNICAL_ERROR", "error_code": "FAKE_SUPPLIER_UNAVAILABLE"}

def identity_approved(item, approved):
    """Only explicitly admitted manufacturer/OEM pairs may reach fake supplier."""
    if not isinstance(item, dict):
        return False
    manufacturer, oem = item.get("manufacturer"), item.get("oem")
    return (
        isinstance(manufacturer, str) and isinstance(oem, str)
        and bool(manufacturer.strip()) and bool(oem.strip())
        and manufacturer.strip().upper() not in {"AUTO", "__AUTO__", "UNKNOWN"}
        and (manufacturer, oem) in approved
    )


def execute(config, *, agent=None, lookup=None, approved_identities=()):
    """One mock cycle, not a daemon. An unresolved journal is never replayed."""
    with LinuxSingleton(config["lock"]):
        if config["journal"].exists():
            persist_status(config["status"], "quarantined", "unknown", "unresolved_journal_operator_reconciliation_required")
            return 24
        persist_status(config["status"], "alive", "unavailable", "mock_runner_started")
        if agent is None:
            import dp_sync_agent as agent
        # Override every legacy path/target before contacting the mock broker.
        agent.BASE_URL = config["url"]
        agent.JOURNAL_FILE = config["journal"]
        agent.STATUS_FILE = config["status"]
        agent._PRODUCER_STOP = False
        agent._DCP_STATUS = "READY"  # only fixture's readiness, NOT real DCP health
        agent._set_dcp_health = lambda *a, **kw: None
        supplier = lookup or fake_lookup
        agent.oemixibot_dcp_price.get_dealer_price_dict = supplier
        agent._auto_identity_price = lambda oem: {"status": "TECHNICAL_ERROR", "error_code": "UNVERIFIED_IDENTITY"}
        if hasattr(agent, '_claim'):
            original_claim = agent._claim
            def guarded_claim():
                item = original_claim()
                if item is not None and not identity_approved(item, approved_identities):
                    raise UnsafeConfiguration('unverified_identity')
                return item
            agent._claim = guarded_claim
        try:
            completed = agent.run_once()  # existing fenced claim / v2 POST / atomic completion
            if agent.JOURNAL_FILE.exists() or agent._PRODUCER_STOP:
                persist_status(config["status"], "quarantined", "unavailable", "operator_reconciliation_required")
                return 24
            persist_status(config["status"], "alive", "unavailable", "mock_claim_completed" if completed else "mock_idle")
            return 0
        except BaseException:
            # Includes KeyboardInterrupt/system exit: no auto-requeue or restart.
            persist_status(config["status"], "quarantined", "unknown", "crash_or_unconfirmed_commit")
            return 24

def main():
    try:
        cfg = configuration()
        return execute(cfg)
    except SingletonHeld:
        return 23
    except (UnsafeConfiguration, OSError, ValueError):
        return 25

if __name__ == "__main__":
    raise SystemExit(main())
