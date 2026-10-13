# CONTRACT v1.4 — CODEX TASKS

All tasks are isolated, independently testable, and must remain on
`finance-contract-v14-20261013` until Release Control.

## Task 1 — Warehouse accrual / spending

Scope:
- atomic direct-payment confirmation + `+(C-P)`;
- warehouse isolation;
- full-coverage-only reservation;
- `-P` application;
- append-only correction;
- concurrency/idempotency.

Files:
- `warehouse_balance_v14.py`
- `test_warehouse_balance_v14.py`

Must not touch client/dealer wallet rules.

## Task 2 — Client wallet informational events

Scope:
- direct partner payment/refund visible in client finance history;
- wallet delta always exactly zero;
- preserve real external amount/reference;
- no fake PAYMENT/ORDER_CHARGE;
- replay-safe.

Files:
- `client_wallet_info_v14.py`
- `test_client_wallet_info_v14.py`
- shared history read-model fields in `oemixibot_finance.py`.

Must not alter wallet balance.

## Task 3 — Dealer negative balance / DCP gate

Scope:
- dealer ORDER_CHARGE may overdraw USD wallet;
- order charge exactly once;
- multiple orders accumulate aggregate debt;
- no credit-limit concept;
- DCP-ready only when aggregate USD balance >= 0;
- manual/delevery debits retain their existing capacity guard.

Files:
- `oemixibot_finance.py`
- `oemixibot_store.py`
- `test_dealer_debt_v14.py`
- `test_oemixibot_debt_runtime_v14.py`
- compatible updates to existing dealer regressions.

Do not invent per-order debt allocation.

## Task 4 — Acting-As audit / permissions

Scope:
- server-side impersonation session;
- reason required;
- actual admin + effective subject preserved;
- role/data-scope intersection;
- no nested impersonation;
- critical-action permission;
- ordinary business guards remain mandatory.

Files:
- `acting_as_v14.py`
- `test_acting_as_v14.py`

No production route wiring in this task.

## Shared CI

`.github/workflows/finance-contract-v14-isolated.yml`

Runs focused v1.4 tests plus existing dealer, client order/finance, and warehouse
partner settlement regressions.
