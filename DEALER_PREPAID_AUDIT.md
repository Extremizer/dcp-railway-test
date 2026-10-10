# Dealer prepaid-only dependency audit

Source: `probnik-runtime-20261005` at
`ad0806cfea8e062d9d26beb3d0b4339e847a6f4c`.
The read-only audit preceded creation of the feature branch and all edits.

## Dependencies found

- `oemixibot_finance.py`: the single FinanceEngine. `credit_limit()` read
  `dealers.credit_limit_usd`; `available_to_order()` and `order_capacity()`
  added that limit to ledger balance. `require_order_capacity()` therefore
  permitted credit orders. Direct `post_event(ORDER_CHARGE)` had no capacity
  enforcement at all.
- `dealer_debt_status()` exposed credit used/remaining/over-limit data.
  `set_credit_limit()` updated the dealer and `finance_credit_limit_audit`;
  `credit_limit_history()` / `credit_limit_event()` read its audit history.
- Due-date APIs created/read/wrote `finance_receivable_terms` and
  `finance_due_date_audit`. Aging allocated payments/refunds to debits and
  exposed no-due/current/overdue 1–7, 8–30 and 30+ buckets. Aging alerts
  created/read/wrote `finance_aging_alert_state` and acknowledged notifications.
- `common_finance_contract.py`: DEALER_POLICY enabled credit, due dates and aging.
- `client_finance_adapter.py` and `extremizer_bot.py` import the same engine
  for CLIENT/RUB. Three checkout regression scripts pin its entire source hash;
  these pins must change when the shared engine changes.
- Repository-wide searches found no other callers/imports of the credit,
  due-date, debt, aging or alert APIs, and no dealer-credit Telegram/UI/
  callback/command handlers. Generic warehouse file-size limits and supplier
  WhatsApp `messaging_product` are unrelated search hits.
- `client_finance_schema.py` creates the shared ledger, not dealer credit
  schema. No independent dealer schema migration or dealer order-submission
  bot implementation is present in this checkout. The production launcher
  explicitly leaves OEMixiBOT isolated; OEMixiBOT identity/analytics/DCP
  clients are not finance/order-placement handlers.
- Existing tests exercise CLIENT/RUB checkout, not dealer credit/aging. Dealer
  behavior now has disposable SQLite regression coverage.

## Runtime changes and compatibility

Remove the unused debt, credit administration/history, due-date, aging,
alert and schema-creation APIs. Keep `credit_limit()` as a deprecated zero-only
compatibility shim and the `order_capacity()['credit_limit']` key fixed at zero.
Neither reads a legacy credit column or authorizes credit. Capacity is the
actual USD ledger balance, including historical negative balances.

All dealer ledger writes require USD. Every dealer debit (ORDER_CHARGE,
DELIVERY_CHARGE, ADJUSTMENT_MINUS) must fit the available prepaid balance.
The shared engine enforces this under the same SQLite write transaction as
the debit; direct post_event cannot bypass a UI capacity check. Caller-owned
connections are never committed/rolled back by this method. Concurrent
standalone debits serialize with BEGIN IMMEDIATE. Existing duplicate-key
rejection semantics are retained.

Manual debit adjustments that would overdraw are now rejected. Historical
negative balances are preserved; payments first cover that balance before
new orders are possible. No implicit historical debt forgiveness is performed.

## Database and release boundaries

No migration, table/column deletion or historical data rewrite. Existing
`dealers.credit_limit_usd`, `finance_credit_limit_audit`,
`finance_receivable_terms`, `finance_due_date_audit` and
`finance_aging_alert_state` remain unused schema debt when already present.
The runtime no longer creates or accesses those aging/credit tables.

Retain finance_ledger, USD payments, charges, manual/auto refunds,
adjustments, payment methods, event/history APIs, idempotency and the single
FinanceEngine. CLIENT/RUB and ORDER CORE contracts are unchanged.

PR #20 changed paths (Apply/auth/web/HTTP regression and its workflow) are
excluded from this patch. No warehouse, supplier, execution or checkout
runtime implementation changes. Only three existing checkout tests update
their expected engine fingerprint; their behavioral assertions remain intact.

No Railway operations, production/volume access, secrets, deployment, merge
or force-push. Tests use only disposable local SQLite fixtures or source reads.

## Remaining scope limits

The isolated OEMixiBOT bot and its external consumers are not in this
repository, so their deployed UI/import compatibility cannot be certified.
External callers of removed APIs must migrate. Historical credit schemas
and debt remain; no production introspection was performed. Arbitrary SQL
writers outside FinanceEngine are not governed by this application-level
policy. A release needs separate deployment authorization and recovery planning.
