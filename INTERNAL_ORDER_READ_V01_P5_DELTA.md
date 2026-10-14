# THEME 2 — INTERNAL ORDER READ v0.1 / P5 VERSIONED DELTA

Base outward contract remains:

`internal-order-read-v0.1`

Status: READ-COMPATIBLE. No WEB2 UI/HTTP/auth code is changed by Theme 1 P5.

## Read compatibility — unchanged

Keep:
- `extremizer_order_number`;
- nullable `warehouse_order_number`;
- exact `warehouse_id`;
- `client_id`;
- textual `order_status`;
- independent `payment_status = DUE|PAID`;
- authoritative `customer_amount_rub`;
- authoritative `partner_amount_rub`;
- `partner_amount_source = internal_payment_snapshot`;
- shared warehouse balance semantics, including the accepted **3 425 ₽** case;
- warehouse isolation;
- results independent of pagination/search mechanics.

Legacy P2/P3 immutable snapshots may remain readable even when they cannot be paid.

## P5 semantic separation

Read compatibility is not payment action eligibility.

Theme 2 must not infer a payment button/action from:
- `payment_status=DUE`;
- existence of C/P alone;
- existence of an order row;
- UI/business status text.

P5 proposes a separate server action-preflight contract, conceptually versioned independently, for example:

`internal-order-payment-eligibility-v0.1`

This is a proposed domain boundary name, not an approved HTTP endpoint.

Minimum server result:
- `payable: bool`;
- exact checked order/warehouse scope;
- structured fail-closed reasons;
- trusted fingerprint presence/match;
- trusted terms provenance result;
- payment route result;
- payment-ready technical-status result;
- actor authorization result;
- execute-permission result.

Unknown/missing checks must produce `payable=false`.

## Security / compatibility

- Do not add payment controls until a real authorized server action exists.
- Do not expose proof internals as editable/browser-trusted fields.
- Do not change ADMIN auth or Acting-As.
- Do not substitute legacy partner settlements for P.
- Do not invent `warehouse_order_number` when null.
- Do not change `internal-order-read-v0.1` field shape solely to represent action eligibility.

Theme 2 can therefore continue Secure Read work independently while Theme 1 payment wiring remains blocked.
