# OEMixiBOT Windows Read-Only Preflight — 2026-10-15

Status: completed read-only. Windows poller was not stopped. No production DB was copied, modified, checkpointed, vacuumed, or migrated.

## Active Windows runtime

Launcher:

`C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002\start_oemixibot_private.ps1`

Launcher behavior:

- working directory:
  `C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002`
- sets runtime main DB:
  `OEMIXIBOT_DB_PATH=<working-dir>\oemixibot_private.db`
- starts:
  `python -B oemixibot_app.py`

Observed process tree:

1. PowerShell `start_oemixibot_private.ps1`
2. Windows Python launcher
3. Python 3.14 `oemixibot_app.py`

The two python.exe entries are parent/child launcher/runtime, not two separate OEMixi polling instances.

No matching OEMixi Scheduled Task was found during this read-only check.

## Live database #1 — dealer/business state

Exact active path:

`C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002\oemixibot_private.db`

Observed:

- size: 253,952 bytes
- journal mode: DELETE
- WAL: absent
- SHM: absent
- integrity_check: ok
- foreign_key_check: 0 violations

Selected row counts:

- dealers: 1
- dealer_orders: 2
- item_slices: 3
- item_status_history: 11
- finance_ledger: 4
- dealer_carts: 1
- dealer_cart_items: 0
- supplier_trackings: 1
- usa_shipments: 1
- dealer_arrivals: 1
- dealer_shipments: 1

Finance ledger logical SHA-256 at preflight:

`b418ed57b81c077960a1531da51da79ac93a6a4ea3e9da8d107200631d371665`

This digest is a checkpoint only. It must be recalculated after the real write freeze because the live bot can continue changing data before OWNER GO.

## Live database #2 — learned identity

Exact active path:

`C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002\oemixibot_identity.db`

Reason:

`OEMIXIBOT_IDENTITY_DB` is not set by the launcher, therefore `oem_identity_service.py` uses its same-directory default.

Observed:

- size: 12,288 bytes
- journal mode: DELETE
- WAL: absent
- SHM: absent
- integrity_check: ok
- foreign_key_check: 0 violations
- `oem_identity`: 13 rows

## Live database #3 — OEM reference

Exact active path:

`C:\data\oem_reference.db`

Reason:

- `EXTREMIZER_DATA_DIR` defaults to `/data`;
- on this Windows host `C:\data` exists;
- therefore `oem_reference_service.py` selects its persistent data-dir DB rather than the bundled repo copy.

Observed:

- size: 42,172,416 bytes
- journal mode: DELETE
- WAL: absent
- SHM: absent
- integrity_check: ok
- foreign_key_check: 0 violations
- `oem_reference`: 15,547 rows
- `source_positions`: 45,266 rows
- `sync_positions`: 45,266 rows
- `weight_observations`: 29,581 rows

## Migration source map

The real migration must use exactly:

- main:
  `...\vk_prod_redeploy_20261002\oemixibot_private.db`
- identity:
  `...\vk_prod_redeploy_20261002\oemixibot_identity.db`
- reference:
  `C:\data\oem_reference.db`

Do not use the similarly named historical/disposable DB files found beside the runtime.

## Safe real preflight command

Repository helper:

`oemixibot_windows_preflight.py`

It opens all sources:

- URI `mode=ro`;
- `PRAGMA query_only=ON`;
- no schema creation;
- no WAL checkpoint;
- no VACUUM;
- no copy.

It records:

- path/size;
- journal/WAL/SHM;
- integrity;
- foreign-key violations;
- table list;
- row counts;
- finance ledger digest;
- dealer USD balance map.

## Real migration precondition

The numbers in this document are not the final migration snapshot because Windows polling remains active.

After OWNER GO:

1. maintenance/write freeze;
2. stop Windows runtime;
3. confirm process tree is gone;
4. rerun the read-only preflight;
5. only then create SQLite Online Backup snapshots.
