"""UltraVDS mock-only PHONE-FIRST bridge for Canonical Snap Chromium.

No DCP navigation, producer, broker, or dealer session. The ONLY launch target
is a local static mock page. CDP and HTTP listen on loopback only.
Requires an already configured non-root user systemd session (Snap confinement).
"""
from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request

UID = 1001
BASE = Path("/home/extremizer-dcp/snap/chromium/common")
STATE = BASE / "dcp-phone-first"
MOCK = BASE / "dcp-phone-first-mock.html"
CHROMIUM = Path("/snap/bin/chromium")
CDP = "http://127.0.0.1:9222/json"
MOCK_URL = MOCK.as_uri()
PORT = 8080

class VpsMockBlocked(RuntimeError):
    pass

def preflight(env=None, *, uid=None, platform=None):
    env = dict(os.environ if env is None else env)
    uid = os.geteuid() if uid is None else uid
    platform = sys.platform if platform is None else platform
    if platform != "linux" or uid != UID:
        raise VpsMockBlocked("dedicated_linux_user_required")
    required = {
        "DCP_PHONE_STAGING_MOCK_ONLY": "1",
        "DCP_PHONE_ULTRAVDS_MOCK_ONLY": "1",
        "DCP_CLOUD_PRODUCER_ENABLED": "0",
        "DCP_LIVE_NAVIGATION_GO": "0",
    }
    if any(env.get(key) != expected for key,expected in required.items()):
        raise VpsMockBlocked("mock_only_admission_required")
    local_probe = env.get("DCP_PHONE_ULTRAVDS_LOCAL_PREFLIGHT") == "1"
    if not local_probe and (
        not env.get("DCP_RECOVERY_TELEGRAM_BOT_TOKEN")
        or not env.get("DCP_RECOVERY_OWNER_ID", "").isdigit()
    ):
        raise VpsMockBlocked("owner_telegram_required")
    if env.get("DCP_CLOUD_STATE_DIR") != str(STATE):
        raise VpsMockBlocked("fixed_state_directory_required")
    if env.get("XDG_RUNTIME_DIR") != f"/run/user/{UID}":
        raise VpsMockBlocked("snap_user_session_required")
    if not CHROMIUM.is_file():
        raise VpsMockBlocked("snap_chromium_not_installed")
    if not Path(env["XDG_RUNTIME_DIR"]).is_dir():
        raise VpsMockBlocked("snap_user_session_missing")
    return True

def browser_args(profile):
    return [
        str(CHROMIUM),
        "--headless=new",
        "--remote-debugging-address=127.0.0.1",
        "--remote-debugging-port=9222",
        "--user-data-dir=" + str(profile),
        "--window-size=1280,800",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-extensions",
        MOCK_URL,
    ]

def _mock_ready():
    with urllib.request.urlopen(CDP, timeout=1) as response:
        targets=json.load(response)
    return any(t.get("type") == "page" and t.get("url") == MOCK_URL
               for t in targets)

def run():
    import uvicorn
    preflight()
    os.umask(0o077)
    if STATE.is_symlink():
        raise VpsMockBlocked("state_symlink_forbidden")
    STATE.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(STATE, 0o700)
    if STATE.stat().st_uid != UID:
        raise VpsMockBlocked("state_owner_mismatch")
    source = Path(__file__).with_name("dcp_phone_staging_mock.html")
    if MOCK.is_symlink() or not source.is_file():
        raise VpsMockBlocked("mock_source_invalid")
    # Snap can open static files and profiles inside its own writable common dir.
    MOCK.write_bytes(source.read_bytes())
    os.chmod(MOCK, 0o600)
    fd = os.open(STATE / "vps-mock.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    browser = None
    profile = None
    stderr = tempfile.TemporaryFile(mode="w+b")
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        profile = Path(tempfile.mkdtemp(prefix="profile-", dir=str(STATE)))
        browser = subprocess.Popen(
            browser_args(profile), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=stderr, start_new_session=True, close_fds=True,
        )
        for _ in range(60):
            if browser.poll() is not None:
                raise VpsMockBlocked("chromium_startup_failed")
            try:
                if _mock_ready():
                    break
            except (OSError, ValueError, json.JSONDecodeError):
                pass
            time.sleep(0.5)
        else:
            raise VpsMockBlocked("loopback_mock_cdp_unavailable")
        print("ULTRAVDS_PHONE_MOCK_LOCAL_READY", flush=True)
        # No public listener. HTTPS reverse proxy is a separate, gated step.
        uvicorn.run("dcp_phone_staging_app:app", host="127.0.0.1", port=PORT,
                    workers=1, log_level="warning", access_log=False)
    finally:
        if browser is not None and browser.poll() is None:
            browser.terminate()
            try:
                browser.wait(timeout=5)
            except subprocess.TimeoutExpired:
                browser.kill()
        stderr.close()
        if profile is not None:
            shutil.rmtree(profile, ignore_errors=True)
        os.close(fd)

def main():
    try:
        run()
        return 0
    except (VpsMockBlocked, BlockingIOError, OSError) as exc:
        code = str(exc) if isinstance(exc,VpsMockBlocked) else type(exc).__name__
        print("ULTRAVDS_PHONE_MOCK_BLOCKED", code, file=sys.stderr, flush=True)
        return 24

if __name__ == "__main__":
    raise SystemExit(main())
