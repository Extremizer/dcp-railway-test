"""Real XLSX parsing, OEM SQLite Apply/backup and legacy migration.

Only the object-store transport and worker scheduling are replaced. No live
credentials, databases, network calls or production Apply are used.
"""
import asyncio
import hashlib
import io
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from openpyxl import Workbook

import backup_object_store as store
from _storage_stage2_writers_regression import FakeS3, CapturingWorker
import migrate_oem_import_artifacts as migration


def create_reference_fixture(path):
    columns = """item_type TEXT, actual_weight_kg REAL, volume_weight_kg REAL,
        weight_state TEXT, direct_observation_count INTEGER, linked_group_count INTEGER,
        needs_review INTEGER, type_conflict INTEGER, actual_weight_conflict INTEGER,
        volume_weight_conflict INTEGER, canonical_name TEXT, manufacturer TEXT,
        name_state TEXT, manufacturer_state TEXT, name_source_count INTEGER,
        manufacturer_source_count INTEGER"""
    with sqlite3.connect(path) as conn:
        conn.executescript(f"""
            CREATE TABLE oem_reference(oem TEXT PRIMARY KEY, {columns});
            CREATE TABLE oem_reference_meta(key TEXT PRIMARY KEY,value TEXT);
            INSERT INTO oem_reference_meta VALUES('schema_version','OEM_REFERENCE_V2');
            CREATE TABLE reference_sources(source_key TEXT PRIMARY KEY,source_name TEXT,
                manufacturer TEXT,source_year INTEGER,source_kind TEXT,trust_level TEXT,
                source_sha256 TEXT,notes TEXT);
            CREATE TABLE oem_relations(from_oem TEXT,to_oem TEXT,relation_type TEXT,
                relation_state TEXT,source_count INTEGER,needs_review INTEGER,pre_v2_known INTEGER,
                UNIQUE(from_oem,to_oem,relation_type));
            CREATE TABLE oem_fact_observations(oem TEXT,fact_type TEXT,value_text TEXT,
                source_key TEXT,trust_level TEXT,status TEXT,
                UNIQUE(oem,fact_type,value_text,source_key));
            CREATE TABLE oem_relation_observations(from_oem TEXT,to_oem TEXT,relation_type TEXT,
                source_key TEXT,trust_level TEXT,status TEXT,direction_verified INTEGER,
                UNIQUE(from_oem,to_oem,relation_type,source_key));
        """)


