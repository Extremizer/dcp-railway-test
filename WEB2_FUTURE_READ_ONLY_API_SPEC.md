# WEB2 Future Read-Only API Specification — P2 Composition Draft

**Status:** specification only / no endpoint created.

This document defines the minimum future GET transport contract needed by WEB2 after Theme 1 approves a runtime read service.

## 1. Identity and scope

Identity is resolved server-side from the authenticated session. The browser must never submit an authoritative role, actor, effective subject, or warehouse scope as trusted facts.

Every warehouse-history response is bound to one exact `warehouse_id`.

A future Acting-As session, if approved, must be resolved server-side as an intersection of:
- actual authenticated actor;
- authorized effective subject;
- effective role;
- exact warehouse/resource scope.

Theme 2 does not activate real Acting-As.

## 2. Proposed GET resources

Names are illustrative and are **not implemented routes**.

### GET /api/web2/warehouses/{warehouse_id}/history

Server responsibilities:
- authorize the session for exactly `warehouse_id`;
- deny cross-warehouse access before reading data;
- call the authoritative Theme 1 read model;
- attach scoped Order metadata;
- return pagination/count without changing full-history financial totals.

Query:
- `search` optional;
- `date_from` optional;
- `date_to` optional;
- `limit` bounded;
- `offset` non-negative.

Response:
- `scope.warehouse_id`;
- `scope.effective_role`;
- `summary.posted_balance_rub`;
- `summary.reserved_balance_rub`;
- `summary.available_balance_rub`;
- `page.offset`;
- `page.limit`;
- `page.matched_count`;
- `rows[]`;
- separate `system_errors[]`.

Each row:
- canonical EXTREMIZER order number;
- canonical warehouse order number;
- warehouse_id;
- date;
- customer amount C;
- partner amount P or null;
- P source or null;
- balance delta or null;
- DUE/PAID when an approved authoritative contract supplies it;
- textual business status;
- scoped client display/search metadata;
- scoped OEM metadata.

### GET /api/web2/orders/{extremizer_order_number}

Returns a role-scoped read-only order card for the same authorized resource scope. Both displayed order numbers must resolve to the same canonical order.

### GET /api/web2/client/wallet-history

Read-only client RUB history. Direct warehouse payment/refund informational events keep wallet delta exactly `0 ₽`.

### GET /api/web2/dealer/finance-summary

Read-only dealer USD balance and DCP Financial Gate as separate fields. Gate state must not be represented as supplier enforcement until the supplier/DCP integration is authoritative.

## 3. Authorization / DENY rules

The server must return DENY for:
- warehouse B requested by a warehouse-A identity;
- order metadata belonging to another warehouse;
- OEM/client metadata outside the authorized order scope;
- client/dealer resources outside the authenticated subject;
- unapproved Acting-As combinations.

No response may contain cross-warehouse rows, totals, client names, OEMs, or order details.

## 4. Pagination and counts

Filtering/pagination affect only `rows[]` and `matched_count`.

They must not alter:
- posted balance;
- reserved balance;
- available balance;
- full-history total.

The server owns count semantics. Browser-side row counting is not an authoritative total.

## 5. Status channels

Business order status has exactly four allowed values:
- Новый;
- Собран, не оплачен;
- Собран, оплачен;
- Отгружен.

Payment status is separate:
- DUE;
- PAID.

System failures are separate:
`system_errors[] = {code, message, context}`

System Error uses `#FF3030` in the UI but never mutates the business status.

## 6. Safe rendering

All dynamic strings are untrusted presentation data:
- client display;
- OEM;
- status text;
- error code/message/context;
- descriptions;
- order identifiers.

The browser must render them with text-safe DOM operations or escaping. No server-provided value may be inserted as executable HTML.

## 7. Read-only invariant

These GET contracts must not:
- create/update payment snapshots;
- confirm payment;
- mutate ledger;
- change payment route;
- change order status;
- initiate supplier/DCP submission;
- start/end Acting-As.

Writes require separate, separately authorized contracts owned outside this Theme 2 specification.

## 8. P3 blocker

`internal_payment_snapshot` remains compatibility-only until Theme 1 selects and freezes one authoritative P3 read contract. Theme 2 must not infer endpoint fields from experimental P3 branches.
