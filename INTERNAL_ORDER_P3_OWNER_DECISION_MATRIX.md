# INTERNAL ORDER P3 — OWNER DECISION MATRIX

Status: recommendations only. No item below is owner-approved by this document.

| Decision | Options | Theme 1 recommendation | Why | Dependency / risk |
|---|---|---|---|---|
| 1. Payment-ready `orders.status` | Any existing status; dedicated payment-ready state; status + invariant checks | Use a dedicated business state corresponding to owner-approved «Собран, не оплачен» before immutable payment snapshot creation | C/P/composition should be final enough for payment; prevents snapshots while still assembling | Exact technical enum mapping to current shared status is not yet approved |
| 2. Who confirms P / when | Warehouse user during line confirmation; warehouse user after all lines confirmed; ADMIN; automatic calculation | Warehouse authorized user confirms final quantities/terms after all lines processed; system calculates P from those confirmed terms and creates snapshot atomically | Warehouse owns partner terms; avoids ADMIN manually retyping P | Requires warehouse employee identity, confirmed-line model and terms source |
| 3. P changes before payment | Edit snapshot; revision/supersede; cancel/recreate order | Revision/supersede: append new immutable snapshot version only while unpaid; preserve old version/audit; invalidate old payment intent | Preserves audit without forcing whole-order recreation | Current v0.1 has one immutable row and intentionally blocks mutation; version schema needs owner approval |
| 4. Internal Client ID ↔ shared orders | Put `client_id` directly on orders; separate link table; keep only payment snapshot | Canonical immutable `orders.client_id` (or canonical order-owner relation) for new orders; migration/link table for historical Telegram orders | Identity is order ownership, not only finance metadata | Requires Internal Client ID/auth rollout and historical migration policy |
| 5. `warehouse_order_number` source/uniqueness | Free text; unique per warehouse; globally unique | Entered by warehouse, optional until assigned, unique within `warehouse_id` for non-null values; preserve change audit if correction is allowed | Warehouse systems can reuse numeric sequences across warehouses | Need correction policy when warehouse entered wrong number |
| 6. Warehouse membership / CONFIRM_PAYMENT / Acting-As | broad warehouse role; explicit permission; admin bypass | Explicit warehouse membership + `CONFIRM_PAYMENT`; Acting-As requires real ADMIN + effective warehouse user scope + critical action audit; no bypass | Matches v1.3/v1.4 dual-actor rule and cross-warehouse isolation | Real identity/session/RBAC integration still absent |
| 7. New Internal balance vs legacy settlements | Merge ledgers; new ledger authoritative forward only; migrate history | New `warehouse_balance_v14` ledger becomes source of truth for new Internal balance events from an explicit cutover; legacy settlement ledger remains historical/read-only until a separately reconciled migration | Avoids double counting and semantic collision | Cutover date/reconciliation/migration rules require owner + Release Control approval |

## Additional notes

### Payment-ready status

Recommendation does not rename or mutate current production statuses. It says
only that snapshot creation should happen at the business point already
described by the owner as «Собран, не оплачен». Theme 4 must approve the exact
shared technical enum/transition.

### P confirmation

The recommendation is not “warehouse types arbitrary P”. Preferred target is:
confirmed quantities + approved warehouse terms are server-authoritative; the
system calculates P; authorized warehouse user confirms the final composition
/ terms event.

### P revision

No silent rewrite. If revision is approved later, every payment confirmation
must reference one exact snapshot version.

### Client identity

The P2 compatibility field in the payment snapshot should not become the
permanent sole owner relation for an order.

### Legacy finance

Do not sum `warehouse_partner_settlements` and
`warehouse_internal_balance_ledger` without a formal reconciliation/cutover.
