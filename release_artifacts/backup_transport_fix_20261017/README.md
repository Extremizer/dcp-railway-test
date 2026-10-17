# EXTREMIZER Backup Transport Fix — 2026-10-17

## Root cause

The approved backup launcher used `railway ssh`, which ultimately depended on the Windows system OpenSSH client:

`C:\Windows\System32\OpenSSH\ssh.exe`

That client was locally broken:

- `ssh.exe -V` -> exit 255
- stdout/stderr empty
- TCP to `ssh.railway.com:22` remained reachable

Git for Windows OpenSSH was already installed and healthy:

`C:\Program Files\Git\usr\bin\ssh.exe`

Validation:

- OpenSSH 10.3p1
- direct Railway host alias probe -> PASS

## Change

Only the remote-exec/file-transfer transport inside `railway_snapshot_launcher.py` changed.

Before:

`railway ssh -- sh -lc <remote command>`

After:

`C:\Program Files\Git\usr\bin\ssh.exe -o BatchMode=yes railway-dcp-railway-test <remote command>`

Backup business logic, SQLite online backup, manifests, SHA validation, integrity checks,
DAILY creation, restore verification, retention and scheduler policy were not changed.

## Isolated transport E2E

Snapshot:

`20261017T014035Z`

Results:

- extremizer_orders.db: 95,084,544 bytes, integrity_check=ok
- oem_reference.db: 785,272,832 bytes, integrity_check=ok
- launcher exit=0
- remote cleanup PASS

## Full isolated AUTO E2E on installed canonical launcher

Snapshot:

`20261017T014849Z`

DAILY:

`DAILY_20261017_015248`

Journal:

- final_status=SUCCESS
- snapshot_verification=PASS

Verification:

- DAILY status=VERIFIED
- restore_test.status=PASS
- production_touched=false

## Forced Task Scheduler acceptance

Task:

`Extremizer Backup AUTO`

Forced run:

- LastRunTime: 2026-10-17 03:54:16 local
- LastTaskResult: 0
- final State: Ready
- lock cleared

Observed unattended transport:

`C:\Program Files\Git\usr\bin\ssh.exe -o BatchMode=yes railway-dcp-railway-test ...`

Railway snapshot:

`20261017T015514Z`

DAILY:

`DAILY_20261017_020145`

Final journal:

`run_id=b952c638c8114eb5ba85d3dcda6c746e`

- snapshot_verification=PASS
- final_status=SUCCESS
- error=null

Final verification:

- status=VERIFIED
- restore_test.status=PASS
- production_touched=false
- databases/extremizer_orders.db: size/SHA/integrity PASS
- databases/oem_reference.db: size/SHA/integrity PASS

## Release gate

Required backup gate is restored:

`SUCCESS -> VERIFIED -> restore PASS`

This gate is mandatory before the next real Railway deployment.

## Recovery

- `railway_snapshot_launcher.before.py` — pre-fix launcher
- `railway_snapshot_launcher.fixed.py` — accepted fixed launcher
- `transport_fix.diff` — exact transport-only diff