class StorageStage2IntegrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="stage2-real-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.data = self.root / "data"
        self.data.mkdir()
        self.tmp = self.root / "tmp"
        self.tmp.mkdir()
        env = {
            "RAILWAY_VOLUME_MOUNT_PATH": str(self.data), "OEM_IMPORT_TMP_DIR": str(self.tmp),
            "EXTREMIZER_BACKUP_TMP_ROOT": str(self.tmp),
            "EXTREMIZER_WEB_ADMIN_TOKEN": "isolated-admin",
            "EXTREMIZER_BACKUP_S3_BUCKET": "isolated-bucket",
        }
        p = patch.dict(os.environ, env, clear=True)
        p.start(); self.addCleanup(p.stop)
        import oem_import_maintenance as oim
        self.oim = oim
        self.fake = FakeS3()
        self.worker = CapturingWorker()
        for name, value in (("DATA_DIR", self.data), ("DB_PATH", self.data / "reference.db"),
                            ("TMP_DIR", self.tmp), ("_WORKER", self.worker),
                            ("ARTIFACT_PREFIX", "isolated/oem-import")):
            p = patch.object(oim, name, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(store, "_make_s3_client", return_value=self.fake)
        p.start(); self.addCleanup(p.stop)
        create_reference_fixture(oim.DB_PATH)

    def upload_and_dry_run(self):
        book = Workbook()
        book.active.append(["PART NUMBER"])
        book.active.append(["TEST-100"])
        output = io.BytesIO(); book.save(output); book.close()
        result = asyncio.run(self.oim.upload_source(
            profile_key="YAMAHA_DEALER_2021",
            file=UploadFile(file=io.BytesIO(output.getvalue()), filename="real.xlsx"),
            x_extremizer_admin_token="isolated-admin"))
        fn, args = self.worker.calls.pop(); fn(*args)
        job_id = result["job"]["job_id"]
        self.assertEqual(self.oim._load_job(job_id)["status"], "dry_run_complete")
        return job_id

    def run_apply(self, job_id):
        self.oim.apply_job(job_id, self.oim.ApplyRequest(confirmation="APPLY " + job_id),
                           x_extremizer_admin_token="isolated-admin")
        fn, args = self.worker.calls.pop(); fn(*args)

    def test_real_upload_dry_run_apply_backup_and_job_reads(self):
        job_id = self.upload_and_dry_run()
        with self.assertRaises(HTTPException) as failure:
            self.oim.apply_job(job_id, self.oim.ApplyRequest(confirmation="wrong"),
                               x_extremizer_admin_token="isolated-admin")
        self.assertEqual(failure.exception.status_code, 400)
        self.assertEqual(self.worker.calls, [])
        self.run_apply(job_id)
        detail = self.oim.get_job(job_id, x_extremizer_admin_token="isolated-admin")
        self.assertEqual(detail["job"]["status"], "applied")
        self.assertFalse(detail["apply"]["prices_read_or_saved"])
        self.assertEqual(detail["apply"]["inserted_total_oems"], 1)
        self.assertTrue(detail["apply"]["backup_path"].startswith("s3://"))
        with sqlite3.connect(self.oim.DB_PATH) as conn:
            self.assertEqual(conn.execute("SELECT oem,manufacturer FROM oem_reference").fetchall(),
                             [("TEST-100", "YAMAHA")])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(len(self.oim.list_jobs(x_extremizer_admin_token="isolated-admin")["jobs"]), 1)
        self.assertEqual(list(self.tmp.iterdir()), [])
        for name in ("import_sources", "import_reports", "import_jobs"):
            self.assertFalse((self.data / name).exists())

    def test_backup_failure_blocks_real_apply_before_db_mutation(self):
        job_id = self.upload_and_dry_run()
        before = self.oim.DB_PATH.read_bytes()
        with patch.object(store, "backup_sqlite_to_bucket", side_effect=RuntimeError("backup failed")):
            self.run_apply(job_id)
        self.assertEqual(self.oim._load_job(job_id)["status"], "failed")
        self.assertEqual(self.oim.DB_PATH.read_bytes(), before)
        self.assertEqual(list(self.tmp.iterdir()), [])

    def legacy_fixture(self, status="dry_run_complete"):
        for folder in ("import_sources", "import_reports", "import_jobs"):
            (self.data / folder).mkdir()
        job_id = "yamaha-legacy"
        source = self.data / "import_sources" / (job_id + "__old.xlsx")
        source.write_bytes(b"legacy-source")
        sha = hashlib.sha256(source.read_bytes()).hexdigest()
        report = self.data / "import_reports" / (job_id + "_dryrun.json")
        report.write_text(json.dumps({"source_sha256": sha, "apply_allowed": True}))
        job = {"job_id": job_id, "status": status, "profile_key": "YAMAHA_DEALER_2021",
               "source_path": str(source), "source_sha256": sha, "source_size_bytes": source.stat().st_size,
               "original_filename": "old.xlsx", "dry_run_report": str(report)}
        path = self.data / "import_jobs" / (job_id + ".json")
        path.write_text(json.dumps(job))
        return job_id, path, source

    def test_legacy_copy_verified_idempotent_and_readable_by_new_writers(self):
        job_id, _, _ = self.legacy_fixture()
        before = {str(p): p.read_bytes() for p in self.data.rglob("*") if p.is_file()}
        plan = migration.build_plan(self.data, self.oim.ARTIFACT_PREFIX)
        self.assertEqual(self.fake.objects, {})  # Inventory makes no S3 calls/writes.
        first = migration.apply_plan(plan, plan["plan_sha256"])
        self.assertEqual(first["verified_objects"], 3)
        self.assertEqual(first["local_files_deleted"], 0)
        keys = set(self.fake.objects)
        self.assertEqual(migration.apply_plan(plan, plan["plan_sha256"]), first)
        self.assertEqual(set(self.fake.objects), keys)
        detail = self.oim.get_job(job_id, x_extremizer_admin_token="isolated-admin")
        self.assertEqual(detail["job"]["status"], "dry_run_complete")
        self.assertTrue(detail["dry_run"]["apply_allowed"])
        with self.oim._materialized_source(self.oim._load_job(job_id)) as source:
            self.assertEqual(source.read_bytes(), b"legacy-source")
        self.assertEqual(before, {str(p): p.read_bytes() for p in self.data.rglob("*") if p.is_file()})

    def test_changed_plan_and_remote_conflict_block_all_writes(self):
        _, _, source = self.legacy_fixture()
        plan = migration.build_plan(self.data, self.oim.ARTIFACT_PREFIX)
        with self.assertRaisesRegex(RuntimeError, "expected plan SHA"):
            migration.apply_plan(plan, "wrong")
        entry = plan["entries"][-1]
        store.put_bytes_verified(b"different", entry["key"])
        before = dict(self.fake.objects)
        with self.assertRaisesRegex(RuntimeError, "destination conflict"):
            migration.apply_plan(plan, plan["plan_sha256"])
        self.assertEqual(self.fake.objects, before)
        source.write_bytes(b"changed")
        with self.assertRaisesRegex(RuntimeError, "source SHA mismatch"):
            migration.apply_plan(plan, plan["plan_sha256"])
        self.assertEqual(self.fake.objects, before)

    def test_active_legacy_jobs_are_rejected(self):
        self.legacy_fixture(status="queued_apply")
        with self.assertRaisesRegex(RuntimeError, "active legacy job"):
            migration.build_plan(self.data, self.oim.ARTIFACT_PREFIX)
        self.assertEqual(self.fake.objects, {})

    def test_failed_copy_does_not_publish_job_pointer(self):
        job_id, _, _ = self.legacy_fixture()
        plan = migration.build_plan(self.data, self.oim.ARTIFACT_PREFIX)
        self.fake.fail_put = True
        with self.assertRaisesRegex(RuntimeError, "write failure"):
            migration.apply_plan(plan, plan["plan_sha256"])
        self.assertNotIn(("isolated-bucket", self.oim._job_key(job_id)), self.fake.objects)


if __name__ == "__main__":
    unittest.main()
