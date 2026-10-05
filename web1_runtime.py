#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Run Extremizer production components in one Railway service/container.

Processes:
- @Extremizer_bot -> extremizer_bot.py
- @ExtremizerBOT -> probnik_app.py when PROBNIK_BOT_TOKEN exists
- WEB1 -> uvicorn web_app:app

@OEMixiBOT is intentionally not started here because its current dealer-price
backend requires an authorized local Chrome CDP session on 127.0.0.1:9222.
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

    print("WEB1_RUNTIME: running persistent seed", flush=True)
    seed = subprocess.run([sys.executable, "-u", "seed_probe14.py"], check=False)
    if seed.returncode != 0:
        print(f"WEB1_RUNTIME: seed failed with {seed.returncode}", flush=True)
        return seed.returncode or 1

    if not os.getenv("EXTREMIZER_BOT_TOKEN", "").strip():
        print("WEB1_RUNTIME: EXTREMIZER_BOT_TOKEN missing", flush=True)
        return 2

    _spawn("main_bot", [sys.executable, "-u", "extremizer_bot.py"])

    if os.getenv("PROBNIK_BOT_TOKEN", "").strip():
        _spawn("probnik", [sys.executable, "-u", "probnik_app.py"])
    else:
        print("WEB1_RUNTIME: PROBNIK_BOT_TOKEN absent; Probnik disabled", flush=True)

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
            "web_app:app",
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
