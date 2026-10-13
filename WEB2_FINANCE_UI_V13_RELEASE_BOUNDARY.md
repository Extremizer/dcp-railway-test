# WEB2 UI v1.3 — Finance v1.4 isolated implementation boundary

> **Role/read-model addendum:** this earlier boundary is retained for traceability. Current role-consistency and warehouse read-model rules are authoritative in `WEB2_ROLE_CONSISTENCY_READMODEL_BOUNDARY.md` and `WEB2_WAREHOUSE_HISTORY_ADAPTER_CONTRACT.md`.

Status: isolated UI only. No production wiring, merge, deployment, financial operation, real user impersonation, DCP submission, or order mutation is authorized by this package.

## Fixture UI surfaces

- Warehouse History Desktop ADMIN — exactly five primary columns.
- Warehouse History Desktop SKLAD — same five primary fields with warehouse order number first.
- Warehouse History Mobile — structured cards preserving all five fields.
- Client wallet informational events for `INFO_DIRECT_WAREHOUSE_PAYMENT` and `INFO_DIRECT_WAREHOUSE_REFUND`.
- Dealer USD balance plus separate DCP Financial Gate state.
- Persistent Acting-As indicator with actual admin, effective subject, effective role and explicit exit control.

The browser consumes a fixture read-model JSON. It does not calculate finance from localStorage and never imports or instantiates FinanceEngine/OrderEngine.

## Confirmed visual rules

Warehouse business-order row palette:

- New: `#FFFFFF`.
- Assembled, unpaid: `#FF6B65`.
- Assembled, paid: `#FFF176`.
- Shipped: `#BFF803`.

Status text remains visible; color is never the sole indicator.

`System Error` is **not** a fifth business-order status. `#FF3030` is reserved for a separate system-error message carrying explicit error text/context.

The full-history balance fixture is `3 425 ₽` and is deliberately supplied by the read model, not recomputed from filtered browser rows. The preview also exposes posted/reserved/available separately.

## Server APIs/read models required before real wiring

No endpoint below is asserted to exist today. Names are intentionally illustrative; authoritative server contracts must be owned by COMMON CORE / Theme 1.

### Warehouse History read model

Theme 1 now provides the proven read-only module `warehouse_history_v14.py`. Its typed WEB2 mapping is frozen in `WEB2_WAREHOUSE_HISTORY_ADAPTER_CONTRACT.md`; no parallel history calculator is permitted.

Authorization for future HTTP/service exposure remains: ADMIN capability or exact warehouse resource scope.

Required UI response fields:

- `warehouse_id`
- `posted_rub`
- `reserved_rub`
- `available_rub`
- `full_history_balance_rub`
- paginated rows containing:
  - `date`
  - `extremizer_order_no`
  - `warehouse_order_no`
  - `client_display`
  - searchable `oem[]`
  - `customer_amount_rub`
  - `partner_amount_rub`
  - ready-to-render balance delta / empty unpaid state
  - `customer_amount_struck` or an authoritative payment-source semantic
  - textual status plus stable status code

Server must own finance semantics and total balance. Browser may filter/paginate presentation but must not reconstruct ledger truth.

### Client Wallet read model

Authorization: exact client scope (or authorized Acting-As intersection).

Required fields:

- `client_id`
- current RUB wallet balance
- paginated ledger/history events
- `event_type`
- `order_id`
- `description`
- `amount` (wallet delta)
- `source_amount`
- `source_currency`
- `reference_type`
- `reference_id`
- `created_at`

For direct warehouse payment/refund informational rows, wallet delta must remain `0 RUB` while real external RUB amount remains visible.

### Dealer USD Finance read model

Authorization: exact dealer tenant/scope.

Required fields:

- `dealer_id`
- aggregate USD balance
- `dcp_financial_gate_ready`
- dealer order snapshot/status
- order total USD
- persisted ORDER_CHARGE reference/event count or equivalent idempotency evidence

Do not expose a credit-limit mechanism. DCP financial gate is separate from order status. Until supplier wiring exists, UI must state that gate readiness is not proof of automatic DCP submission blocking.

### Acting-As read/session model

Authorization: authenticated ADMIN with `ACT_AS`; critical writes additionally require `ACT_AS_CRITICAL` and normal business guards.

Required visible fields:

- actual admin actor
- effective subject
- effective role
- data/resource scope
- active session identifier/status
- expiry if Theme 4 chooses to display it
- explicit end/return-to-admin action

No target-user password/session token is ever requested by the UI.

### Order Card read model

Authorization: role/capability/resource-scope projection, never unrestricted admin data for client/dealer/sklad views.

Required fields include canonical EXTREMIZER order number, canonical warehouse order number where applicable, customer/partner amounts, textual status, searchable OEM/client metadata, positions/quantities, and documents/tracking permitted to the current role.

Both displayed order numbers must resolve to the same authoritative order card.

## Integration gates

Theme 1 has now closed two former preparation blockers on branch `finance-v14-internal-routing-p0-p1-20261013`:

- default Internal warehouse routing is `CLIENT -> WAREHOUSE` / `DIRECT_PARTNER`;
- authoritative read-only `warehouse_history_v14.py` exists.

Theme 2 still does **not** wire real payment or finance runtime. Remaining real-wiring gates include:

1. role-scoped HTTP/read-service exposure of `warehouse_history_v14.py`;
2. authoritative canonical warehouse-order-number ownership for every Internal order;
3. scoped Order Core metadata projection for status/client/OEM and detailed order cards;
4. real Acting-As identity resolver/router and permission composition;
5. supplier/DCP submission enforcement of `dcp_procurement_ready()` before automatic blocking can be claimed.
