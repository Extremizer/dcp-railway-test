# THEME 2 — INTERNAL ORDER READ v0.1 / P5 ACTION-ELIGIBILITY DELTA

Base read contract remains:

`internal-order-read-v0.1`

No existing field is renamed or removed.

## Read compatibility

Continue to support:

- nullable `warehouse_order_number`;
- DUE/PAID;
- authoritative C/P from trusted snapshot;
- warehouse-scoped reads;
- posted/reserved/available;
- 3 425 RUB control;
- search/pagination-independent summary.

A legacy P2/P3/P4 snapshot may remain readable.

## New separate server contract

Payment action eligibility is NOT derived from the read row.

New isolated contract:

`internal-payment-eligibility-v0.1`

Conceptual response:

```text
{
  contract_version: "internal-payment-eligibility-v0.1",
  order_id: string,
  warehouse_id: integer,
  eligible: boolean,
  reasons: [
    { code: string, detail?: string }
  ]
}
```

## DUE != PAYABLE

UI must never infer an enabled payment-confirmation action solely from:

`payment_status == "DUE"`.

Eligibility additionally requires server proof of:

- trusted fingerprint present and matching;
- trusted terms provenance;
- direct-partner payment route for direct warehouse confirmation;
- owner-approved payment-ready technical status;
- actor authorization;
- exact warehouse scope;
- CONFIRM_PAYMENT permission.

Unknown/missing condition = `eligible:false`.

## Legacy snapshots

Readable snapshot without P4/P5 proof:

- read contract may display it according to existing compatibility rules;
- payment action eligibility = false.

## No UI change in Theme 1 P5

Theme 1 does not add buttons, routes or WEB2 behavior. Theme 2 should consume
this delta only after a real authorized server endpoint is separately approved.
