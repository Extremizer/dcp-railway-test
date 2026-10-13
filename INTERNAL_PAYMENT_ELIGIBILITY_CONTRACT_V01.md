# INTERNAL PAYMENT ELIGIBILITY CONTRACT v0.1

Version: `internal-payment-eligibility-v0.1`

Core rule:

**DUE != PAYABLE.**

Read-only evaluator:

`evaluate_direct_payment_eligibility(...)`

It performs no UPDATE/INSERT/ledger mutation.

A direct-partner payment action is eligible only when all are true:

1. payment state is DUE;
2. trusted Internal payment snapshot exists;
3. P4 terms fingerprint exists and matches current protected order_items;
4. trusted terms provenance verifies;
5. payment_route is DIRECT_PARTNER;
6. current orders.status is in an owner-approved payment-ready status policy;
7. status still equals snapshot order_status_snapshot;
8. actor context is authorized;
9. actor warehouse scope equals order warehouse;
10. permission CONFIRM_PAYMENT is present.

Unknown/missing data always returns `eligible=false`.

## Payment-ready policy

P5 does not approve a technical payment-ready status.

The evaluator therefore requires a server-side policy with explicit
`payment_ready_statuses`.

Empty/unresolved policy fails closed.

## Legacy snapshots

P2/P3 snapshots remain readable through `internal-order-read-v0.1`.

Without P4/provenance proof they are not payable.

## Authorization

P5 actor context is mock/disposable only. Real auth/RBAC/Acting-As remains
blocked and must construct the context server-side.
