# EXTREMIZER INTERNAL ORDER P5 SELECTIVE RELEASE — 2026-10-17

## Purpose

Build a dependency-closed Internal Order P5 proof/boundary slice on top of the final RC1 checkpoint without merging the divergent Finance v1.4 development history.

## Release base

- branch: release-control-rc1-20261017
- HEAD: 757d6b773bac1c5dd25f5360bce5535fefddf1e2

## Accepted P5 source

Primary P5 source:
- branch: internal-order-p5-trusted-persistence-20261014
- HEAD: d631e30ce398bacb7195ca518e2260067a51c36f
- source CI: #38488405122 — SUCCESS

Earlier alternative branch:
- internal-order-p5-trusted-terms-20261014
- HEAD: 40e70c4cd619e4f244e65c5a078cae7cf34ca74f

The earlier trusted-terms line is intentionally NOT merged here. It is more invasive and diverged from the later trusted-persistence line.

## Dependency closure

RC1 did not contain the P2–P4 Internal Order modules. Therefore P5 cannot be released as only its six source commits.

This selective slice adds only the runtime/support chain required by P5:
- client_wallet_info_v14.py
- warehouse_balance_v14.py
- warehouse_history_v14.py
- internal_order_core_v01.py
- internal_order_read_v01.py
- internal_order_terms_v01.py
- internal_order_p5_boundary.py

Focused source tests are included for wallet/balance/history, atomic Internal finance, P2 core, P3 read model, P4 terms integrity and P5 boundary.

## Source integrity

15 runtime/test files in this slice are byte-for-byte equal to the accepted d631e30c... source.

common_finance_contract.py is intentionally NOT replaced by the older P5 version.

Only the isolated helper:
resolve_internal_client_payment_route(...)
is added to the current RC1 finance contract.

The current RC1 global default behavior remains unchanged:
- default_client_payment_route("usa") -> EXTREMIZER_BALANCE
- default_client_payment_route("warehouse") -> EXTREMIZER_BALANCE

This avoids silently enabling the broader P0/P1 warehouse-direct-payment rollout.

## Explicitly excluded

Not included:
- global warehouse default route switch to DIRECT_PARTNER;
- test_internal_payment_routing_v14.py, because it belongs to that broader routing rollout;
- older P5 trusted-terms schema/provenance implementation;
- Acting-As/debt runtime;
- public HTTP/payment routes;
- production writer wiring;
- real auth/RBAC;
- payment button/action;
- ledger cutover;
- schema migration/backfill.

## P5 semantics

P5 remains fail closed.

DUE != PAYABLE.

P5 proves:
- strict Internal persistence input boundary;
- immutable snapshot and terms provenance checks;
- full confirmed-quantity equality;
- server-side P calculation boundary;
- warehouse scope and CONFIRM_PAYMENT permission checks in isolated context;
- no browser-supplied P;
- no public/production action wiring.

## Owner decisions still open

P5 source explicitly leaves owner decisions open for:
- exact payment-ready technical status;
- real actor/RBAC authority;
- canonical warehouse terms source;
- revision/supersede rules;
- canonical client identity;
- legacy/new balance cutover.

Therefore this slice is suitable as an isolated core/read/action-boundary component, NOT as authorization for a live payment action.

## Release boundary

NO MERGE.
NO DEPLOY.
NO PRODUCTION PAYMENT ACTION.
NO DB MIGRATION.
NO PUBLIC ROUTE.

Exact-head GitHub/Linux CI is required before Release Control acceptance.
