# INTERNAL ORDER P3 — READ-MODEL CONTRACT v0.1

**Status:** isolated server-side contract / authorization wiring pending.  
**Producer boundary:** Theme 1 COMMON CORE.  
**Consumer:** Theme 2 WEB2 role-based UI.  
**No public HTTP route is created by this contract.**

## 1. Authoritative sources

Finance/history:
- `warehouse_history_v14.py`;
- `warehouse_internal_balance_ledger`;
- `warehouse_internal_balance_reservations`.

Payment-ready Internal terms:
- `internal_order_payment_snapshots_v01`;
- `internal_order_payment_state_v01`.

Order metadata:
- future authoritative role-scoped shared Order Core projection.

Forbidden source for P:
- legacy `warehouse_partner_settlements`;
- browser/localStorage;
- C-minus-margin reconstruction;
- any UI fallback.

## 2. Raw warehouse-history row

For an Internal order with a trusted immutable snapshot:

| Field | Type | Source |
|---|---|---|
| `warehouse_id` | integer | snapshot + exact shared-line validation |
| `extremizer_order_number` | string | shared `orders.order_id` |
| `warehouse_order_number` | string/null | immutable payment snapshot; old fallback only when no snapshot exists |
| `date` | string/null | shared order created_at; snapshot created_at fallback |
| `client_id` | string/null | immutable payment snapshot |
| `customer_amount_rub` | integer/null | immutable payment snapshot C when present |
| `partner_amount_rub` | integer/null | immutable payment snapshot P when present |
| `partner_amount_source` | string/null | exactly `internal_payment_snapshot` for snapshot P |
| `payment_status` | `DUE`/`PAID`/null | `internal_order_payment_state_v01` |
| `payment_route` | string/null | immutable payment snapshot |
| `order_status_snapshot` | string/null | immutable payment snapshot |
| `balance_rub` | integer/null | posted new Internal balance ledger effect |

For a new unpaid Internal order without authoritative P snapshot:
- `partner_amount_rub = null`;
- `partner_amount_source = null`;
- `payment_status = null`;
- `balance_rub = null`.

No synthetic P is permitted.

## 3. Summary fields

The read model preserves server-side full-ledger summary:

- `warehouse_id`;
- `posted_balance_rub`;
- `reserved_balance_rub`;
- `available_balance_rub`;
- `direct_payment_accruals_rub`;
- `applied_balance_rub`;
- `corrections_net_rub`.

Search and pagination affect rows only. They never recalculate the summary.

Control regression remains:

`+2 925 +1 500 +3 000 +4 500 −8 500 = 3 425 ₽`.

## 4. Snapshot trust checks

A snapshot is accepted by `warehouse_history_v14` only when:

1. snapshot schema contains all v0.1 immutable fields;
2. snapshot `order_id` matches the requested shared order;
3. shared order exists when shared `orders` is available;
4. snapshot `warehouse_id` exactly equals requested warehouse scope;
5. all persisted shared order lines belong to that same warehouse and are warehouse lines;
6. matching payment-state row exists;
7. payment status is exactly `DUE` or `PAID`.

Mismatch is fail-closed.

## 5. Scoped read-service contract

Version:

`internal-order-p3-read-v0.1`

Entry point:

`internal_order_p3_read_service.read_scoped_warehouse_history(...)`

Top-level response:

- `contract_version`;
- `scope`;
- `summary`;
- `rows`;
- `page`.

`scope`:
- `warehouse_id`;
- `effective_role`;
- `effective_subject`;
- `acting_as`.

`page`:
- `offset`;
- `limit`;
- `matched_count`.

## 6. Authorization input contract

Current P3 uses only disposable fake trusted context:

`WarehouseReadAuthorizationContext`

Required facts:
- actual actor;
- effective subject;
- effective role;
- exact `warehouse_id`;
- permission `WAREHOUSE_HISTORY_READ`;
- authorized=true;
- Acting-As flag when relevant.

Allowed prototype roles:
- `admin`;
- `rf_warehouse_user`.

Both ADMIN and SKLAD use exact warehouse scope. No wildcard ADMIN read is modeled here.

This object is NOT authentication and MUST NOT be constructed from browser claims in real runtime.

## 7. Scoped Order Core metadata

WEB2 warehouse history additionally requires authoritative Order Core metadata:

`ScopedOrderMetadata`

Fields:
- `order_id`;
- `warehouse_id`;
- `client_id`;
- `client_display`;
- `oems[]`;
- `status`.

Rules:
- metadata order ID must equal canonical EXTREMIZER order ID;
- metadata warehouse ID must equal exact read scope;
- when immutable snapshot has `client_id`, scoped Order metadata `client_id` must match;
- missing or mismatched metadata fails closed;
- finance values are never inferred from status/client/OEM metadata.

Search may match:
- date;
- EXTREMIZER order number;
- warehouse order number;
- client ID/display;
- OEM;
- textual status.

## 8. Read-only guarantee

P3 read-service:
- exposes no write command;
- creates no route;
- does not initialize or migrate schema during reads;
- opens owned `warehouse_history_v14` SQLite connections with `PRAGMA query_only=ON`;
- never changes ledger, snapshots, payment route or payment state.

## 9. Compatibility

Orders without P3 snapshots keep the previous Finance v1.4 read behavior.

Legacy direct-payment confirmation/reservation sources remain fallback only for historical rows that have no immutable Internal payment snapshot.

USA orders remain outside this Internal warehouse read service.

## 10. Still pending before runtime wiring

- real session/authentication adapter;
- warehouse membership source;
- `WAREHOUSE_HISTORY_READ` / future `CONFIRM_PAYMENT` capability source;
- real Acting-As intersection;
- canonical shared Order Core client linkage;
- canonical warehouse order-number ownership/uniqueness;
- approved payment-ready shared order status;
- approved P confirmation/revision workflow;
- cutover/ownership decision for new Internal balance vs legacy settlements;
- public/private transport layer contract if an HTTP endpoint is later approved.
