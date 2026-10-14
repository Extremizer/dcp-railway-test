# INTERNAL ORDER P5 — SHARED PERSISTENCE / TRUSTED TERMS AUDIT

Status: ISOLATED P5 EVIDENCE. No production writer, route, ledger, auth or deployment change.

## 1. Shared order persistence inventory

| WRITE PATH | SOURCE OF TRUTH | INPUT VALIDATION | TRANSACTION OWNER | RISK | REQUIRED ADAPTER |
|---|---|---|---|---|---|
| `extremizer_bot.save_order_to_history()` | Client checkout cart plus server pricing/warehouse snapshots; writes shared `orders`, `order_items`, fulfillment groups | Existing runtime normalizes/coerces values; notably `qty = int(...)` and warehouse `customer_unit_rub = int(round(float(price_snapshot_rub)))` | Self-owned `BEGIN` when called alone; caller-owned when `conn` is supplied | Implicit coercion is incompatible with P5 strict Internal input contract. Direct reuse with untrusted Internal values can silently change types/values | REQUIRED before future Internal integration: strict server-side Internal persistence adapter that validates exact int RUB/quantity/warehouse/route before this writer. P5 does not patch the writer |
| `client_checkout_orchestrator.persist_client_checkout_atomic()` | Prepared user/cart plus shared persisted line snapshots | Envelope checks: callable writer, adapter type, order/client/cart presence. Domain finance validation occurs after persistence | **Canonical client checkout transaction owner:** `BEGIN IMMEDIATE -> save_order_fn(conn) -> finance bridge -> COMMIT` | Must remain the one shared checkout transaction; creating a second order writer/engine would fork truth | Reuse transaction boundary; future Internal adapter must enter before persistence, not replace this orchestration |
| `client_checkout_callback_adapter.execute_prepared_checkout()` | Already-prepared checkout facts | Requires prepared inputs and manager notification; delegates durable core | No independent SQL transaction; delegates to `persist_client_checkout_atomic()` | Post-commit side effects cannot authorize or rewrite already committed order financial facts | No Internal payment writer here; keep as orchestration only |
| `client_order_finance_bridge.post_persisted_order_charge()` | Persisted `orders.telegram_user_id` and `order_items` finance snapshots | Verifies order owner, source, route, positive quantity/non-negative RUB; legacy helper parses numeric snapshots through Decimal/string compatibility | Caller-owned transaction | It is a finance bridge, not a trusted Internal terms creator. It must not become a P source | None for P creation; retain existing client checkout role |
| `internal_order_core_v01.create_payment_snapshot()` | Persisted C/warehouse/route/order status plus caller-supplied trusted P/client identity | Strict P4 integer/warehouse/order-line validation; immutable conflict check; fingerprint creation | Self-owned `BEGIN IMMEDIATE`, or supplied trusted transaction | **Bypass surface if exposed directly:** function still accepts `partner_amount_rub`. Safe only as an internal primitive called after trusted P calculation | P5 trusted command boundary; never expose this primitive to browser/public route |
| `internal_order_terms_v01.confirm_terms_and_create_payment_snapshot()` | Approved warehouse terms + exact current order items + confirmed quantities | Actor authorized flag, warehouse scope, full item set, exact quantities, approved/source-matching terms, strict integer P | Owns `BEGIN IMMEDIATE`; calls snapshot primitive with same connection | P4 context is a prototype authorization object, not real RBAC; caller could be forged if constructed from HTTP fields | Future server auth adapter must mint trusted context and permission; P5 wrapper demonstrates this boundary |
| `internal_order_p5_boundary.create_trusted_terms_payment_snapshot()` | Same P4 authoritative terms/order facts; **no P input exists** | Requires trusted context + explicit `CONFIRM_PAYMENT`; P calculated server-side | Delegates to P4 single transaction | Isolated proof only; not wired to HTTP/UI/auth | Future real server command adapter after owner decisions |
| Runtime `orders.status` mutations in `extremizer_bot.py` | Existing manager/admin workflow | Conditional SQL, observed `new -> working` | Local connection/commit in runtime handler | Existing business statuses are not proven payment-ready states | Do not map to PAYABLE until owner approves exact technical status |
| `admin_order_apply_service.prepare_order_apply()` | Current shared order + current warehouse stock/price/reservations | Requires `orders.status='confirmed'`, rechecks stock/price under lock | Own `BEGIN IMMEDIATE` for warehouse reservation preparation | `confirmed` is execution-preparation state, not automatically payment eligibility | No payment adapter; keep logistics/stock boundary separate |

