# EXTREMIZER BACKUP TRANSPORT GATE — 2026-10-17

## Status

BACKUP TRANSPORT — RESTORED

Mandatory pre-Railway-release gate:
**SUCCESS → VERIFIED → restore PASS**

This gate is required before the next real Railway deployment or write-capable migration.

## Root cause

The backup Python engine and Railway key registration were not the root cause.

Observed:
- Windows System OpenSSH:
  - path: C:\Windows\System32\OpenSSH\ssh.exe
  - version: OpenSSH_9.5p2 for Windows / file version 9.5.6.4
  - even `ssh -V` / `ssh -G` returned rc=255
- network to `ssh.railway.com:22` was healthy
- Railway CLI itself was healthy
- Git for Windows OpenSSH:
  - path: C:\Program Files\Git\usr\bin\ssh.exe
  - version: OpenSSH_10.3p1
  - Railway SSH probe returned `BACKUP_TRANSPORT_OK` / rc=0

Therefore the failure was local Windows OpenSSH transport, not Railway production, not SQLite, and not the backup verification engine.

## Fix

Approved backup Python code was left unchanged.

Only the launcher environment was changed:

```cmd
set "PATH=C:\Program Files\Git\usr\bin;%PATH%"
```

This forces Railway CLI subprocesses to use Git OpenSSH instead of the broken Windows System OpenSSH.

Authoritative local launcher SHA-256 after fix:

`115D074F22987964419CD374E8D0E0061E8716BA2E0C491D7714E0D9CC441E42`

Reference copy:
`release_artifacts/backup_transport/RUN_EXTREMIZER_BACKUP_AUTO.cmd`

## Real Scheduled Task proof

Task:
`Extremizer Backup AUTO`

Task action still points to the same approved launcher.

Observed child transport during the real AUTO run:
`C:\Program Files\Git\usr\bin\ssh.exe`

Task result:
- State: Ready
- LastTaskResult: 0
- NextRunTime: 2026-10-18 03:00 local

## Final post-deploy backup gate

Latest accepted snapshot:
`20261017T015514Z`

Latest DAILY checkpoint:
`DAILY_20261017_020145`

Journal:
- run_id: `b952c638c8114eb5ba85d3dcda6c746e`
- snapshot_verification: PASS
- final_status: SUCCESS

Databases:
- `extremizer_orders.db`
  - journal SHA-256: `7ce7c1d0620388ceb77861d9a594c7877cfb48dd54155eeb7808b147a92e7a4c`
- `oem_reference.db`
  - journal SHA-256: `fb271b697140408ffc6cf53936117d5edaa00acb2a5788749d81249426381ace`

Health result:
- railway_snapshot: PASS
- databases: 2/2
- daily: VERIFIED
- journal: SUCCESS
- task_scheduler: SUCCESS
- overall: SUCCESS
- health exit code: 0

Restore verification:
- restore_test.status: PASS
- temporary_environment: true
- production_touched: false
- both active SQLite databases passed size, SHA-256, integrity_check and queryability checks

## Mandatory release rule

Before every future real Railway deployment:

1. Run/confirm `Extremizer Backup AUTO`.
2. Require a snapshot newer than the currently running production deployment when a fresh predeploy backup is required.
3. Require journal `final_status=SUCCESS`.
4. Require DAILY `status=VERIFIED`.
5. Require restore test `status=PASS`.
6. Require Task Scheduler `LastTaskResult=0` or an explicitly successful manual predeploy run.
7. If any item fails: **NO DEPLOY**.

Do not substitute:
- an older backup,
- a snapshot without restore test,
- a journal-only SUCCESS,
- or an unverified file copy.

## Release Control decision

The backup blocker identified during RC1 audit is CLOSED.

This does not authorize an unrelated Railway deployment by itself.
It only restores the mandatory backup gate required before the next Railway release.
