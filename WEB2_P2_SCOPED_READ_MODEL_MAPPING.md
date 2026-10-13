# WEB2 + Internal P3 — Scoped Read-Model Adapter Mapping

**Status:** isolated integration on authoritative Theme 1 P3 / no production wiring.

**Base:** `09af37edd17fe2881f6b87f2d57946b5e0b7692e`  
**Contract:** `internal-order-read-v0.1`

## Authoritative sources

- `internal_order_read_v01.read_warehouse_history()`
- `internal_order_read_v01.read_order()`
- `warehouse_history_v14.py`

Theme 2 does not modify these files.

## P3 envelope

WEB2 requires:
- `contract_version == internal-order-read-v0.1`;
- exact `scope.warehouse_id`;
- matching `summary.warehouse_id`;
- rows whose `warehouse_id` equals the exact scope.

Any mismatch fails closed.

## Nullable history fields

The P3 history contract permits:
- `warehouse_order_number = null`;
- `date = null`;
- `customer_amount_rub = null`;
- `partner_amount_rub = null`;
- `payment_status = null`.

WEB2 preserves null and renders it as `—`. It never converts missing C/P to `0 ₽`.

`balance_rub = null` remains a blank balance-delta cell until a real ledger event exists.

## Snapshot-backed rows

When `internal_payment_snapshot = true`:
- P source must be `internal_payment_snapshot`;
- payment status must be DUE or PAID;
- WEB2 requires a matching real `read_order()` payload;
- strict order scope, order ID, C/P, payment source/status/route, warehouse number and balance must match the history row.

Strict `read_order()` provides scoped `client_id` and `items[]`, which WEB2 uses for client/OEM presentation metadata.

## Pre-terms rows

A pre-terms row may remain visible without a trusted snapshot.

WEB2 does not invent:
- warehouse order number;
- date;
- C;
- P;
- payment status;
- client metadata;
- OEM metadata.

Missing values render as `—`.

## Status separation

Server technical order states are mapped only to the four approved UI business states:

- `new` → Новый — `#FFFFFF`
- `assembled_unpaid` → Собран, не оплачен — `#FF6B65`
- `assembled_paid` → Собран, оплачен — `#FFF176`
- `shipped` → Отгружен — `#BFF803`

DUE/PAID remains separate from business status.

System Error remains a separate `#FF3030` message channel.

## Authorization boundary

`AdminReadContext` and `WarehouseReadContext` in P3 are disposable test contracts only.

They prove scope behavior but are **not production authentication/session/RBAC**.

No real Acting-As is activated by Theme 2.
