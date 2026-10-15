# OEMixiBOT Release Control Plan

Status: release preparation only. No merge, deploy, production DB migration, Windows poller stop, or live DCP.

## 1. Accepted dependency chain

Theme 1 accepted chain:

- PR #38 `4469af37b40acfe70a5ec7030acc0f023bdec6d4`
- PR #39 `f19921878d3a3664946f537f702154870a20e9c2`
- PR #40 `868efd70f56ddba70ce79e9e1705d51f1aec6007`
- PR #41 `46199eb5adc88c358240e9ebf28af60682a96a6f`
- PR #42 `ce6927c88689f1a13c382e6909061edc5cdd9659`
- PR #43 `9fc7d69f60d1cba8ae3008c79c983c74402846c7`

PR #43 already contains the Theme 1 chain through #42.

Theme 6 dependencies remain separate until Release Control accepts them:

- PR #32 `2ed283ac68ac1cfbc1252bd9120f98d7d9494fd5`
- PR #33 `3325f30925ddbb662507cc95b6322625098bb90f`
- PR #34 `e8d45e2f46bda0267b8595283befb704786142a9`
- PR #35 `16e3fa34f1e2659aa6c1e6d85b914371253bfe6d`
- PR #36 `2a2d64184465e66ef817f2f6e4dda8b414d3c467`
- PR #37 `5a26ab78ebb8eb7862da7816580a577000d8579d`

All observed exact-head Theme 6 GitHub workflows were SUCCESS at the time of this preparation, but this document does not accept Theme 6 on behalf of Theme 4/Release Control.

## 2. Readiness defect

PR #43 set `state.ready=True` before entering `Application.run_polling()`.

Base regression:

- branch: `oemixibot-release-prep-20261015`
- failing run: GitHub Actions #38775521966
- exact failure: readiness was already true on entry to polling.

Fix:

`start_polling_readiness_monitor()`

Readiness now becomes true only when both are true:

- `application.running`
- `application.updater.running`

Before that `/ready` remains 503.

On shutdown readiness is cleared before health server close.

## 3. Dual poller protection

### Layer A — Railway local singleton

Existing `flock` prevents two polling processes using the same Railway volume.

### Layer B — authoritative server cutover fence

New module:

`oemixibot_poller_fence.py`

Persistent coordination row defaults to:

`active_owner=WINDOWS`

Railway runtime must successfully read the server fence and see:

`active_owner=RAILWAY`

before building/starting the Telegram polling application.

Transition requires:

- current owner WINDOWS;
- exact expected generation;
- non-empty Windows stop evidence;
- explicit actor;
- atomic `BEGIN IMMEDIATE` compare-and-swap.

Only the SHA-256 of stop evidence is stored.

Default fence therefore blocks accidental early Railway polling.

### Layer C — Windows cutover procedure

Current Windows process tree observed read-only:

`start_oemixibot_private.ps1 -> Windows Python launcher -> Python 3.14 oemixibot_app.py`

This is one runtime, not two independent pollers.

No matching Scheduled Task was found during read-only inspection.

At real cutover, after separate OWNER GO:

1. stop the Windows OEMixi runtime;
2. prove all three process-tree entries are gone;
3. prove no matching OEMixi autorun/scheduled task is active;
4. capture stop evidence;
5. atomically transition fence WINDOWS -> RAILWAY;
6. only then start Railway polling.

A manual Windows restart after the fence transition remains operationally prohibited. Release Control should retire/disable the old Windows launch path during cutover before declaring migration complete.

## 4. Exact server Gateway connection plan

Current production FastAPI authority:

`web_app.py`

Current authoritative coordination DB:

`core.ORDERS_DB_FILE`

Candidate mount module:

`oemixibot_railway_gateway_mount.py`

It mounts:

- secure price Gateway router;
- secure poller-fence router.

The price router delegates only to:

`dcp_price_gateway.request_price(core.ORDERS_DB_FILE, ...)`

It therefore uses the existing Verified Cache / Shared Broker / producer path rather than creating a second one.

### Release Control integration patch

After Theme 6 integration is accepted:

1. provision a dedicated scoped secret named `OEMIXIBOT_GATEWAY_SECRET` on:
   - authoritative `dcp-railway-test` service;
   - future `oemixibot` service.
2. do not reuse DCP cookies, browser sessions, DP sync token, or Telegram token as Gateway credentials.
3. in the integrated `web_app.py`, import `oemixibot_railway_gateway_mount`.
4. include the Gateway routers using:
   - `app`;
   - `core.ORDERS_DB_FILE`;
   - the scoped Gateway secret.
5. keep Dealer freshness server-authoritative at 24h.
6. keep MSRP freshness server-authoritative at 720h.
7. expose the service to OEMixi through Railway private networking:
   `http://dcp-railway-test.railway.internal:8080`
8. keep HMAC/timestamp/nonce auth enabled even on the private network.
9. enable the Railway OEMixi worker only after the server route and Theme 6 admission/first-switch gates are proven.
10. no direct browser fallback is allowed from OEMixi.

The mount is not wired into current production in this branch.

## 5. Server Gateway first-switch gate

Before Railway polling may be enabled:

- Theme 6 challenge handling accepted;
- atomic result completion accepted;
- fenced recovery accepted;
- producer v2/single writer accepted;
- admission freeze/release gates accepted;
- bootstrap/readiness accepted;
- passive-document guard accepted;
- one producer proof;
- queue/checkpoint/maintenance state safe;
- server Gateway signed smoke succeeds;
- default poller fence still reports WINDOWS.

A Gateway cache miss must remain fail-closed while Theme 6 admission is closed.

## 6. Railway service target

Separate service:

`oemixibot`

Target settings:

- repo: `Extremizer/dcp-railway-test`
- exact Release Control SHA only
- start: `python -u oemixibot_railway_runtime.py`
- one replica
- sleep disabled
- restart policy ALWAYS
- healthcheck `/ready`
- dedicated volume at `/data`
- no public domain required
- private Gateway URL only
- no local Chrome
- no local DCP agent
- no direct DCP browser path

## 7. Database cutover order

Real migration remains blocked until OWNER GO.

Order:

1. read-only preflight;
2. verify Windows runtime still authoritative;
3. maintenance/write freeze;
4. stop Windows poller;
5. capture stop evidence;
6. verified SQLite Online Backup of all three DBs;
7. restore to dedicated Railway volume;
8. integrity/FK/row-count/ledger/balance verification;
9. verify server Gateway;
10. transition poller fence WINDOWS -> RAILWAY;
11. start exactly one Railway poller;
12. wait for confirmed polling readiness;
13. read-only admin/dealer smoke;
14. only then allow normal writes.

## 8. Rollback

Before any Railway write:

- stop Railway;
- keep/return fence to safe owner only through Release Control;
- original Windows DBs remain authoritative.

After any Railway business/finance write:

do not restart Windows against the old snapshot.

A reverse reconciliation/snapshot is required first.

## 9. Release blocking conditions

Block release if any of these is true:

- Theme 6 dependency not accepted;
- Gateway route not mounted;
- Gateway secret missing;
- poller fence unavailable;
- fence still WINDOWS at attempted Railway start;
- Windows stop evidence absent;
- two Railway replicas;
- database preflight fails;
- migration digest/balance mismatch;
- `/ready` becomes 200 before PTB updater is running;
- direct DCP fallback detected;
- Finance/checkout regression fails.
