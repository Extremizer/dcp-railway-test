# OEMixiBOT Railway Storage Audit / Migration / Rollback

Status: rehearsal-ready only. Real Windows source DBs were not touched.

## Storage discovered in code

### 1. Main dealer state

Environment:

`OEMIXIBOT_DB_PATH`

Railway target:

`/data/oemixibot.db`

Created/used by `OemixiStore` and `FinanceEngine`.

Core tables include:

- `dealers`
- `dealer_orders`
- `item_slices`
- `item_status_history`
- `supplier_trackings`
- `usa_shipments`
- `shipment_items`
- `dealer_arrivals`
- `arrival_items`
- `oem_physical`
- `oem_physical_history`
- `receiving_discrepancies`
- `dealer_shipments`
- `dealer_shipment_items`
- `item_delays`
- `item_financial_events`
- `dealer_carts`
- `dealer_cart_items`
- `finance_ledger`

This DB is the migration unit for balances, carts, orders, statuses, supplier/tracking and dealer shipment state.

### 2. Confirmed OEM identity

Environment:

`OEMIXIBOT_IDENTITY_DB`

Railway target:

`/data/oemixibot_identity.db`

Contains confirmed OEM → manufacturer/item type provenance.

It must be moved or the bot loses learned confirmed identities.

### 3. OEM reference dataset

Environment:

`EXTREMIZER_OEM_REFERENCE_DB_FILE`

Railway target:

`/data/oem_reference.db`

This remains separate from dealer transaction data.

### 4. Server coordination data

Not part of OEMixi migration.

Examples:

- `dealer_price_cache`
- DCP live request queue/claims
- shared orders/catalog/warehouse server data

These remain with the authoritative server/coordination service.

A separate Railway OEMixi service must access them through server APIs, not by assuming a shared `/data`.

## Windows source-path status

Exact currently installed Windows DB paths were not read in Stage Railway development because use of the owner's computer is prohibited.

Therefore real cutover requires a read-only preflight that records the actual configured paths for:

- `OEMIXIBOT_DB_PATH`
- `OEMIXIBOT_IDENTITY_DB`
- active OEM reference DB

No path should be guessed.

## WAL / unfinished transactions

Migration helper:

`oemixibot_railway_migration.py`

uses SQLite Online Backup, opening source DB read-only.

Why:

- committed WAL content visible to SQLite is included consistently;
- uncommitted rows are not copied;
- raw copying only the main `.db` file is not used.

Audit records:

- current journal mode;
- whether `-wal` exists;
- whether `-shm` exists;
- table list;
- per-table row counts;
- finance ledger SHA-256;
- calculated dealer balances.

Validation performs:

- `PRAGMA integrity_check`;
- `PRAGMA foreign_key_check`.

## Rehearsal flow

`backup_bundle(...)` creates exactly:

- `oemixibot.db`
- `oemixibot_identity.db`
- `oem_reference.db`

in a clean destination directory.

Existing destination files are refused.

Main DB validation additionally requires exact equality of:

- table set;
- row counts;
- finance ledger digest;
- dealer balance map.

## Data invariants

Migration must not change:

- dealer IDs / Telegram IDs;
- wallet balances;
- finance ledger ordering/content;
- idempotency keys;
- dealer order IDs;
- line commercial snapshots;
- cart OEM/quantity/prices;
- status history;
- tracking IDs/numbers;
- shipment membership;
- arrival state;
- dealer shipment/receipt history.

## Real cutover plan — OWNER GO required

### Phase 0: preflight

1. Confirm exact Windows source DB paths.
2. Audit all three DBs.
3. Confirm no corruption/FK failures.
4. Record source hashes/logical digests and balance map.
5. Confirm Railway OEMixi service still NOT polling.

### Phase 1: write freeze

Requires separate owner authorization.

1. Prevent new dealer/admin writes.
2. Stop the Windows OEMixi poller.
3. Prove the poller has stopped.
4. Close remaining SQLite writers.
5. Record WAL/journal state.

No Railway Telegram poller starts before this proof.

### Phase 2: snapshot

1. Run SQLite Online Backup for all three DBs.
2. Validate destination.
3. Upload/restore into dedicated Railway OEMixi volume.
4. Re-run integrity/FK/table/ledger/balance checks.

### Phase 3: pre-poll smoke

Before Telegram polling:

- open copied DB from Railway runtime;
- verify dealer count;
- verify selected order/cart/history reads;
- verify balance map;
- verify identity/reference DBs;
- verify Shared Gateway connectivity;
- verify approved TTL config.

### Phase 4: singleton cutover

1. Confirm Windows is stopped.
2. Confirm Railway service replica count = 1.
3. Start one Railway worker.
4. Verify singleton lock.
5. Verify `/ready`.
6. Run admin/dealer informational smoke.
7. Observe first real polling cycle.

### Phase 5: enable normal operation

Only after Release Control accepts Phase 4.

## Rollback plan

The Windows source DBs remain unchanged by migration tooling.

If Railway fails **before accepting new writes**:

1. stop Railway worker;
2. verify it is stopped;
3. restart the prior Windows runtime against unchanged source DBs.

If Railway has already accepted dealer/cart/order/finance writes:

**do not simply restart Windows against the old snapshot.**

That would lose or duplicate post-cutover state.

A reverse reconciliation/snapshot is required first.

Therefore Release Control should keep the initial Railway smoke read-only/maintenance-scoped until basic integrity and singleton checks pass.

## Rehearsal proof

Disposable tests cover:

- WAL-mode source;
- committed WAL row copied;
- uncommitted row not copied;
- ledger digest equality;
- dealer balance equality;
- order persistence;
- cart persistence;
- status history;
- tracking state;
- reopen/restart from destination DB;
- source DB remains usable.
