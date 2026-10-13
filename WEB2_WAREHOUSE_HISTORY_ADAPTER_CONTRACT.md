# WEB2 Warehouse History v1.4 — Read-Model Adapter Contract

**Status:** isolated UI integration preparation only.  
**Authoritative finance/history source:** `warehouse_history_v14.py` from Theme 1.  
**Runtime wiring:** PENDING.

## 1. Boundary

WEB2 does not calculate warehouse finance from browser state and does not own a second FinanceEngine/OrderEngine.

The adapter:

`warehouse_history_v14.read_history()`
→ `web2_finance_read_model_adapter.adapt_warehouse_history()`
→ role-scoped UI read model
→ five-column warehouse presentation.

No write operation is performed by the adapter.

## 2. Theme 1 source → WEB2 mapping

### Summary

| Theme 1 | WEB2 |
|---|---|
| `summary.warehouse_id` | `warehouseId` |
| `summary.posted_balance_rub` | `postedRub` |
| `summary.reserved_balance_rub` | `reservedRub` |
| `summary.available_balance_rub` | `availableRub` |
| `summary.posted_balance_rub` | `fullHistoryBalanceRub` |

Theme 1 calculates `posted_balance_rub` from the complete warehouse ledger independently from row search/pagination. The adapter therefore uses this server value as the approved full-history total. It never totals visible browser rows.

### Row

| Theme 1 | WEB2 |
|---|---|
| `warehouse_id` | `warehouseId` |
| `date` | `date` |
| `extremizer_order_number` | `extremizerOrderNo` |
| `warehouse_order_number` | `warehouseOrderNo` |
| `customer_amount_rub` | `customerAmountRub` |
| `partner_amount_rub` | `partnerAmountRub` |
| `partner_amount_source` | `paymentSource` |
| `balance_rub` | `balanceDeltaRub` |

`customerAmountStruck` is true only when:
- `balance_rub < 0`; and
- `partner_amount_source == "warehouse_balance_reservation"`.

## 3. Authoritative partner amount P

WEB2 must never invent P.

Allowed Theme 1 sources currently proven by `warehouse_history_v14.py`:

- `direct_payment_confirmation`;
- `warehouse_balance_reservation`.

If no authoritative P exists:
- `partner_amount_rub = None`;
- UI renders `—`;
- UI must not render `0 ₽`;
- UI must not calculate P from C, margin, balance delta, fixture history, localStorage or any client-side rule.

If a posted financial effect exists without authoritative P, the typed adapter fails closed with a contract error.

Fixture P values are permitted only in explicit DEMO/fixture data and are not evidence of production P.

## 4. Textual business status

`warehouse_history_v14.py` does not currently return textual business order status.

Therefore status is supplied separately by an authoritative, role-scoped Order Core metadata projection keyed by canonical EXTREMIZER order number.

Allowed business statuses in the current UI contract are exactly:

- Новый → `new`;
- Собран, не оплачен → `assembled-unpaid`;
- Собран, оплачен → `assembled-paid`;
- Отгружен → `shipped`.

The adapter does not infer order status from finance values.

`System Error` is not a business status and is rejected if supplied as order status.

## 5. Client and OEM search metadata

`warehouse_history_v14.py` does not currently expose client-display or OEM search fields.

They must come from the same authoritative scoped Order Core projection as status.

The preview accepts:
- `client`;
- `oems[]`.

These values are presentation/search metadata only and do not participate in finance calculations.

## 6. System errors

System failures use a separate `systemErrors[]` channel with:
- `code`;
- `message`;
- optional canonical EXTREMIZER order number;
- optional warehouse order number.

System Error uses `#FF3030` as a separate alert/message presentation.

It does not:
- become a fifth business order status;
- recolor an order row as a business state;
- replace the actual business status.

## 7. Role consistency

Normal Acting-As preview context:

`effectiveRole=rf_warehouse_user`
→ effective view = `SKLAD`
→ warehouse number first.

An ADMIN presentation may be compared with SKLAD only after explicit `DEMO MODE` activation.

DEMO MODE:
- is clearly labelled;
- hides the Acting-As banner to avoid implying an effective-role change;
- changes presentation only;
- does not change permissions, effective role, real session or server data scope.

## 8. Safe rendering

Future server-provided strings are treated as text:
- client;
- OEM;
- status;
- system-error code/message/context;
- client wallet descriptions;
- dealer order identifiers/status;
- order identifiers.

The preview escapes dynamic values before HTML template insertion. Load errors are rendered with DOM `textContent`.

Browser regression injects HTML/event-handler payloads and verifies they do not create active DOM elements or script side effects.

## 9. Still required from server/Common Core

Before real wiring, Theme 1/Common Core still must provide:

1. a role-scoped HTTP/read-service boundary for `warehouse_history_v14.read_history()`;
2. authoritative scoped Order Core metadata for:
   - business status,
   - client display/search,
   - OEM search;
3. canonical ownership/source of `warehouse_order_number` for all Internal orders;
4. authorization enforcing exact `warehouse_id` scope for SKLAD and Acting-As intersections;
5. real pagination/count contract if server-side pagination is adopted;
6. separate system-error payload contract;
7. real Acting-As router/session integration;
8. Dealer DCP supplier enforcement if `dcp_procurement_ready()` is to block real supplier submission.

No real financial endpoint is created by this Theme 2 work.
