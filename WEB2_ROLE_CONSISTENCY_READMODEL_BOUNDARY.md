# WEB2 Role Consistency / Internal P3 Read Boundary

**Target:** WEB2 + AUTHORITATIVE INTERNAL P3 READ COMPATIBLE / ISOLATED CI PASS

## Included

- Existing role-consistent WEB2 fixture UI.
- Typed adapter for `internal-order-read-v0.1`.
- Real disposable SQLite compatibility with `read_warehouse_history()`.
- Real strict `read_order()` compatibility for snapshot-backed rows.
- Nullable pre-terms support.
- Exact warehouse scope validation.
- Four business statuses only.
- Separate System Error channel `#FF3030`.
- Safe rendering.
- Browser regression.

## Not included

- No Finance/Core change.
- No modification of Theme 1 P3 files.
- No P4 code.
- No real auth/session/RBAC.
- No real Acting-As.
- No financial write endpoint.
- No merge.
- No deploy.
- No production change.

## Nullable UI rule

Missing:
- warehouse order number;
- date;
- C;
- P;
- payment status;
- client/OEM metadata

is represented as `—`.

A missing balance delta remains visually blank until a real ledger event.

## Role rule

Outside DEMO MODE, effective role controls the visible presentation.

DEMO MODE is presentation-only and is not an authorization mechanism.

## P3 authorization warning

Passing a disposable P3 `WarehouseReadContext` or `AdminReadContext` test is not evidence of production authentication.

## CI rule

The isolated workflow must run on the exact branch HEAD and execute:
- authoritative Theme 1 P3 regression;
- Theme 2 real-P3 adapter compatibility;
- browser role/nullable/safe-render regression;
- Python and JavaScript syntax checks.
