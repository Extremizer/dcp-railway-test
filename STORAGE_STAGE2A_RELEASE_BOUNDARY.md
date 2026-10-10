# Storage Stage 2A: pre-switch and post-switch evidence

Scope: OEM source/job/report writers only. VK stays in Stage 2B. No DB Apply,
production copy, cleanup or Railway configuration change is performed by this
PR. It remains Draft for Release Control.

## CI evidence

`storage-writers-isolated.yml` runs the existing verified SQLite backup suite,
the original nine OEM writer checks and the integration/migration tests. The
integration suite runs actual XLSX parsing, OEM dry-run, SQLite Apply and backup
on disposable fixtures. Object-store transport and worker scheduling are
replaced; a live bucket is not tested by these checks.

## Legacy history before switching writers

Existing local jobs cannot be read by the new object-only reader until migrated.
Release Control must obtain the current local inventory and ensure OEM uploads
and Apply workers are stopped/quiescent before planning. Active jobs are rejected
by the migration tool. Do not run the tool automatically at application startup.

Planning only, without S3 access or writes:

```sh
python -B migrate_oem_import_artifacts.py --data-root /data
```

Review the complete plan, job statuses, unreferenced artifacts and `plan_sha256`.
If the inventory is empty, record that fact instead of inventing a migration.
If files remain, explicitly execute the reviewed copy before switching writers:

```sh
python -B migrate_oem_import_artifacts.py --data-root /data --apply --expected-plan-sha REVIEWED_SHA
```

The apply command rejects changed plans, missing/corrupt referenced sources and
reports, and conflicting destination objects. It copies unreferenced files too,
verifies full round trips, and publishes converted job pointers after sources
and reports. It can resume an interrupted copy while writers remain stopped.
No local file or DB is deleted or modified. This is a storage copy command,
separate from the business OEM Apply operation.

Complete migration verification and confirm the destination prefix matches
`OEM_IMPORT_OBJECT_PREFIX` before selecting the writer-switch deployment.
Do not migrate into a prefix with concurrent writers.

## Environment and deployment evidence still required

Confirm endpoint/bucket access and full size/metadata/body SHA verification in
an explicitly selected test environment. Synthetic transport tests cannot prove
that live endpoint. No production probe is authorized by this PR alone.

After the controlled deployment: verify released SHA, Online/health, a controlled
upload/dry-run/report retrieval and legacy job/report retrieval. Confirm new
artifacts are in Object Storage and no new durable source/job/report files
appear in the former local directories. Keep all existing local copies until
post-deploy verification passes. Cleanup requires a separate action.
