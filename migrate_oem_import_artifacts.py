"""Explicit copy/verify migration of legacy OEM artifacts; no DB writes/deletes.

Default invocation only inventories local files. Stop OEM uploads/Apply workers
before planning and keep them stopped until release verification. An approved
plan SHA is required for --apply. This command is never called during startup.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re

import backup_object_store as store

ACTIVE_STATUSES = {"queued_dry_run", "dry_running", "queued_apply", "applying"}


def _json_bytes(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def build_plan(data_root: Path, prefix: str) -> dict:
    root = data_root.resolve()
    prefix = store._validated_object_key(prefix)
    entries = {}
    files = {}
    for folder, category in (("import_sources", "sources"),
                             ("import_reports", "reports"), ("import_jobs", "jobs")):
        directory = root / folder
        if directory.is_symlink():
            raise RuntimeError("legacy artifact directory is a symlink")
        if not directory.exists():
            continue
        for path in sorted(directory.iterdir()):
            if path.is_symlink() or not path.is_file():
                raise RuntimeError(f"unexpected legacy artifact: {path}")
            raw = path.read_bytes()
            key = f"{prefix}/{category}/{path.name}"
            files[path] = raw
            entries[key] = {"file": str(path), "key": key, "kind": category,
                            "local_sha256": _sha(raw), "sha256": _sha(raw),
                            "size_bytes": len(raw)}

    jobs = []
    referenced = set()
    for path, raw in files.items():
        if path.parent.name != "import_jobs":
            continue
        if path.suffix != ".json":
            raise RuntimeError(f"unexpected job filename: {path.name}")
        job = json.loads(raw.decode("utf-8"))
        job_id = str(job.get("job_id") or "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", job_id) or path.stem != job_id:
            raise RuntimeError("invalid legacy job identity")
        if job.get("status") in ACTIVE_STATUSES:
            raise RuntimeError(f"active legacy job must settle before migration: {job_id}")
        source = Path(str(job.get("source_path") or "")).resolve()
        if source.parent != root / "import_sources" or source not in files:
            raise RuntimeError(f"legacy source missing or outside inventory: {job_id}")
        if _sha(files[source]) != job.get("source_sha256"):
            raise RuntimeError(f"legacy source SHA mismatch: {job_id}")
        if len(files[source]) != job.get("source_size_bytes"):
            raise RuntimeError(f"legacy source size mismatch: {job_id}")
        converted = dict(job)
        converted.pop("source_path", None)
        converted["source_object_key"] = f"{prefix}/sources/{source.name}"
        converted["source_size_bytes"] = len(files[source])
        referenced.add(source)
        for mode, old_field, new_field in (("dryrun", "dry_run_report", "dry_run_report_key"),
                                           ("apply", "apply_report", "apply_report_key")):
            report = root / "import_reports" / f"{job_id}_{mode}.json"
            old_path = job.get(old_field)
            if old_path and Path(str(old_path)).resolve() != report:
                raise RuntimeError(f"unexpected legacy report path: {job_id}")
            required = bool(old_path) or (mode == "dryrun" and job.get("status") == "dry_run_complete") \
                or (mode == "apply" and job.get("status") == "applied")
            if required and report not in files:
                raise RuntimeError(f"legacy report missing: {job_id}/{mode}")
            converted.pop(old_field, None)
            if report in files:
                value = json.loads(files[report].decode("utf-8"))
                if value.get("source_sha256") != job["source_sha256"]:
                    raise RuntimeError(f"legacy report/source SHA mismatch: {job_id}/{mode}")
                converted[new_field] = f"{prefix}/reports/{report.name}"
                referenced.add(report)
        key = f"{prefix}/jobs/{path.name}"
        payload = _json_bytes(converted)
        entries[key].update(payload=converted, sha256=_sha(payload), size_bytes=len(payload))
        jobs.append({"job_id": job_id, "status": job.get("status")})
        referenced.add(path)
    # Sources/reports with no job are copied too and exposed in the inventory.
    plan = {"data_root": str(root), "prefix": prefix, "jobs": jobs,
            "unreferenced_files": sorted(str(p) for p in files if p not in referenced),
            "entries": sorted(entries.values(), key=lambda e: (e["kind"] == "jobs", e["key"]))}
    plan["plan_sha256"] = _sha(_json_bytes(plan))
    return plan


def apply_plan(plan: dict, expected_plan_sha: str) -> dict:
    current = build_plan(Path(plan["data_root"]), plan["prefix"])
    if current != plan or current["plan_sha256"] != expected_plan_sha:
        raise RuntimeError("migration plan changed or expected plan SHA mismatch")
    payloads = []
    for entry in current["entries"]:
        raw = Path(entry["file"]).read_bytes()
        if _sha(raw) != entry["local_sha256"]:
            raise RuntimeError("local artifact changed after plan verification")
        data = _json_bytes(entry["payload"]) if "payload" in entry else raw
        try:
            remote = store.read_bytes_verified(entry["key"])
        except FileNotFoundError:
            remote = None
        if remote is not None and remote != data:
            raise RuntimeError(f"destination conflict; refusing overwrite: {entry['key']}")
        payloads.append((entry, data, remote is None))
    # Preflight every destination before any write. Publish jobs only after all
    # referenced sources/reports have passed round-trip verification.
    for entry, data, missing in payloads:
        if missing:
            store.put_bytes_verified(data, entry["key"], metadata={"kind": "oem-import-migration"})
        if store.read_bytes_verified(entry["key"]) != data:
            raise RuntimeError("migration destination verification failed")
    return {"plan_sha256": expected_plan_sha, "verified_objects": len(payloads),
            "jobs": current["jobs"], "local_files_deleted": 0}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path(os.getenv("RAILWAY_VOLUME_MOUNT_PATH", "/data")))
    parser.add_argument("--prefix", default=os.getenv("OEM_IMPORT_OBJECT_PREFIX", "runtime-artifacts/oem-import"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-sha")
    args = parser.parse_args()
    if args.apply and not args.expected_plan_sha:
        parser.error("--apply requires --expected-plan-sha from the reviewed inventory")
    plan = build_plan(args.data_root, args.prefix)
    result = apply_plan(plan, args.expected_plan_sha) if args.apply else plan
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
