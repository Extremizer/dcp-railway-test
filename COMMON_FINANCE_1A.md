# COMMON FINANCE 1A — integration scaffold

Status: **PREPARATION ONLY / NO PRODUCTION DEPLOYMENT**

Base commit: `356015184e5ac8840cb72b4811296c74faa5ebeb`
Branch: `common-finance-1a-20261006`

## Scope

Prepare the common finance integration for:

- `@OEMixiBOT` — dealer interface;
- `@Extremizer_bot` — client bot «Проценки»;
- WEB — future consumer of the same service layer.

Explicitly out of scope:

- `@ExtremizerBOT` / «Пробник» — stays a reduced read-only/test bot;
- production deployment;
- reimplementation of FinanceEngine;
- client credit;
- client due dates;
- client aging / aging summary / aging alerts.

## Fixed client finance rules

1. Client wallet currency: **RUB**.
2. Client credit is not supported.
3. An order may be created even if the resulting client balance is `<= 0`.
4. USA positions must not enter processing while client balance is `<= 0`.
5. USA financial processing becomes allowed only when client balance is `> 0`.
6. Client payment is always a **balance top-up**, never a payment allocated to an order.
7. Amount corrections are done by a new `+` or `-` balance event with a free-text reason.
8. The reason text of an existing manual top-up/debit may be edited; the amount/type/date stay unchanged.
9. Every reason edit must create an audit record: old text, new text, actor, timestamp.
10. Financial mutations require idempotency.

## Partner warehouse future rule

Partner settlements are a separate future domain:

**PARTNER SETTLEMENTS / «Мои расчёты со складами» — PARKED.**

Fulfillment and payment are independent axes.

### Fulfillment route

- `through_extremizer`
- `dropship`

### Payment route

- `extremizer_balance`
- `direct_partner`

The payment route must be snapshotted on the order line/offer.

Changing warehouse configuration later must never rewrite the financial meaning of an old order.

Initial safe default before partner-direct payments are enabled:

- USA -> `extremizer_balance`
- warehouse -> `extremizer_balance`

Future:

- warehouse may be configured as `direct_partner`;
- such a line remains in the order/status/logistics flow;
- its amount must not create a client wallet charge.

## Important distinction

`orders.customer_total_rub` is the customer-visible order total.

It must **not** become the finance debit blindly.

Finance debit must be based on the sum of line snapshots whose:

`payment_route == "extremizer_balance"`

This protects future mixed orders:

- USA paid through Extremizer;
- partner warehouse paid directly to partner.

## Current Проценки integration points

### Existing checkout

The current production flow is roughly:

`cart -> checkout_confirm -> manager message -> save_order_to_history() -> Supplier Orders -> warehouse reserve -> cart clear`

### Existing price snapshots

`save_order_to_history()` already resolves and persists:

- USA client unit RUB from verified DP and client coefficient/rate;
- warehouse client unit RUB from warehouse price snapshot;
- `offer_source`;
- `customer_total_rub`.

Do not reimplement client price math inside finance.

### Required finance integration

The future implementation must turn order creation + finance charge into one DB transaction:

1. create order;
2. create order items / fulfillment snapshots;
3. snapshot each line `payment_route`;
4. calculate chargeable RUB total from persisted line snapshots;
5. post exactly one idempotent `ORDER_CHARGE` for the client;
6. commit all of the above together.

If any step fails: rollback all DB writes.

Recommended idempotency key:

`ORDER_CHARGE:order:{order_id}`

Manager Telegram notification should not be the source of truth for finance/order persistence.

## USA financial hold

Do not overload normal order status with payment state.

Add a separate financial readiness/hold concept for USA processing.

Required rule:

- `balance_rub <= 0` -> USA processing blocked;
- `balance_rub > 0` -> USA processing financially allowed.

Warehouse lines must not be blocked merely because USA lines are financially blocked.

A mixed order can therefore contain simultaneously:

- warehouse line continuing through its own fulfillment flow;
- USA line waiting for positive client balance.

## Common finance functions needed by Проценки

Required:

- balance;
- ORDER_CHARGE;
- PAYMENT / top-up;
- manual debit;
- REFUND / AUTO_REFUND where applicable;
- history;
- idempotency;
- financial audit;
- reason-text edit + audit.

Not required for clients:

- credit limit;
- dealer available-credit formula;
- due_date;
- aging;
- aging admin summary;
- aging alerts.

## Exact FinanceEngine recovery rule

Do **not** recreate `oemixibot_finance.py` from memory.

When the exact proven file becomes accessible:

1. copy/recover exact bytes;
2. record SHA-256;
3. compare public API/method list with the proven production version;
4. run its existing disposable regression suite unchanged;
5. only then place/refactor it into COMMON CORE;
6. verify OEMixiBOT behavior remains unchanged;
7. add the RUB/client adapter separately.

No production deploy before this gate passes.

## Next code step after FinanceEngine recovery

1. import exact FinanceEngine into this branch;
2. create a thin client-policy adapter around it;
3. add disposable RUB-client DB tests;
4. adapt `save_order_to_history()` to accept/use a shared transaction;
5. add line-level `payment_route` snapshot;
6. add atomic client `ORDER_CHARGE`;
7. prove:
   - retry does not double-charge;
   - failed checkout rolls back order + charge;
   - mixed USA/warehouse totals are correct;
   - `direct_partner` line does not debit wallet;
   - balance `<= 0` blocks USA processing only;
   - balance `> 0` releases financial hold.
