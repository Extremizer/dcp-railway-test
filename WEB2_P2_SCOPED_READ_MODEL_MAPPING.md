# WEB2 + Internal P2 — Scoped Read-Model Adapter Mapping

**Status:** isolated composition on Theme 1 P2 / real wiring pending.

**Composite base:** `35d584a81fcc30cb6c4ab79d7cda1a53098ed045`

## Boundary

Theme 2 owns only presentation mapping. It does not modify `warehouse_history_v14.py`, Finance/Core, ledger, payment routing, or Internal Order Core.

Current server truth is Theme 1 P2:

`warehouse_history_v14.read_history(...)`
→ `web2_finance_read_model_adapter.adapt_warehouse_history(...)`
→ role-scoped UI model.

## Summary mapping

| Theme 1 P2 | WEB2 |
|---|---|
| `summary.warehouse_id` | exact `warehouseId` scope |
| `posted_balance_rub` | `postedRub` and full-history total |
| `reserved_balance_rub` | `reservedRub` |
| `available_balance_rub` | `availableRub` |

Search/pagination may reduce returned rows but must not recalculate the full-history total.

## Row mapping

| Theme 1 P2 | WEB2 |
|---|---|
| `warehouse_id` | `warehouseId` |
| `date` | `date` |
| `extremizer_order_number` | canonical EXTREMIZER number |
| `warehouse_order_number` | canonical warehouse number |
| `customer_amount_rub` | customer amount C |
| `partner_amount_rub` | partner amount P or null |
| `partner_amount_source` | payment/source discriminator |
| `balance_rub` | historical balance delta or null |
| optional `payment_status` | DUE/PAID, separate from business status |

## Business status vs payment status

Business status is authoritative scoped Order metadata and is exactly one of:

- Новый — `#FFFFFF`;
- Собран, не оплачен — `#FF6B65`;
- Собран, оплачен — `#FFF176`;
- Отгружен — `#BFF803`.

Payment status, when supplied by a future approved server contract, is only `DUE` or `PAID`. It never replaces or derives the business status.

System Error `#FF3030` is a separate message channel, never a fifth order status.

## Authoritative P

P2-approved sources:

- `direct_payment_confirmation`;
- `warehouse_balance_reservation`.

Missing authoritative P remains `null` and renders as `—`. WEB2 must not synthesize P from C, margin, balance, legacy settlements, browser state, or fixtures.

A posted ledger effect with missing P fails closed.

## Internal payment snapshot compatibility

`internal_payment_snapshot` is **not enabled by default** in the adapter.

Compatibility tests may pass:

`allow_internal_payment_snapshot_compat=True`

to prove that a future P3 row can carry authoritative C/P and DUE/PAID without colliding with business status. This opt-in test is not runtime wiring and must remain disabled until Theme 1 approves the P3 contract.

## Scoped Order metadata

Every visible row requires an authoritative metadata projection keyed by canonical EXTREMIZER order ID containing:

- exact `warehouse_id`;
- textual business `status`;
- `client` display/search value;
- `oems[]`.

Metadata `warehouse_id` must exactly match the server history scope. Missing or cross-warehouse metadata fails closed.

## Five-column presentation

Desktop remains exactly:

1. Дата
2. role-relative order numbers
3. Сумма для клиента
4. Сумма по условиям склада
5. Баланс

ADMIN: EXTREMIZER number first.  
SKLAD: warehouse number first.

No financial meaning is recalculated in the browser.
