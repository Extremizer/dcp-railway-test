# WEB2 Future Read-Only API Specification — Authoritative P3 Draft

**Status:** specification only / no endpoint implemented.

**Server read contract:** `internal-order-read-v0.1`.

## Identity

Future HTTP identity must be resolved server-side from a real authenticated session/RBAC source.

The P3 Python `AdminReadContext` and `WarehouseReadContext` are test authorization inputs only and must not be exposed as browser-trusted identity.

## Proposed read resources

Names remain illustrative:

- `GET /api/web2/warehouses/{warehouse_id}/history`
- `GET /api/web2/orders/{extremizer_order_number}`
- `GET /api/web2/client/wallet-history`
- `GET /api/web2/dealer/finance-summary`

No route is created by this Theme 2 branch.

## Warehouse history

Transport should preserve the P3 server contract:
- exact warehouse scope;
- full-ledger summary;
- nullable pre-terms fields;
- DUE/PAID separately from business status;
- canonical order identifiers;
- authoritative P source;
- nullable balance delta.

Filtering/pagination must not recalculate summary totals.

## Strict order metadata

For snapshot-backed orders, transport should expose the authoritative `read_order()` result or an equivalent server-side joined projection:
- exact warehouse scope;
- canonical EXTREMIZER number;
- warehouse order number;
- client_id;
- business status;
- payment status;
- C/P and P source;
- payment route;
- balance delta;
- scoped items/OEMs.

## DENY

Cross-warehouse requests must fail before any row, total, client, OEM, or order detail is returned.

ADMIN still selects one explicit warehouse scope.

## System Error

System failures must use a separate error channel, not a fifth business status.

## Safe rendering

Client/OEM/order/error/status strings are untrusted presentation values. WEB2 escapes them before HTML rendering.

## Read-only guarantee

These future GET routes must not:
- create/modify snapshots;
- confirm payments;
- mutate ledger;
- rewrite payment route;
- mutate order status;
- submit to supplier/DCP;
- start/end Acting-As.

## Current blockers

Still missing before runtime wiring:
- real session/auth/RBAC adapter;
- approved HTTP transport;
- authoritative count/pagination envelope if required;
- production Acting-As resolver;
- release decision through Theme 4 / Release Control.
