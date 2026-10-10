from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from botocore.exceptions import ClientError
from fastapi import UploadFile, HTTPException


class FakeS3:
    def __init__(self):
        self.objects: dict[tuple[str, str], bytes] = {}
        self.metadata: dict[tuple[str, str], dict[str, str]] = {}
        self.content_types: dict[tuple[str, str], str | None] = {}
        self.modified: dict[tuple[str, str], datetime] = {}
        self.fail_put = False

    def put_object(self, **kwargs):
        if self.fail_put:
            raise RuntimeError("simulated object-store write failure")
        bucket = kwargs["Bucket"]
        key = kwargs["Key"]
        body = kwargs["Body"]
        if hasattr(body, "read"):
            body = body.read()
        data = bytes(body)
        self.objects[(bucket, key)] = data
        self.metadata[(bucket, key)] = dict(kwargs.get("Metadata") or {})
        self.content_types[(bucket, key)] = kwargs.get("ContentType")
        self.modified[(bucket, key)] = datetime.now(timezone.utc)
        return {"ETag": '"fake"'}

    def upload_file(self, filename, bucket, key, ExtraArgs=None):
        if self.fail_put:
            raise RuntimeError("simulated object-store write failure")
        data = Path(filename).read_bytes()
        extra = dict(ExtraArgs or {})
        self.objects[(bucket, key)] = data
        self.metadata[(bucket, key)] = dict(extra.get("Metadata") or {})
        self.content_types[(bucket, key)] = extra.get("ContentType")
        self.modified[(bucket, key)] = datetime.now(timezone.utc)

    def head_object(self, *, Bucket, Key):
        pair = (Bucket, Key)
        if pair not in self.objects:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "Not Found"}},
                "HeadObject",
            )
        return {
            "ContentLength": len(self.objects[pair]),
            "Metadata": dict(self.metadata[pair]),
            "ContentType": self.content_types.get(pair),
        }

    def get_object(self, *, Bucket, Key):
        pair = (Bucket, Key)
        if pair not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "Not Found"}},
                "GetObject",
            )
        return {"Body": io.BytesIO(self.objects[pair])}

    def list_objects_v2(self, **kwargs):
        bucket = kwargs["Bucket"]
        prefix = str(kwargs.get("Prefix") or "")
        max_keys = int(kwargs.get("MaxKeys") or 1000)
        rows = []
        for (b, key), data in self.objects.items():
            if b != bucket or not key.startswith(prefix):
                continue
            rows.append(
                {
                    "Key": key,
                    "Size": len(data),
                    "LastModified": self.modified[(b, key)],
                }
            )
        rows.sort(key=lambda x: x["LastModified"], reverse=True)
        return {
            "Contents": rows[:max_keys],
            "IsTruncated": False,
            "KeyCount": min(len(rows), max_keys),
        }


class CapturingWorker:
    def __init__(self):
        self.calls: list[tuple[object, tuple[object, ...]]] = []

    def submit(self, fn, *args):
        self.calls.append((fn, args))
        return None


