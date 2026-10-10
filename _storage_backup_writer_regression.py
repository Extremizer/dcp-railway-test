from __future__ import annotations

import hashlib
import io
import os
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch

import backup_object_store as bos


class _FakeS3:
    def __init__(self, corrupt_roundtrip: bool = False):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.corrupt_roundtrip = corrupt_roundtrip

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        data = Path(filename).read_bytes()
        self.objects[(bucket, key)] = data
        self.metadata[(bucket, key)] = dict((ExtraArgs or {}).get("Metadata") or {})

    def head_object(self, *, Bucket, Key):
        data = self.objects[(Bucket, Key)]
        return {
            "ContentLength": len(data),
            "Metadata": self.metadata[(Bucket, Key)],
        }

    def get_object(self, *, Bucket, Key):
        data = self.objects[(Bucket, Key)]
        if self.corrupt_roundtrip:
            data = data + b"x"
        return {"Body": io.BytesIO(data)}


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE proof(id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO proof(value) VALUES('storage-stage-1')")
        conn.commit()
    finally:
        conn.close()


def _env(tmp_root: Path) -> dict[str, str]:
    return {
        bos.ENV_ENDPOINT: "https://example.invalid",
        bos.ENV_BUCKET: "extremizer-backups-test",
        bos.ENV_REGION: "auto",
        bos.ENV_ACCESS_KEY: "test-access",
        bos.ENV_SECRET_KEY: "test-secret",
        bos.ENV_ADDRESSING_STYLE: "virtual",
        bos.ENV_TMP_ROOT: str(tmp_root),
    }


def test_verified_copy() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "live.db"
        tmp_root = root / "tmp"
        _make_db(src)
        fake = _FakeS3()

        with patch.dict(os.environ, _env(tmp_root), clear=False), patch.object(
            bos, "_make_s3_client", return_value=fake
        ):
            result = bos.backup_sqlite_to_bucket(
                src,
                "preimport/oem-reference/test.db",
                metadata={"kind": "pre-import", "job_id": "job-1"},
            )

        data = fake.objects[("extremizer-backups-test", "preimport/oem-reference/test.db")]
        remote_sha = hashlib.sha256(data).hexdigest()
        assert result["sha256"] == remote_sha
        assert result["roundtrip_sha256"] == remote_sha
        assert result["integrity_check"] == "ok"
        assert result["size_bytes"] == len(data)
        assert result["uri"].startswith("s3://extremizer-backups-test/")
        assert list(tmp_root.iterdir()) == []

        restored = root / "restored.db"
        restored.write_bytes(data)
        conn = sqlite3.connect(restored)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert conn.execute("SELECT value FROM proof").fetchone()[0] == "storage-stage-1"
        finally:
            conn.close()


def test_roundtrip_corruption_blocks_writer() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        src = root / "live.db"
        tmp_root = root / "tmp"
        _make_db(src)
        fake = _FakeS3(corrupt_roundtrip=True)

        with patch.dict(os.environ, _env(tmp_root), clear=False), patch.object(
            bos, "_make_s3_client", return_value=fake
        ):
            try:
                bos.backup_sqlite_to_bucket(src, "preimport/oem-reference/bad.db")
            except RuntimeError as exc:
                assert "round-trip SHA-256 mismatch" in str(exc)
            else:
                raise AssertionError("corrupted round-trip must block the writer")


def test_oem_import_wiring() -> None:
    text = Path("oem_import_maintenance.py").read_text(encoding="utf-8")
    assert 'DATA_DIR / "backups"' not in text
    assert "BACKUP_DIR" not in text
    assert "backup_object_store.backup_sqlite_to_bucket" in text
    assert "preimport/oem-reference" in text


if __name__ == "__main__":
    test_verified_copy()
    test_roundtrip_corruption_blocks_writer()
    test_oem_import_wiring()
    print("STORAGE_BACKUP_WRITER_REGRESSION PASS")
