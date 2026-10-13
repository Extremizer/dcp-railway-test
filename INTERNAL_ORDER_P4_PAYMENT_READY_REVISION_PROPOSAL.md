# INTERNAL ORDER P4 — PAYMENT-READY / REVISION DESIGN PROPOSAL

Status: OWNER DECISION REQUIRED. This document is a design proposal only.

## 1. Payment-ready transition

Business state already approved conceptually:

`Собран, не оплачен`.

Recommendation:

1. all Internal lines have final active confirmed quantities;
2. warehouse terms for those quantities are resolved;
3. server calculates C and P;
4. authorized warehouse terms confirmation occurs;
5. immutable payment snapshot + terms-integrity proof are created atomically;
6. order is considered payment-ready.

P4 does NOT change the current technical `orders.status` value.

Owner/Theme 4 must approve the exact technical status enum and transition.

## 2. Terms integrity proof

New P4 snapshots receive a SHA-256 fingerprint over canonical ordered line data:

- order_item ID;
- offer_source;
- warehouse_id;
- OEM;
- quantity;
- customer_unit_rub;
- payment_route.

Fingerprint version:

`order-items-v1`.

Payment confirmation recalculates this proof inside the same
`BEGIN IMMEDIATE` transaction before any payment-state or finance write.

Any mismatch:

`INTERNAL_ORDER_TERMS_DRIFT`.

Legacy P2/P3 snapshots without proof remain readable but are not payable after
P4:

`INTERNAL_TERMS_INTEGRITY_PROOF_REQUIRED`.

No automatic backfill is permitted.

## 3. Revision / supersede proposal

Owner decision remains open.

Recommendation for future revision model:

- never UPDATE an old immutable snapshot;
- create a new snapshot version;
- preserve old C/P/proof/provenance;
- mark prior payment intent superseded;
- only latest non-superseded version can be paid;
- payment confirmation references exact snapshot version;
- old confirmation IDs cannot migrate to a new version;
- corrections/history remain append-only.

Suggested conceptual fields only:

- snapshot_id;
- order_id;
- revision_no;
- supersedes_snapshot_id;
- status ACTIVE/SUPERSEDED;
- terms_fingerprint;
- created_at/created_by.

These field names are NOT approved schema.

## 4. What triggers a revision

Possible reasons requiring owner approval:

- warehouse changes confirmed quantity;
- warehouse terms version changes;
- client composition changes before payment;
- warehouse_order_number correction;
- payment route administrative exception.

P4 does not implement any revision.

## 5. Payment intent invalidation

Recommendation:

when terms change before payment, existing unpaid payment intent becomes invalid
before a new snapshot is activated.

No automatic reuse of a payment proof or confirmation across revisions.

## 6. Post-payment changes

P4 does not define post-payment correction/refund semantics. Existing v1.4 rule
remains: financial history is append-only; corrections are new events.