def _assert_no_forbidden_dirs(root: Path) -> None:
    for name in ("import_sources", "import_reports", "import_jobs", "vk_backups"):
        assert not (root / name).exists(), f"forbidden persistent dir created: {name}"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="storage-stage2-regression-") as td:
        root = Path(td)
        data_root = root / "data"
        data_root.mkdir()
        orders_db = root / "orders.db"
        tmp_root = root / "tmp"
        tmp_root.mkdir()

        os.environ["RAILWAY_VOLUME_MOUNT_PATH"] = str(data_root)
        os.environ["EXTREMIZER_ORDERS_DB_FILE"] = str(orders_db)
        os.environ["EXTREMIZER_BACKUP_S3_ENDPOINT"] = "https://example.invalid"
        os.environ["EXTREMIZER_BACKUP_S3_BUCKET"] = "stage2-test-bucket"
        os.environ["EXTREMIZER_BACKUP_S3_REGION"] = "auto"
        os.environ["EXTREMIZER_BACKUP_S3_ACCESS_KEY_ID"] = "test-access"
        os.environ["EXTREMIZER_BACKUP_S3_SECRET_ACCESS_KEY"] = "test-secret"
        os.environ["EXTREMIZER_BACKUP_S3_ADDRESSING_STYLE"] = "virtual"

        import backup_object_store as bos
        import oem_import_maintenance as oim
        import web_app as wa

        fake = FakeS3()
        bucket = "stage2-test-bucket"
        worker = CapturingWorker()

        oim.TMP_DIR = tmp_root / "oem-import"
        oim.TMP_DIR.mkdir(parents=True, exist_ok=True)
        oim.DB_PATH = data_root / "oem_reference.db"
        oim.ARTIFACT_PREFIX = "runtime-artifacts/oem-import"
        oim._WORKER = worker
        oim._require_admin = lambda token: None

        with patch.object(bos, "_make_s3_client", return_value=fake):
            # 1. Generic bytes writer/read: size + metadata SHA + full round trip.
            payload = b'{"proof":"stage2"}'
            proof = bos.put_bytes_verified(
                payload,
                "runtime-artifacts/proof/proof.json",
                metadata={"kind": "regression-proof"},
                content_type="application/json",
            )
            assert proof["size_bytes"] == len(payload)
            assert proof["sha256"] == hashlib.sha256(payload).hexdigest()
            assert proof["roundtrip_sha256"] == proof["sha256"]
            assert bos.read_bytes_verified(proof["key"]) == payload
            print("PASS 1 object put/read verification")

            # 2. Missing object is fail-safe.
            try:
                bos.read_bytes_verified("runtime-artifacts/missing.json")
            except FileNotFoundError:
                pass
            else:
                raise AssertionError("missing object must raise FileNotFoundError")
            print("PASS 2 missing object fail-safe")

            # 3. Corrupted object body is rejected by metadata SHA.
            pair = (bucket, proof["key"])
            original = fake.objects[pair]
            fake.objects[pair] = original + b"x"
            try:
                bos.read_bytes_verified(proof["key"])
            except RuntimeError as exc:
                assert "SHA-256 mismatch" in str(exc)
            else:
                raise AssertionError("corrupted object must be rejected")
            fake.objects[pair] = original
            print("PASS 3 corrupted SHA fail-safe")

            # 4. OEM source upload writes source + job only to object storage.
            source_bytes = b"stage2-xlsx-placeholder"
            upload = UploadFile(file=io.BytesIO(source_bytes), filename="fixture.xlsx")
            result = asyncio.run(
                oim.upload_source(
                    profile_key="YAMAHA_DEALER_2021",
                    file=upload,
                    x_extremizer_admin_token="test",
                )
            )
            job_id = result["job"]["job_id"]
            source_key = oim._source_key(job_id, "fixture.xlsx")
            job_key = oim._job_key(job_id)
            assert fake.objects[(bucket, source_key)] == source_bytes
            saved_job = json.loads(fake.objects[(bucket, job_key)].decode("utf-8"))
            assert saved_job["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
            assert saved_job["source_object_key"] == source_key
            assert len(worker.calls) == 1
            _assert_no_forbidden_dirs(data_root)
            print("PASS 4 OEM upload -> object storage only")

            # 5. Dry-run materializes source under /tmp only and stores report/job remotely.
            real_build = oim._build_dry_run

            def fake_build(source_path: Path, profile_key: str):
                assert source_path.read_bytes() == source_bytes
                assert str(source_path).startswith(str(oim.TMP_DIR))
                return {
                    "profile_key": profile_key,
                    "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "apply_allowed": True,
                    "generated_at": oim._utc_now(),
                }

            oim._build_dry_run = fake_build
            oim._run_dry_job(job_id)
            dry_key = oim._report_key(job_id, "dryrun")
            assert (bucket, dry_key) in fake.objects
            dry_job = oim._load_job(job_id)
            assert dry_job["status"] == "dry_run_complete"
            assert dry_job["dry_run_report_key"] == dry_key
            assert list(oim.TMP_DIR.iterdir()) == []
            _assert_no_forbidden_dirs(data_root)
            print("PASS 5 dry-run source temp-only + remote report/job")

            # 6. Apply reads remote dry-run, materializes source temp-only, saves remote report.
            real_apply = oim._apply

            def fake_apply(job, dry, source_path: Path):
                assert source_path.read_bytes() == source_bytes
                assert str(source_path).startswith(str(oim.TMP_DIR))
                assert dry["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
                return {
                    "source_sha256": dry["source_sha256"],
                    "completed_at": oim._utc_now(),
                    "proof": "apply-isolated",
                }

            oim._apply = fake_apply
            oim._run_apply_job(job_id)
            apply_key = oim._report_key(job_id, "apply")
            assert (bucket, apply_key) in fake.objects
            applied_job = oim._load_job(job_id)
            assert applied_job["status"] == "applied"
            assert applied_job["apply_report_key"] == apply_key
            assert list(oim.TMP_DIR.iterdir()) == []
            _assert_no_forbidden_dirs(data_root)
            print("PASS 6 apply temp-only + remote report/job")

            # 7. Job list/get are backed by object storage.
            jobs = oim.list_jobs(x_extremizer_admin_token="test")["jobs"]
            assert any(j["job_id"] == job_id for j in jobs)
            detail = oim.get_job(job_id, x_extremizer_admin_token="test")
            assert detail["job"]["status"] == "applied"
            assert detail["dry_run"]["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
            assert detail["apply"]["proof"] == "apply-isolated"
            print("PASS 7 jobs list/get from object storage")

            oim._build_dry_run = real_build
            oim._apply = real_apply

            # 8. Storage write failure blocks upload before any job is queued.
            fake.fail_put = True
            failed_upload = UploadFile(file=io.BytesIO(b"blocked"), filename="blocked.xlsx")
            before_calls = len(worker.calls)
            before_keys = set(fake.objects)
            try:
                asyncio.run(
                    oim.upload_source(
                        profile_key="YAMAHA_DEALER_2021",
                        file=failed_upload,
                        x_extremizer_admin_token="test",
                    )
                )
            except RuntimeError as exc:
                assert "simulated object-store write failure" in str(exc)
            else:
                raise AssertionError("storage failure must block OEM upload")
            assert len(worker.calls) == before_calls
            assert set(fake.objects) == before_keys
            fake.fail_put = False
            print("PASS 8 storage failure blocks OEM workflow")

            # 9. VK backup writer/download use object storage, not /data/vk_backups.
            def fake_vk_call(method: str, token: str, params: dict):
                assert method == "wall.get"
                assert token == "vk-test"
                return {"count": 1, "items": [{"id": 101, "text": "stage2"}]}

            real_vk_call = wa._vk_call
            wa._vk_call = fake_vk_call
            vk = wa.vk_maintenance_backup(wa.VKBackupRequest(access_token="vk-test"))
            vk_key = f"runtime-artifacts/vk-backups/vk_extremizer_wall_backup_{vk['backup_id']}.json"
            assert (bucket, vk_key) in fake.objects
            response = wa.vk_maintenance_backup_download(vk["backup_id"])
            assert response.body == fake.objects[(bucket, vk_key)]
            assert hashlib.sha256(response.body).hexdigest() == vk["sha256"]
            assert "attachment;" in response.headers["content-disposition"]
            _assert_no_forbidden_dirs(data_root)
            wa._vk_call = real_vk_call
            print("PASS 9 VK backup save/download -> object storage only")

            # 10. VK missing object maps to 404.
            try:
                wa.vk_maintenance_backup_download("0" * 32)
            except HTTPException as exc:
                assert exc.status_code == 404
            else:
                raise AssertionError("missing VK backup must return 404")
            print("PASS 10 VK missing object -> 404")

        _assert_no_forbidden_dirs(data_root)

        oem_text = Path("oem_import_maintenance.py").read_text(encoding="utf-8")
        web_text = Path("web_app.py").read_text(encoding="utf-8")
        for forbidden in (
            '/data/import_sources',
            '/data/import_reports',
            '/data/import_jobs',
            '/data/vk_backups',
            'SOURCE_DIR',
            'REPORT_DIR',
            'JOB_DIR',
            'VK_BACKUP_DIR',
        ):
            assert forbidden not in oem_text + web_text, forbidden

        print("PASS 11 no forbidden persistent file-writer references")
        print("STORAGE_STAGE2_FILE_WRITERS_ISOLATED_REGRESSION PASS")


if __name__ == "__main__":
    main()
