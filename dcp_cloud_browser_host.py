"""Isolated cloud-browser host for owner-operated DCP recovery (no producer claims).

This process starts one persistent Chromium on *loopback-only CDP* and a
private recovery UI. It deliberately does NOT run dp_sync_agent, accept broker
claims, or open production admission. The eventual producer must be gated by
global server-side ownership and separately authorized to use DCP.
"""
from __future__ import annotations

import os
import tempfile
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


def run(config, *, app_module='dcp_phone_recovery_app:app'):
    import fcntl
    import uvicorn

    state=config["state"]
    profile=state/"browser-profile"
    runtime_home=state/"chromium-home"
    runtime_config=runtime_home/".config"
    runtime_cache=runtime_home/".cache"
    for location in (runtime_home,runtime_config,runtime_cache):
        location.mkdir(mode=0o700,parents=True,exist_ok=True)
        os.chmod(location,0o700)
    browser_env=os.environ.copy()
    browser_env.update({"HOME":str(runtime_home),"XDG_CONFIG_HOME":str(runtime_config),"XDG_CACHE_HOME":str(runtime_cache)})
    profile.mkdir(mode=0o700,exist_ok=True)
    os.chmod(profile,0o700)
    lockpath=state/"cloud-browser-owner.lock"
    descriptor=os.open(lockpath,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    browser=None
    diagnostic=tempfile.TemporaryFile(mode="w+b")
    try:
        fcntl.flock(descriptor,fcntl.LOCK_EX|fcntl.LOCK_NB)
        browser=subprocess.Popen([
            config["executable"],
            "--remote-debugging-address=127.0.0.1",
            "--remote-debugging-port=9222",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-crash-reporter",
            "--user-data-dir="+str(profile),
            "file:///app/dcp_phone_staging_mock.html"
            if os.environ.get("DCP_PHONE_STAGING_MOCK_ONLY") == "1"
            else "about:blank",
        ],env=browser_env,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=diagnostic,
        close_fds=True,start_new_session=True)
        for _ in range(35):
            if browser.poll() is not None:
                diagnostic.seek(0)
                err=diagnostic.read(8192).decode("utf-8","replace").lower()
                if any(marker in err for marker in ("no usable sandbox", "failed to move to new namespace", "operation not permitted", "namespace sandbox")):
                    code="chromium_sandbox_unavailable"
                elif any(marker in err for marker in ("cannot open display", "missing x server")):
                    code="chromium_display_unavailable"
                elif "crashpad" in err:
                    if "database is required" in err:
                        code="crashpad_database_argument_missing"
                    elif "permission denied" in err or "access denied" in err:
                        if "/data/" in err or "/data:" in err:
                            code="crashpad_permission_data"
                        elif "/tmp/" in err or "/tmp:" in err:
                            code="crashpad_permission_tmp"
                        elif "/dev/shm" in err:
                            code="crashpad_permission_shm"
                        elif "/proc/" in err:
                            code="crashpad_permission_proc"
                        elif "/home/" in err or "/home:" in err:
                            code="crashpad_permission_home"
                        else:
                            # Only emit fixed known errors, never raw stderr or paths.
                            if "chrome_crashpad_handler" in err and "exec" in err:
                                code="crashpad_handler_exec_denied"
                            elif "setuid" in err or "suid" in err:
                                code="crashpad_setuid_denied"
                            elif "ptrace" in err:
                                code="crashpad_ptrace_denied"
                            elif "socket" in err:
                                code="crashpad_socket_denied"
                            else:
                                code="crashpad_permission_unknown_path"
                    elif "operation not permitted" in err or "sandbox" in err or "namespace" in err:
                        code="crashpad_sandbox_or_namespace_denied"
                    elif "not found" in err or "no such file" in err:
                        code="crashpad_missing_file"
                    else:
                        code="chromium_crashpad_failure"
                elif "permission denied" in err:
                    code="chromium_filesystem_permission"
                else:
                    code="browser_terminated_during_startup"
                # Show only a bounded, sanitized Chromium diagnostic line.
                import re
                lines=[line.strip() for line in err.splitlines() if "crashpad" in line or "permission denied" in line]
                if lines:
                    line=lines[0][:400]
                    line=re.sub(r"(?i)(?:/[a-z0-9_.-]+)+", "[PATH]", line)
                    line=re.sub(r"(?i)[a-z0-9_.+-]+@[a-z0-9.-]+", "[EMAIL]", line)
                    line=re.sub(r"(?i)(?:token|password|authorization|cookie)[=: ]+\\S+", "[REDACTED]", line)
                    line=re.sub(r"[A-Za-z0-9_-]{32,}", "[REDACTED]", line)
                    print("CHROMIUM_DIAGNOSTIC_SANITIZED",line[:300],flush=True)
                raise HostConfigurationError(code)
            try:
                with urllib.request.urlopen("http://127.0.0.1:9222/json/version",timeout=1) as response:
                    if response.status==200:break
            except OSError:
                time.sleep(.2)
        else:
            raise HostConfigurationError("browser_loopback_unavailable")
        # Only a manually operated UI is launched. This does not imply DCP READY.
        uvicorn.run(app_module,host="0.0.0.0",port=config["port"],
                    log_level="warning",access_log=False,workers=1)
    finally:
        if browser and browser.poll() is None:
            browser.terminate()
            try:browser.wait(timeout=5)
            except subprocess.TimeoutExpired:browser.kill()
        diagnostic.close()
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
