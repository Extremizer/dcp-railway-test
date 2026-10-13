# WEB2 P4 — Secure Read Boundary

**Status:** isolated proof only / no production route / no deploy.

## 1. Identity

ADMIN identity reuses the existing WEB ADMIN mechanism in `supplier_admin_auth.py`:

- cookie: `extremizer_admin_session`;
- signed HMAC session;
- `WEB_ADMIN_SESSION_MAX_AGE = 12h`;
- verification through `require_web_admin_access()`;
- raw ADMIN token is not stored in the browser cookie.

P4 does **not** create a second ADMIN login.

Browser-supplied role, actor, effective subject or warehouse headers are never authoritative identity.

## 2. ADMIN scope

The isolated proof binds an authenticated WEB ADMIN session to a server-side set of allowed warehouse IDs.

Request path `warehouse_id` is treated only as a requested resource scope. It succeeds only when server-side policy allows that exact warehouse.

Cross-warehouse access is DENY before P3 data is returned.

## 3. SKLAD blocker

Real warehouse employee authentication/membership is not established in the audited boundary.

Therefore P4 does not turn:
- WEB ADMIN session;
- disposable `WarehouseReadContext`;
- browser role;
- fixture Acting-As

into warehouse employee authentication.

SKLAD proof is DENY-only until an authoritative membership source exists.

## 4. Internal read chain

Successful isolated ADMIN GET:

signed ADMIN session
→ server-side exact warehouse scope
→ `internal_order_read_v01.read_warehouse_history()`
→ snapshot-backed `read_order()` where required
→ `web2_finance_read_model_adapter`
→ JSON.

Contract version remains exactly:

`internal-order-read-v0.1`

## 5. Financial validation

WEB2 accepts RUB fields only as exact integers.

Rejected:
- bool;
- float, including integral-looking float;
- numeric strings;
- fractional strings;
- NaN / Infinity;
- lists/dicts/other non-integer types.

C and P are nullable where the P3 history contract permits null, but when present they must be non-negative exact integers.

Signed `balance_rub` remains valid because ledger effects may be positive or negative.

Summary invariant:

`available_balance_rub == posted_balance_rub - reserved_balance_rub`

The adapter validates this but does not replace/recalculate the server value for display.

## 6. Client identity

`read_order().order.client_id` is a canonical client identifier, not a display name.

WEB2 stores it separately as `clientId`.

A display name may appear only from a separately supplied trusted scoped Client Directory projection.

Without such a directory:

`client = null`

and the browser displays `—`.

No client name is inferred from another warehouse, legacy settlement data, order text, or the canonical ID.

## 7. Status mapping

Four approved UI states remain:

- Новый — #FFFFFF
- Собран, не оплачен — #FF6B65
- Собран, оплачен — #FFF176
- Отгружен — #BFF803

P4 technical mapping is deliberately narrower than the UI vocabulary.

Authoritatively evidenced shared `orders.status` values in current P3/Core regressions:

- `new` → Новый
- `assembled_unpaid` → Собран, не оплачен

Unknown/unproven technical states, including `cancelled`, `assembled_paid`, and `shipped`, fail closed in the P4 proof.

A future payment-ready technical mapping remains a Theme 1/owner decision.

## 8. Session expiry

Missing, invalid, malformed, rotated-token, or expired ADMIN session is DENY.

Failure responses are generic and do not return:
- order IDs;
- client IDs;
- OEMs;
- C/P;
- balance values;
- internal contract error text.

## 9. Pagination and count

P3 accepts `search`, `limit`, and `offset`.

P4 transport constrains:
- search length;
- limit 1..100;
- offset >= 0.

P3 does not currently expose authoritative `matched_count`.

Therefore P4 returns:

`matchedCount = null`

and does not fake total count from the visible page.

Summary posted/reserved/available values remain full-history values independent of page/search.

## 10. System Error

System Error remains a separate channel and never becomes a fifth business status.

UI color remains `#FF3030`.

## 11. Safe rendering

Dynamic client/OEM/order/status/error strings remain untrusted presentation data and browser tests continue to prove escaping.

## 12. Read-only invariant

The isolated GET proof performs no writes.

It must not:
- create or update payment snapshots;
- confirm payment;
- mutate ledger;
- reserve/apply balance;
- mutate order status;
- mutate payment route;
- initiate DCP/supplier/logistics actions;
- create/end Acting-As.

Disposable SQLite before/after dumps are compared in regression.

## 13. Not production wiring

The harness route prefix `/_test/web2/...` exists only inside `web2_p4_secure_read_harness.py`.

It is not imported into `web_app.py` and is not a public runtime route.

NO MERGE / NO DEPLOY / NO PRODUCTION.
