"""Isolated cloud-browser host for owner-operated DCP recovery (no producer claims).

This process starts one persistent Chromium on *loopback-only CDP* and a
private recovery UI. It deliberately does NOT run dp_sync_agent, accept broker
claims, or open production admission. The eventual producer must be gated by
global server-side ownership and separately authorized to use DCP.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.request


class HostConfigurationError(RuntimeError):
    pass


def configuration(env=None, *, platform=None):
    env = dict(os.environ if env is None else env)
    platform = sys.platform if platform is None else platform
    if platform != "linux":
        raise HostConfigurationError("linux_required")
    if env.get("DCP_CLOUD_BROWSER_HOST_ENABLED") != "1":
        raise HostConfigurationError("explicit_host_enable_required")
    if env.get("DCP_CLOUD_PRODUCER_ENABLED","0") != "0":
        raise HostConfigurationError("broker_producer_not_implemented")
    if env.get("DCP_PHONE_RECOVERY_ENABLED") != "1":
        raise HostConfigurationError("manual_recovery_required")
    if not env.get("DCP_RECOVERY_TELEGRAM_BOT_TOKEN") or not str(env.get("DCP_RECOVERY_OWNER_ID","")).isdigit():
        raise HostConfigurationError("missing_owner_telegram_auth")
    root = Path(env.get("DCP_CLOUD_STATE_DIR",""))
    if not root.is_absolute() or not root.is_dir() or root.is_symlink() or not os.access(root,os.W_OK):
        raise HostConfigurationError("existing_persistent_state_dir_required")
    # Require separate state mount, not ephemeral repo checkout or root fs.
    if str(root) in {"/","/tmp","/app","/home"} or root.parent == Path("/tmp"):
        raise HostConfigurationError("ephemeral_or_root_state_forbidden")
    if not env.get("DISPLAY"):
        raise HostConfigurationError("interactive_display_required")
    executable=env.get("DCP_CHROMIUM_EXECUTABLE","")
    if not executable or not Path(executable).is_absolute() or not Path(executable).is_file():
        raise HostConfigurationError("explicit_installed_chromium_required")
    port=int(env.get("PORT","8080"))
    if not 1024 <= port <= 65535 or port == 9222:
        raise HostConfigurationError("invalid_http_port")
    return {"state":root,"executable":executable,"port":port}


def run(config):
    import fcntl
    import uvicorn

    state=config["state"]
    profile=state/"browser-profile"
    profile.mkdir(mode=0o700,exist_ok=True)
    os.chmod(profile,0o700)
    lockpath=state/"cloud-browser-owner.lock"
    descriptor=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    browser=None
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        browser=subprocess.Popen([
            config["executable"],
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            "--no-first-run",
            "--no-default-browser-check",
            "--user-data-dir="+str(profile),
            "file:///app/dcp_phone_staging_mock.html"
            if os.environ.get("DCP_PHONE_STAGING_MOCK_ONLY") == "1"
            else "https://www.dealercostparts.com/",
        ],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
        close_fds=True,start_new_session=True)
        for _ in range(35):
            if browser.poll() is not None:
                raise HostConfigurationError("browser_terminated_during_startup")
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/version",timeout=1) as response:
                    if response.status==200:break
            except OSError:
                time.sleep(.2)
        else:
            raise HostConfigurationError("browser_loopback_unavailable")
        # Only a manually operated UI is launched. This does not imply DCP READY.
        uvicorn.run("dcp_phone_recovery_app:app",host="0.0.0.0",port=config["port"],
                    log_level="warning",access_log=False,workers=1)
    finally:
        if browser and browser.poll() is None:
            browser.terminate()
            try:browser.wait(timeout=5)
            except subprocess.TimeoutExpired:browser.kill()
        os.close(descriptor)


def main():
    try:
        config=configuration()
        run(config)
        return 0
    except (HostConfigurationError,BlockingIOError,ValueError) as exc:
        print("DCP_CLOUD_BROWSER_HOST_BLOCKED:",type(exc).__name__,file=sys.stderr)
        return 24


if __name__=="__main__":
    raise SystemExit(main())
