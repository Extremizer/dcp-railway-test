# INTERNAL ORDER READ CONTRACT v0.1

Version: `internal-order-read-v0.1`

Status: P3 ISOLATED / READ-ONLY / NOT AN HTTP API.

Audience: Theme 2 UI integration planning and Theme 4 architecture review.

## 1. Authorization boundary

The service is server-side only.

Prototype contexts:

- `AdminReadContext(actual_actor, authorized)`
- `WarehouseReadContext(actual_actor, warehouse_id, authorized)`

These are test contracts, not authentication.

A future real adapter must construct them only after session/auth/RBAC checks.

Warehouse readers are always exact-`warehouse_id` scoped. ADMIN may request a
warehouse explicitly, but every response still has one exact warehouse scope.

## 2. Strict single-order response

Function:

`read_order(db_file, warehouse_id, order_id, context)`

Requires a trusted immutable
`internal_order_payment_snapshots_v01` row and matching
`internal_order_payment_state_v01` row.

Missing/mismatched authoritative data fails closed.

Response shape:

```text
{
  contract_version: "internal-order-read-v0.1",

  scope: {
    warehouse_id: integer,
    viewer_type: "admin" | "warehouse"
  },

  order: {
    extremizer_order_number: string,
    warehouse_order_number: string | null,
    warehouse_id: integer,
    client_id: string,
    order_status: string,
    payment_status: "DUE" | "PAID",
    payment_route: "direct_partner" | "extremizer_balance",
    customer_amount_rub: integer,
    partner_amount_rub: integer,
    partner_amount_source: "internal_payment_snapshot",
    balance_rub: integer | null
  },

  items: [
    {
      order_item_id: integer | null,
      manufacturer: string | null,
      oem: string | null,
      requested_oem: string | null,
      name: string | null,
      quantity: integer
    }
  ]
}
```

No field in this response is writable through the read contract.

## 3. Warehouse-history response

Function:

`read_warehouse_history(db_file, warehouse_id, context, search, limit, offset)`

Response shape:

```text
{
  contract_version: "internal-order-read-v0.1",

  scope: {
    warehouse_id: integer,
    viewer_type: "admin" | "warehouse"
  },

  summary: {
    warehouse_id: integer,
    posted_balance_rub: integer,
    reserved_balance_rub: integer,
    available_balance_rub: integer,
    direct_payment_accruals_rub: integer,
    applied_balance_rub: integer,
    corrections_net_rub: integer
  },

  rows: [
    {
      extremizer_order_number: string,
      warehouse_order_number: string | null,
      date: string | null,
      order_status: string | null,
      payment_status: "DUE" | "PAID" | null,
      client_id: string | null,
      customer_amount_rub: integer | null,
      partner_amount_rub: integer | null,
      partner_amount_source:
          "internal_payment_snapshot"
        | "direct_payment_confirmation"
        | "warehouse_balance_reservation"
        | null,
      payment_route: string | null,
      internal_payment_snapshot: boolean,
      warehouse_id: integer,
      balance_rub: integer | null
    }
  ]
}
```

## 4. Authoritative precedence

When `internal_order_payment_snapshots_v01` exists for a row, it is
authoritative for:

- warehouse_id;
- warehouse_order_number;
- client_id;
- C;
- P;
- payment_route.

Payment status is authoritative from
`internal_order_payment_state_v01`.

Current shared order status is read from `orders.status`.

The read model validates that the snapshot warehouse matches all warehouse
order lines. Any mismatch fails closed.

## 5. Pre-terms / legacy rows

A warehouse order may exist before an Internal payment snapshot is created.

Such a row may remain visible in historical list context, but the read model
must not invent trusted Internal terms:

- `partner_amount_rub = null`;
- `partner_amount_source = null`;
- `payment_status = null`;
- `client_id = null`.

The strict `read_order()` contract refuses such an order with
`INTERNAL_PAYMENT_SNAPSHOT_REQUIRED`.

No P is loaded from `warehouse_partner_settlements`.

## 6. Five-column financial meaning

For the future role-specific table, the read contract exposes underlying data;
Theme 2/4 controls presentation.

The financial row value remains:

- no posted new-ledger event: `balance_rub = null` (blank);
- direct-payment accrual: historical `+(C-P)`;
- warehouse-balance funded order: historical `-P`;
- later spending does not rewrite prior positive rows.

Warehouse-wide current balance is from `summary`, never from summing the
visible page.

## 7. Search/pagination invariant

Search/limit/offset affect only `rows`.

They never alter:

- posted balance;
- reserved balance;
- available balance;
- accrual/spend/correction totals.

## 8. Theme 2 canonical technical names

Use exactly:

- `extremizer_order_number`;
- `warehouse_order_number`;
- `warehouse_id`;
- `client_id`;
- `order_status`;
- `payment_status`;
- `customer_amount_rub`;
- `partner_amount_rub`;
- `partner_amount_source`;
- `payment_route`;
- `balance_rub`.

Do not introduce UI-specific aliases into the domain/API contract.

## 9. Security invariant

A SKLAD reader for warehouse A must never receive:

- warehouse B order metadata;
- warehouse B financial totals;
- warehouse B OEM/item details.

ADMIN reads still require an explicitly selected warehouse scope.

## 10. Not yet authorized

This contract does not create:

- public HTTP routes;
- production warehouse login;
- production ADMIN route;
- WEB2 UI;
- mutations;
- payment confirmation buttons.
