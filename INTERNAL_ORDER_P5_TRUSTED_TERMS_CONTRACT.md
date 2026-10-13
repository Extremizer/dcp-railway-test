# INTERNAL ORDER P5 — TRUSTED TERMS / SNAPSHOT PROVENANCE CONTRACT

Version: `internal-terms-v1`

Status: isolated server contract, no public route.

## Trusted creation boundary

Future payable Internal snapshots must be created only through:

`confirm_terms_and_create_payment_snapshot(...)`

Inputs are server-side facts:

- exact order;
- exact warehouse actor scope;
- confirmed quantities by order_item ID;
- approved terms version;
- approved terms source type/reference;
- trusted client identity;
- optional warehouse_order_number.

There is **no partner_amount_rub argument**.

The server calculates:

`P = SUM(partner_unit_rub * confirmed_quantity)`.

## Low-level compatibility primitive

`create_payment_snapshot(partner_amount_rub=...)` remains available for P2/P4 compatibility and isolated tests.

It is NOT a trusted future integration boundary.

A snapshot created directly does not receive trusted provenance and therefore
must evaluate as NOT PAYABLE in P5 payment eligibility.

This avoids a breaking API change while preventing future adapters from treating
low-level snapshot existence as payment permission.

## Immutable chain

P5 creates and verifies:

`internal_warehouse_terms_v01`
→ `internal_terms_confirmation_audit_v01`
→ `internal_order_payment_snapshots_v01`
→ `internal_order_terms_integrity_v01`
→ `internal_order_snapshot_terms_provenance_v01`.

The provenance record binds:

- order_id;
- confirmation_key;
- contract version;
- warehouse_id;
- client_id;
- nullable warehouse_order_number;
- terms_version;
- source type/reference;
- calculated P;
- SHA-256 of immutable confirmation evidence;
- order-items fingerprint version/SHA.

## Evidence proof

Confirmation evidence contains per line:

- order_item_id;
- OEM;
- confirmed quantity;
- partner unit RUB;
- partner line RUB.

Verifier recomputes evidence arithmetic and SHA-256 and checks:

- audit order/warehouse/key/source/version;
- audit P == sum of evidence;
- snapshot P == audit P;
- snapshot client/warehouse/order number == provenance;
- snapshot `created_by` references same confirmation key;
- stored order-items fingerprint == provenance fingerprint.

## Terms source changes after confirmation

Historical proof deliberately does NOT re-read current
`internal_warehouse_terms_v01` values.

Therefore a later approved-price change, source withdrawal, or `approved=0`
does not rewrite or silently invalidate the already confirmed payment intent.

The original confirmation evidence and provenance remain verifiable.

A new order/payment intent must use the currently approved terms and a new
confirmation key/version according to future revision rules.

## Fail closed

Missing or inconsistent:

- provenance link;
- confirmation audit;
- integrity fingerprint;
- evidence hash;
- snapshot linkage;

means future payment eligibility is false.

No legacy partner settlement can repair missing provenance.
