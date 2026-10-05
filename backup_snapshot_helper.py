from pathlib import Path
import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import time
from datetime import datetime, timezone

DB_NAMES = ("extremizer_orders.db", "oem_reference.db")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def make_backup(src, dst):
    source = sqlite3.connect(
        "file:" + src.as_posix() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    target = sqlite3.connect(str(dst))

    try:
        source.backup(target, pages=256, sleep=0.01)
    finally:
        target.close()
        source.close()

    check = sqlite3.connect(
        "file:" + dst.as_posix() + "?mode=ro",
        uri=True,
    )
    try:
        result = check.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        check.close()

    if result != "ok":
        raise RuntimeError(
            src.name + ": integrity_check=" + str(result)
        )


def snapshot(data_dir, out_root):
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    out_root.mkdir(parents=True, exist_ok=True)

    building = Path(
        tempfile.mkdtemp(
            prefix=".building-",
            dir=str(out_root),
        )
    )

    final = out_root / stamp

    try:
        files = []

        for name in DB_NAMES:
            src = data_dir / name

            if not src.is_file():
                raise FileNotFoundError(src)

            dst = building / name

            make_backup(src, dst)

            files.append(
                {
                    "name": name,
                    "size": dst.stat().st_size,
                    "sha256": sha256(dst),
                    "integrity_check": "ok",
                }
            )

        manifest = {
            "status": "READY",
            "created_at_utc": stamp,
            "files": files,
        }

        (building / "manifest.json").write_text(
            json.dumps(manifest, indent=2),
            encoding="utf-8",
        )

        os.replace(building, final)

        print(
            "BACKUP_SNAPSHOT_READY",
            final,
            flush=True,
        )

    except Exception:
        shutil.rmtree(building, ignore_errors=True)
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--data-dir",
        default="/data",
    )

    parser.add_argument(
        "--out-root",
        default="/tmp/extremizer-backup-auto",
    )

    parser.add_argument("--daily", action="store_true")
    parser.add_argument("--hour-utc", type=int, default=0)
    parser.add_argument("--minute", type=int, default=50)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    out_root = Path(args.out_root)

    if not args.daily:
        snapshot(data_dir, out_root)
    else:
        last_day = None
        while True:
            now = datetime.now(timezone.utc)
            day = now.strftime("%Y-%m-%d")
            if now.hour == args.hour_utc and now.minute == args.minute and day != last_day:
                try:
                    snapshot(data_dir, out_root)
                    last_day = day
                except Exception as exc:
                    print("BACKUP_SNAPSHOT_FAILED", repr(exc), flush=True)
            time.sleep(20)