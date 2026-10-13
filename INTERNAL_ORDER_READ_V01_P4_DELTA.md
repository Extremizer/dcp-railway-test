# THEME 2 — INTERNAL ORDER READ CONTRACT v0.1 / P4 DELTA

Base contract:

`internal-order-read-v0.1`

Status: field-shape compatible. No WEB2 implementation is performed here.

## 1. Contract version

The outward read contract remains:

`internal-order-read-v0.1`.

P4 does not require Theme 2 to rename existing fields.

## 2. No field removal

Keep canonical fields:

- extremizer_order_number;
- warehouse_order_number;
- warehouse_id;
- client_id;
- order_status;
- payment_status;
- payment_route;
- customer_amount_rub;
- partner_amount_rub;
- partner_amount_source;
- balance_rub.

## 3. warehouse_order_number

`warehouse_order_number` remains nullable.

Theme 2 typed adapter must accept:

`string | null`.

Do not substitute EXTREMIZER order ID or warehouse_id when it is null.

## 4. P source

Trusted P remains:

`partner_amount_source = "internal_payment_snapshot"`.

If no authoritative snapshot exists:

- partner_amount_rub = null;
- partner_amount_source = null.

No UI fallback to legacy settlements or fixture values outside DEMO MODE.

## 5. P4 integrity semantics

New payment snapshots may have an internal terms-integrity proof.

That proof is server/domain validation and is NOT currently an additional UI
field in `internal-order-read-v0.1`.

Theme 2 must not infer payment eligibility only from `payment_status=DUE`.
Real action availability must later come from an authorized server action
contract.

## 6. Legacy P2/P3 snapshot compatibility

Legacy immutable snapshots without P4 fingerprint remain readable under
`internal-order-read-v0.1`.

They may show trusted historical snapshot C/P, but P4 payment mutation is
fail-closed until a separately approved migration/revision path exists.

Theme 2 should not label read compatibility as "payment-ready".

## 7. Status separation

Keep:

- textual business `order_status`;
- independent `payment_status`.

Do not merge System/business/finance states.

## 8. Security

All strings from future server read model remain data, not trusted HTML.
Theme 2 should continue text-safe rendering for:

- client fields;
- OEM;
- manufacturer;
- item name;
- order status;
- warehouse order number.

## 9. DEMO MODE

Fixture C/P/order numbers are allowed only in explicit DEMO MODE.

Future server integration must preserve null authoritative values instead of
inventing replacements.
