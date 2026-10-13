# WEB2 FINANCE v1.4 — INTEGRATION MAP

Status: READ-ONLY ANALYSIS / DOCUMENTATION ONLY.

This document does not wire any v1.4 module into runtime. It records only
integration points proven in the current branch and marks unproven targets as
`NOT PROVEN`.

## 1. Executive boundary

WEB2 currently reuses the WEB1 FastAPI application through
`web2_runtime_app.py -> from web_app import app` and attaches only the WEB2 UI.

The browser cart is still localStorage-based and its only checkout-like action is
the existing `POST /api/v1/handoff` WEB -> Telegram handoff.

No native WEB2 Internal checkout/payment confirmation endpoint is proven in the
current runtime.

Therefore the v1.4 finance modules must not be wired directly from browser
state. Authoritative order/payment facts must come from server-side order and
warehouse records.

## 2. Integration table

| Module | Target | Proven current runtime entry point | v1.4 calls | Transaction boundary | Permission boundary | Post-integration tests | Open / NOT PROVEN |
|---|---|---|---|---|---|---|---|
| `warehouse_balance_v14.py` | Internal warehouse orders / full direct-payment confirmation | Existing WEB ADMIN partner-settlement routes are proven: `/admin/warehouse-settlements/{id}/payment` and `/adjustment`. Existing RF Supplier Order lifecycle is proven in `SupplierOrderService`. **But neither is the approved warehouse-user “ОПЛАЧЕН” action. Exact v1.4 payment-confirmation route: NOT PROVEN.** | `confirm_direct_payment_and_accrue()`; when EXTREMIZER warehouse balance pays another order: `reserve_full_balance()` -> `apply_reserved_balance()`; cancellation before application: `release_reserved_balance()`; approved finance correction: `post_correction()`. | Current v1.4 functions own `BEGIN IMMEDIATE` and do **not** accept caller `conn`. Thus they are internally atomic, but cannot yet be in the same DB transaction as an Internal order/payment-status write. Integration requires a conn-aware shared-transaction adapter/internal API before claiming order-status + finance atomicity. | Full-payment confirmation must be authorized for the same `warehouse_id`; admin Acting-As still must pass effective warehouse permissions; no cross-warehouse access; server-authoritative C/P/order facts only. | exact replay; conflicting replay; warehouse isolation; simultaneous full-balance reservations; payment-status rollback with finance rollback; no balance posting before full payment; no double accrual after retry. | Warehouse role/login and real “ОПЛАЧЕН” endpoint are NOT PROVEN. Mapping between new Internal order domain and current legacy partner-settlement rows must be defined before wiring. |
| `client_wallet_info_v14.py` | Client RUB finance history | Shared ledger read path is proven: `FinanceEngine.history()` and `ClientFinanceAdapter.history()`. No WEB2 client finance-history route/UI is proven. | `record_direct_payment()`; `record_direct_refund()`. | Current writer opens its own transaction and does not accept caller `conn`. To guarantee “warehouse payment confirmation + 0 RUB info event” as one unit, integration needs a conn-aware call from the authoritative Internal payment transaction (or a durable post-commit recovery contract). | Internal Client ID owner check; order belongs to client; warehouse/order/reference must be server-derived; Acting-As cannot change wallet owner. | 0 RUB delta; real `source_amount`; exact replay; conflict on changed external amount/order/client; history visibility; real PAYMENT/ORDER_CHARGE remain unchanged; failure/retry recovery. | Native WEB2 client account/finance-history API is NOT PROVEN. Exact Internal payment-confirmation transaction is NOT PROVEN. |
| Dealer Finance v1.4 | OEMixiBOT / Dealer USD checkout | **PROVEN:** `oemixibot_app.callback(data=="checkout") -> OemixiStore.checkout_cart() -> _confirm_order_and_charge_conn() -> FinanceEngine.post_event(... ORDER_CHARGE ..., conn=c)`. | Existing `FinanceEngine.post_event()`; `dcp_procurement_ready()` later at procurement gate. | **PROVEN:** dealer order creation, items, confirmation and one ORDER_CHARGE share caller transaction from `checkout_cart()`. | Dealer resolved from Telegram access; inactive dealer blocked; USD wallet only; idempotent ORDER_CHARGE; admin top-ups remain admin-protected. | negative balance checkout; retry no second charge; multiple orders aggregate debt; payment reduces debt; Telegram callback regression; old refund/adjustment/delivery regressions. | Native Dealer WEB checkout is not proven here. Existing OEMixiBOT finance copy still contains prepaid-era wording and needs separate UX/text approval. |
| `dcp_procurement_ready()` | Actual dealer-order send to DCP | **NOT PROVEN.** Repository contains RF `SupplierOrderService.begin_send_attempt/finalize_send_attempt`, but that is partner-warehouse Supplier Orders and must not be treated as dealer DCP procurement. No exact dealer -> DCP send runtime entry point was proven. | `FinanceEngine.dcp_procurement_ready(dealer_id, conn=...)` immediately before the irreversible DCP procurement/send transition. | Function already accepts optional caller `conn`. Once real DCP send transaction is identified, gate should read balance inside the same serialized transaction/critical section as the send-state transition. External DCP network side effect still requires durable attempt/idempotency handling. | Dealer/order ownership; admin/supplier-send permission; no Acting-As bypass; block while aggregate dealer USD balance < 0. | debt < 0 blocks before network call; balance 0 allows; concurrent payment/send; repeated send attempt does not duplicate procurement; post-network uncertainty recovery. | Exact DCP send function/route, external idempotency primitive, and any per-order debt-allocation requirement are NOT PROVEN. |
| `acting_as_v14.py` | ADMIN / WEB role & permission layer | **PARTIALLY PROVEN:** current WEB ADMIN identity/session and scoped CSRF exist in `supplier_admin_auth.py`; `web_app._require_scoped_admin_form()` protects admin mutations. Current auth has no effective-subject/role impersonation model. | `start_session()`; `authorize()`; `record_event()`; `end_session()`. | Current Acting-As session/audit functions own their connections. They are not yet transaction-coupled to the protected business mutation. For critical finance/status operations, integration needs audit/authorization composition that cannot leave a successful business write without durable actual-actor/effective-subject audit. | Existing real admin authentication + ACT_AS + action permission + effective-subject permission + exact data scope + normal business guard; `ACT_AS_CRITICAL` for critical finance; nested impersonation prohibited. | admin permission denial; effective-role denial; cross-scope denial; expired/ended session; critical permission; actual/effective actor audit; business rollback/audit consistency; CSRF/session regression. | Role registry, user-to-role mapping, warehouse employee identity, USA warehouse employee identity, session TTL/re-auth policy, and exact WEB routes are NOT PROVEN/owner-open. |

