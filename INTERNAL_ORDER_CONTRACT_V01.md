# AUTHORITATIVE INTERNAL ORDER CONTRACT v0.1

Status: P2 ISOLATED PROTOTYPE. No production wiring, migration or WEB route is
authorized by this contract.

## 1. Principle

Internal Order Core v0.1 extends the existing shared Order Core. It does not
create a second OrderEngine.

Existing shared tables remain authoritative for facts they already own:

- `orders.order_id` — EXTREMIZER order identifier;
- `orders.status` — current shared order status;
- `order_items` — persisted line snapshots, including `offer_source`,
  `warehouse_id`, quantity, `customer_unit_rub`, and `payment_route`.

The prototype adds an immutable Internal payment snapshot only for facts that
are not yet represented authoritatively in the current shared schema.

## 2. Current schema gaps

The verified shared order schema does not yet provide all target Internal facts:

- target Internal Client ID is not a canonical `orders.client_id` field;
  historical orders are still owned by `telegram_user_id`;
- `warehouse_order_number` is not a canonical current order column;
- partner amount `P` has no approved Internal Order source;
- order payment status is not independent from the general order status.

Legacy `warehouse_partner_settlements` MUST NOT be used automatically as the
source of `P`.

## 3. v0.1 extension tables

### internal_order_payment_snapshots_v01

One immutable payment-ready snapshot per Internal order.

Fields:

- `order_id` — PK, existing shared order ID;
- `warehouse_id` — the one partner warehouse for this Internal order;
- `warehouse_order_number` — nullable warehouse-assigned number;
- `client_id` — approved Internal Client ID supplied by trusted server context;
- `customer_amount_rub` — immutable C;
- `partner_amount_rub` — immutable P;
- `payment_route` — immutable route snapshot;
- `order_status_snapshot` — shared order status when payment snapshot was made;
- `created_by`, `created_at`.

This table is not a second order table. It is a payment/terms snapshot attached
to the shared `orders.order_id`.

Creation is strict-idempotent: an exact repeat returns the same snapshot;
different terms for the same order are rejected. v0.1 does not invent a
snapshot-revision business rule.

### internal_order_payment_state_v01

Separate payment state for the Internal order:

- `DUE`;
- `PAID`.

It stores the successful confirmation ID, confirmed full client amount, actor
provenance and confirmation time. Payment state does not replace
`orders.status`.

### internal_order_audit_v01

Append-only server audit for Internal payment commands:

- actual actor;
- effective subject;
- warehouse scope;
- order;
- action;
- idempotency key;
- immutable JSON details;
- timestamp.

## 4. Authoritative C

v0.1 derives C from the persisted shared `order_items` snapshots:

`SUM(customer_unit_rub * quantity)`.

Snapshot creation fails unless:

- the order exists;
- it has items;
- all items are `offer_source='warehouse'`;
- all items belong to exactly one warehouse;
- all quantities are positive;
- all `customer_unit_rub` snapshots are present;
- all persisted line payment routes are identical.

No browser amount is accepted as C.

## 5. Authoritative P

P is not derived from legacy partner settlements.

For v0.1 P must be supplied by a trusted server-side Internal terms/warehouse
confirmation context when the immutable payment snapshot is created.

If no snapshot containing P exists, payment confirmation fails closed with
`INTERNAL_PAYMENT_SNAPSHOT_REQUIRED`.

Future real wiring must identify the server workflow that creates P from
warehouse-confirmed quantities/terms before this prototype can become active.

## 6. Client identity

Target architecture is Internal Client ID.

Because the current shared `orders` schema does not yet contain canonical
`client_id`, v0.1 stores the trusted Internal Client ID in the immutable
Internal payment snapshot.

This is a compatibility bridge, not an authorization implementation and not a
migration of historical Telegram ownership.

## 7. warehouse_order_number

The canonical technical name is `warehouse_order_number`.

v0.1 snapshots it when supplied by trusted server context. It is optional.
No current production order-schema migration is performed.

## 8. Direct payment confirmation command

Prototype command:

`confirm_internal_direct_payment(...)`

Server sequence, one caller-owned SQLite transaction:

1. load existing shared order;
2. load immutable Internal payment snapshot;
3. require route `DIRECT_PARTNER`;
4. verify current shared order status still equals
   `order_status_snapshot`;
5. validate server-provided warehouse actor context and exact warehouse scope;
6. require server-confirmed payment;
7. require confirmed amount equals C exactly;
8. change Internal payment state `DUE -> PAID`;
9. call `warehouse_balance_v14.confirm_direct_payment_and_accrue(..., conn)`;
10. call `client_wallet_info_v14.record_direct_payment(..., conn)`;
11. append Internal audit event;
12. COMMIT.

Any error causes the caller transaction to ROLLBACK completely.

The test actor context is only an input contract. It is NOT real warehouse
authentication.

## 9. Direct payment idempotency

A confirmation ID identifies one immutable confirmation.

Exact replay must preserve:

- order;
- warehouse;
- client;
- full amount C;
- actual actor;
- effective subject;
- route.

Exact replay returns the same durable result and creates no second:

- payment-state transition;
- `+(C-P)`;
- zero-RUB client INFO event;
- audit event.

A conflicting replay is rejected without modifying prior history.

## 10. Order status

P2 does not invent a new general order-status transition.

The current shared `orders.status` remains authoritative. The immutable
payment snapshot stores the status seen when the order became payment-ready.
Direct payment confirmation fails if current status differs, forcing a future
explicit status-transition contract instead of silently confirming stale terms.

Payment state is separate from order state.

## 11. ADMIN exception contract — CLIENT -> EXTREMIZER

This is an isolated contract only; no real order wiring is authorized.

Required server sequence:

1. load immutable Internal payment snapshot;
2. require authorized ADMIN context;
3. use that order's exact warehouse and P;
4. reserve the FULL P from that warehouse's EXTREMIZER balance in the same
   serialized transaction;
5. reject if available balance < P;
6. reject partial reservation and mixed funding;
7. only after the full reservation exists, resolve route to
   `EXTREMIZER_BALANCE`;
8. client cannot request or select this route;
9. later real payment/settlement command must use the reservation proof and
   must not reserve/spend the same P twice.

The P2 prototype may prove reservation eligibility, but does not mutate a real
production Internal order route and does not perform real client payment.

## 12. Historical compatibility

No historical `orders`, `order_items.payment_route`, finance events or
partner-settlement rows are rewritten.

USA orders do not use this Internal service.

## 13. Integration blockers before real API/server wiring

1. canonical Internal Client ID linkage into shared Order Core;
2. authoritative server workflow that creates P;
3. real warehouse employee authentication and warehouse-scoped permissions;
4. approved shared order statuses that are payment-confirmable;
5. canonical `warehouse_order_number` persistence;
6. decision on snapshot revision when warehouse terms change before payment;
7. integration ownership between new Internal balance domain and legacy
   `warehouse_partner_settlements`;
8. real API route and CSRF/session/Acting-As composition.

Until those are resolved, v0.1 remains isolated/disposable-SQLite only.