## 2. Trusted P creation / bypass analysis

The P4 primitive `create_payment_snapshot()` remains backward-compatible and therefore still accepts `partner_amount_rub` from trusted Python code. P5 does **not** make a breaking API change because that would break accepted P2–P4 tests/callers without owner approval.

The P5 integration rule is instead:

`trusted server context + confirmed quantities + approved terms -> server-calculated P -> immutable snapshot`

The isolated P5 command intentionally has **no `partner_amount_rub` argument**.

Fail-closed conditions proven/required:
- browser-supplied P: impossible at the P5 command signature;
- missing/unauthorized caller: DENY;
- missing `CONFIRM_PAYMENT`: DENY;
- forged/cross-warehouse scope: DENY through context and order-line scope checks;
- missing/partial/different quantities: DENY;
- missing/unapproved/wrong-source terms: DENY;
- replay with changed immutable identity/evidence: conflict;
- second conflicting snapshot: existing immutable snapshot conflict.

Production/public wiring remains blocked until real auth/RBAC and owner decisions exist.

## 3. Provenance integrity

Current P4 chain:

`internal_warehouse_terms_v01 -> internal_terms_confirmation_audit_v01 -> internal_order_payment_snapshots_v01 -> internal_order_terms_integrity_v01`

P5 verifies:
- snapshot `created_by=terms-confirmation:<confirmation_key>` resolves one exact confirmation audit;
- audit order and warehouse must equal snapshot order/warehouse;
- audit calculated P must equal snapshot P;
- immutable confirmed-line evidence must cover exactly the current fingerprinted item-id set;
- OEM and quantity in evidence must equal fingerprinted shared order terms;
- each line total must equal immutable unit P term × confirmed quantity;
- summed evidence P must equal both audit P and snapshot P;
- current order-items fingerprint must still reproduce;
- tampered evidence or missing provenance fails closed.

Historical proof does **not** depend on the current source row remaining present. Deleting/replacing a current `internal_warehouse_terms_v01` row does not erase what the immutable confirmation audit recorded. This is deliberate evidence preservation, not permission to use a revoked source for a new confirmation.

### Current schema limitation

The current P4 snapshot links to the confirmation indirectly through the immutable `created_by` marker. It does not contain a dedicated FK/confirmation column. P5 therefore proves a safe read/action boundary without migrating schema.

Recommended future **versioned immutable terms contract** (OWNER APPROVAL REQUIRED):
- immutable snapshot identity/version;
- exact `terms_confirmation_key`;
- terms version/source type/source ref captured at confirmation;
- immutable line-term evidence or evidence hash;
- order-items fingerprint version/hash;
- revision/supersede relation;
- ACTIVE/SUPERSEDED intent state;
- exact actor/subject/permission provenance.

No migration, revision, backfill or supersede implementation is performed in P5.

## 4. Strict persistence boundary

`validate_internal_persistence_line()` is an isolated pre-SQL contract. It requires:
- exact Python `int` for quantity, warehouse id, item id and integer RUB;
- positive quantity/ids;
- non-negative exact whole RUB;
- `offer_source='warehouse'`;
- valid explicit payment route;
- non-empty order/OEM strings.

Rejected before any SQL:
- bool;
- float;
- numeric string;
- NaN/Infinity (not ints);
- negative values;
- zero quantity;
- implicit coercion.

It also produces a deterministic canonical line fingerprint for future adapter composition. P5 does not write these inputs into the production order writer.

## 5. Payment eligibility contract

P5 introduces a read-only isolated evaluator. It separates historical/read compatibility from action eligibility.

Checks:
1. `payment_status == DUE`;
2. trusted fingerprint exists;
3. current fingerprint matches;
4. trusted terms provenance verifies;
5. payment route is known;
6. exact payment-ready technical status is configured and matches;
7. actor is authorized;
8. actor has `CONFIRM_PAYMENT`;
9. actor warehouse scope equals snapshot warehouse.

**DUE != PAYABLE.**

Unknown/missing reason fails closed. Legacy snapshots remain readable but cannot pass provenance/fingerprint action checks.

No payment button, HTTP action, real auth or mutation is added.