## 3. Proven WEB2 bridge

`web2_runtime_app.py` imports the existing `web_app.app` and attaches WEB2 UI.
Therefore a future native WEB2 checkout should normally add server APIs to the
shared WEB application/common service layer rather than create a second finance
or order backend.

Current WEB2 browser behavior:

1. OEM result from existing `/api/v1/oem/...` APIs.
2. Cart stored in browser `localStorage`.
3. Browser posts only source/warehouse/qty identity to `/api/v1/handoff`.
4. `web_app.create_telegram_handoff()` rebuilds the authoritative offer
   server-side before producing the Telegram handoff.

This proves the existing server-trust pattern, but not native WEB Checkout.

## 4. Warehouse balance integration detail

### Proven reusable domains

- shared persistent orders DB;
- RF Supplier Order service;
- warehouse stock/reservation lifecycle;
- legacy independent warehouse-partner settlement ledger;
- WEB ADMIN authenticated/scoped mutation routes.

### Not interchangeable

`warehouse_partner_settlements.py` is an earlier per-order-item settlement
domain. `warehouse_balance_v14.py` models the newly approved accumulated
EXTREMIZER balance at each partner warehouse.

They must not be silently treated as the same ledger.

Before integration, a migration/domain ownership decision is required:

- whether v1.4 warehouse balance supersedes part of the legacy settlement ledger;
- whether legacy rows are read-only historical input;
- how one Internal order references the accumulated warehouse balance ledger.

No automatic migration is authorized.

## 5. Client wallet informational integration detail

The common `finance_ledger` can hold the v1.4 zero-delta informational event,
and existing history readers now expose source payment fields.

However the authoritative write trigger must be the real Internal money event,
not a WEB2 button click and not an uploaded payment screenshot.

Direct payment:

warehouse authorized confirmation
-> order/payment-domain validation
-> v1.4 partner-balance accrual
-> client `0 RUB` informational history.

The exact shared transaction is not yet implementable with current public
function signatures because both new writers currently own their DB
connections.

## 6. Dealer v1.4 integration detail

Telegram OEMixi dealer checkout is already the strongest proven integration
point in this addendum.

`OemixiStore.checkout_cart()` owns the transaction and calls
`confirm_order_and_charge(..., conn=c)`. The FinanceEngine receives the same
connection.

No extra Dealer Finance engine is required.

The next finance integration point is not checkout; it is DCP procurement. That
entry point remains `NOT PROVEN`.

## 7. Acting-As integration detail

Existing WEB ADMIN protection currently proves:

- signed admin session;
- header token compatibility;
- action/subject-scoped CSRF;
- protected admin mutation routes.

It does not prove:

- client/dealer/warehouse effective subject;
- role-based permission matrix;
- warehouse/USA-warehouse staff login;
- acting-as session selection UI.

A future integration should compose Acting-As *after* real-admin
authentication but *before* domain mutation, then pass the real actor and
effective subject into the domain call. It must not replace the underlying
business guard.

## 8. Required integration sequence

1. Prove/define native Internal order/payment-status runtime.
2. Make v1.4 warehouse-balance and client-info writers caller-transaction aware.
3. Add warehouse identity/permission contract.
4. Wire full-payment confirmation atomically.
5. Add client finance-history API/read UI.
6. Locate/prove dealer DCP procurement send entry point.
7. Insert `dcp_procurement_ready()` at that proven boundary.
8. Define role registry and effective-subject mapping.
9. Integrate Acting-As around selected admin routes.
10. Only then add WEB2 native checkout/finance UI adapters.

No production rollout is implied by this map.
