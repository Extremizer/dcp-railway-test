"""Headless offline-only Chromium launcher for mock PHONE-FIRST acceptance."""
import fcntl
import os
from pathlib import Path
import subprocess
import time
import urllib.request
import tempfile
import shutil

def _reason(log_file):
    try:
        text=Path(log_file).read_text(errors="replace").lower()[-40000:]
    except OSError:
        return "no_browser_stderr"
    for token,code in (
        ("devtoolsactiveport", "devtools_port_error"),
        ("profile in use", "profile_locked"),
        ("process singleton", "profile_singleton"),
        ("failed to create", "browser_create_failed"),
        ("permission denied", "file_permission_denied"),
        ("no space left", "volume_full"),
        ("cannot allocate memory", "out_of_memory"),
        ("resource temporarily unavailable", "resource_limit"),
        ("connection refused", "cdp_connection_refused"),
    ):
        if token in text:
            return code
    return "unknown_browser_stderr"

def main():
    if os.getenv("DCP_PHONE_STAGING_MOCK_ONLY") != "1":
        print("MOCK_ONLY_REQUIRED",flush=True)
        return 24
    state=Path(os.getenv("DCP_CLOUD_STATE_DIR","/data"))
    if not state.is_dir() or state.is_symlink() or not os.access(state,os.W_OK):
        print("PERSISTENT_VOLUME_REQUIRED",flush=True)
        return 24
    lock=os.open(state/"staging.lock",os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    browser=None
    ephemeral_profile=None
    try:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        # Mock staging has no DCP session; a unique throwaway Chrome profile
        # avoids process-singleton conflicts during Railway rolling deploys.
        # Persistent volume is retained for one-time Telegram send ledger.
        ephemeral_profile=tempfile.mkdtemp(prefix="dcp-mock-",dir="/tmp")
        profile=Path(ephemeral_profile)
        os.chmod(profile,0o700)
        logfile=state/"mock-chromium-startup.log"
        args=[
            "/usr/bin/chromium","--headless=new","--no-sandbox",
            "--disable-dev-shm-usage","--disable-background-networking",
            "--no-first-run","--no-default-browser-check",
            "--disable-extensions","--disable-gpu",
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            "--remote-allow-origins=http://127.0.0.1:9222",
            "--user-data-dir="+str(profile),
            "--window-size=1280,800",
            "file:///app/dcp_phone_staging_mock.html",
        ]
        log_fd=os.open(logfile,os.O_CREAT|os.O_TRUNC|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            browser=subprocess.Popen(args,stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL,stderr=log_fd,close_fds=True)
        finally:
            os.close(log_fd)
        for attempt in range(90):
            if browser.poll() is not None:
                raise RuntimeError("browser_exited_"+str(browser.returncode))
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:9222/json/version",timeout=1
                ) as response:
                    if response.status==200:
                        print("MOCK_CDP_READY",flush=True)
                        break
            except OSError:
                pass
            time.sleep(.5)
        else:
            raise RuntimeError("mock_cdp_unavailable")
        import uvicorn
        uvicorn.run("dcp_phone_staging_app:app",host="0.0.0.0",
                    port=int(os.getenv("PORT","8080")),
                    log_level="warning",access_log=False,workers=1)
        return 0
    except Exception as exc:
        # No browser command-line, URL, password, token or user ID in logs.
        category=(str(exc) if isinstance(exc,RuntimeError)
                  and str(exc).startswith(("mock_cdp_unavailable","browser_exited_"))
                  else type(exc).__name__)
        print("STAGING_BLOCKED",category,"BROWSER_DIAG",
              _reason(state/"mock-chromium-startup.log"),flush=True)
        return 24
    finally:
        if browser and browser.poll() is None:
            browser.terminate()
            try:browser.wait(timeout=4)
            except subprocess.TimeoutExpired:browser.kill()
        if ephemeral_profile:
            shutil.rmtree(ephemeral_profile,ignore_errors=True)
        os.close(lock)

if __name__=="__main__":
    raise SystemExit(main())
