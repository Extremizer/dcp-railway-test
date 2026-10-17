# EXTREMIZER BACKUP TRANSPORT — FINAL AUDIT 2026-10-17

## Root causes
1. Windows System OpenSSH (C:\Windows\System32\OpenSSH\ssh.exe) returned exit 255 and no useful diagnostic output.
2. railway_snapshot_launcher.py hardcoded /mise/installs/python/3.13.15/bin/python3.13; current Railway runtime is Python 3.13.16.

## Fix
1. RUN_EXTREMIZER_BACKUP_AUTO.cmd prepends C:\Program Files\Git\usr\bin to the process-local PATH only.
   - Git OpenSSH verified: OpenSSH_10.3p1.
   - No global Windows PATH change.
   - No Railway SSH key registration change.
2. railway_snapshot_launcher.py now uses /mise/installs/python/latest/bin/python.
   - Current target resolves to Python 3.13.16.
   - Avoids patch-version hardcoding.

## Manual repaired run
- snapshot: 20261017T012949Z
- DAILY: DAILY_20261017_013523
- run_id: ebc1e6761840410b8863377d4ee6e386
- snapshot_verification: PASS
- journal final_status: SUCCESS
- restore: PASS
- production_touched: false

## Forced Task Scheduler proof
- task: Extremizer Backup AUTO
- LastTaskResult: 0
- snapshot: 20261017T013845Z
- DAILY: DAILY_20261017_014702
- run_id: 073164cbb7eb446e870649486d56cf05
- snapshot_verification: PASS
- journal final_status: SUCCESS
- verification status: VERIFIED
- restore status: PASS
- production_touched: false

DB SHA-256:
- extremizer_orders.db: 507dbec5f3e9433dedaaff2660330507b9df8c81cd92cecee87edaf03968f9d2
- oem_reference.db: fb271b697140408ffc6cf53936117d5edaa00acb2a5788749d81249426381ace

## Final health
backup_auto_health.py:
- railway_snapshot: PASS
- databases: 2/2
- daily: VERIFIED
- journal: SUCCESS
- task_scheduler: SUCCESS
- overall: SUCCESS

## Mandatory release gate
Before every real Railway deploy or write-capable migration:
1. run the approved backup pipeline;
2. require a fresh snapshot from the current production state;
3. require final_status=SUCCESS;
4. require DAILY status=VERIFIED;
5. require restore test PASS;
6. require production_touched=false during restore verification;
7. if any item fails: NO DEPLOY.

This gate is fail-closed.

No Railway service redeploy/restart was required to repair backup transport.
