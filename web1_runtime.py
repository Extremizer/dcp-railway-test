#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Railway WEB1-only runtime: WEB1/WEB2 HTTP + existing backup process.

No Telegram polling here, even if bot tokens exist in the environment:
main @Extremizer_bot, test @ExtremizerBOT (Probnik), and @OEMixiBOT
are managed outside Railway WEB1. Probnik is owned by ASUS/T4.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time

children = []
stopping = False


def _terminate_children():
    global stopping
    if stopping:
        return
    stopping = True
    for name, child in children:
        if child.poll() is None:
            child.terminate()
    deadline = time.time() + 8
    while time.time() < deadline:
        if all(child.poll() is not None for name, child in children):
            return
        time.sleep(0.2)
    for name, child in children:
        if child.poll() is None:
            child.kill()


def _handle_signal(signum, _frame):
    print(f"WEB1_RUNTIME: signal {signum}, stopping children", flush=True)
    _terminate_children()
    raise SystemExit(0)


def _spawn(name, args):
    child = subprocess.Popen(args)
    children.append((name, child))
    print(f"WEB1_RUNTIME: {name} pid={child.pid}", flush=True)
    return child


def main():
    port = os.getenv("PORT", "8080").strip() or "8080"

    # Telegram polling is forbidden in WEB1, regardless of token presence.
    print("WEB1_RUNTIME: WEB_ONLY_RELEASE; Probnik delegated to ASUS", flush=True)

    if os.getenv("OEMIXIBOT_TOKEN", "").strip():
        print(
            "WEB1_RUNTIME: OEMIXIBOT_TOKEN present; dealer bot remains isolated",
            flush=True,
        )

    _spawn(
        "backup_snapshot",
        [sys.executable, "-u", "backup_snapshot_helper.py", "--daily", "--hour-utc", "0", "--minute", "50"],
    )

    _spawn(
        "web",
        [
            sys.executable,
            "-u",
            "-m",
            "uvicorn",
            "web2_runtime_app:app"
            if os.getenv("EXTREMIZER_WEB2_ENABLED", "").strip() == "1"
            else "web_app:app",
            "--host",
            "0.0.0.0",
            "--port",
            port,
        ],
    )

    try:
        while True:
            for name, child in list(children):
                code = child.poll()
                if code is None:
                    continue
                print(f"WEB1_RUNTIME: child exited name={name} code={code}", flush=True)
                _terminate_children()
                return code if code != 0 else 1
            time.sleep(1)
    finally:
        _terminate_children()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    raise SystemExit(main())
