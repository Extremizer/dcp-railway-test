from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config


ENV_ENDPOINT = "EXTREMIZER_BACKUP_S3_ENDPOINT"
ENV_BUCKET = "EXTREMIZER_BACKUP_S3_BUCKET"
ENV_REGION = "EXTREMIZER_BACKUP_S3_REGION"
ENV_ACCESS_KEY = "EXTREMIZER_BACKUP_S3_ACCESS_KEY_ID"
ENV_SECRET_KEY = "EXTREMIZER_BACKUP_S3_SECRET_ACCESS_KEY"
ENV_ADDRESSING_STYLE = "EXTREMIZER_BACKUP_S3_ADDRESSING_STYLE"
ENV_TMP_ROOT = "EXTREMIZER_BACKUP_TMP_ROOT"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _required(name: str) -> str:
    value = str(os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"missing required backup object-store variable: {name}")
    return value


def _make_s3_client():
    endpoint = _required(ENV_ENDPOINT)
    region = str(os.getenv(ENV_REGION) or "auto").strip() or "auto"
    access_key = _required(ENV_ACCESS_KEY)
    secret_key = _required(ENV_SECRET_KEY)
    style = str(os.getenv(ENV_ADDRESSING_STYLE) or "virtual").strip() or "virtual"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        config=Config(s3={"addressing_style": style}),
    )


def _stream_sha256(body) -> str:
    h = hashlib.sha256()
    for block in iter(lambda: body.read(8 * 1024 * 1024), b""):
        h.update(block)
    return h.hexdigest()


def _sqlite_online_backup(src: Path, dst: Path) -> None:
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


def _sqlite_integrity(path: Path) -> str:
    conn = sqlite3.connect(
        "file:" + path.as_posix() + "?mode=ro",
        uri=True,
        timeout=30,
    )
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def backup_sqlite_to_bucket(
    src_path: str | Path,
    object_key: str,
    *,
    metadata: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Create a consistent SQLite backup in /tmp, upload it, and verify a full round-trip hash.

    Nothing is persisted under /data by this function. Any failure raises before the
    caller can continue with the protected mutation.
    """
    src = Path(src_path)
    if not src.is_file():
        raise FileNotFoundError(src)

    key = str(object_key or "").strip().lstrip("/")
    if not key or key.endswith("/") or ".." in Path(key).parts:
        raise ValueError("invalid backup object key")

    bucket = _required(ENV_BUCKET)
    tmp_root = Path(os.getenv(ENV_TMP_ROOT, "/tmp/extremizer-backup-stage"))
    tmp_root.mkdir(parents=True, exist_ok=True)

    db_size = src.stat().st_size
    free = shutil.disk_usage(tmp_root).free
    required_free = int(db_size * 1.25) + 100 * 1024 * 1024
    if free < required_free:
        raise RuntimeError(
            f"insufficient temporary space for safe SQLite backup: free={free} required={required_free}"
        )

    with tempfile.TemporaryDirectory(prefix="sqlite-", dir=str(tmp_root)) as work:
        local_backup = Path(work) / src.name
        _sqlite_online_backup(src, local_backup)

        integrity = _sqlite_integrity(local_backup)
        if integrity != "ok":
            raise RuntimeError(f"backup integrity_check failed: {integrity}")

        size = local_backup.stat().st_size
        sha = sha256_file(local_backup)

        object_metadata = {
            "sha256": sha,
            "integrity_check": "ok",
            "source_name": src.name,
        }
        for k, v in (metadata or {}).items():
            key_name = str(k).strip().lower().replace("_", "-")
            value = str(v).strip()
            if key_name and value:
                object_metadata[key_name] = value

        s3 = _make_s3_client()
        s3.upload_file(
            str(local_backup),
            bucket,
            key,
            ExtraArgs={"Metadata": object_metadata},
        )

        head = s3.head_object(Bucket=bucket, Key=key)
        if int(head.get("ContentLength", -1)) != size:
            raise RuntimeError("backup object size verification failed")
        remote_meta = head.get("Metadata") or {}
        if remote_meta.get("sha256") != sha:
            raise RuntimeError("backup object metadata SHA-256 verification failed")

        body = s3.get_object(Bucket=bucket, Key=key)["Body"]
        remote_sha = _stream_sha256(body)
        if remote_sha != sha:
            raise RuntimeError(
                f"backup object round-trip SHA-256 mismatch: local={sha} remote={remote_sha}"
            )

        return {
            "bucket": bucket,
            "key": key,
            "uri": f"s3://{bucket}/{key}",
            "size_bytes": size,
            "sha256": sha,
            "integrity_check": integrity,
            "roundtrip_sha256": remote_sha,
        }
