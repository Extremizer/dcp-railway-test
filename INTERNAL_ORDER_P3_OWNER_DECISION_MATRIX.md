# INTERNAL ORDER P3 — OWNER DECISION MATRIX

**Status:** recommendations only. Nothing in this file is owner-approved automatically.

| # | Decision | Current proven state | Recommendation for owner | Why | Status |
|---|---|---|---|---|---|
| 1 | Payment-ready `orders.status` | P2 snapshots current shared status; confirmation rejects changed status. No new canonical status approved. | Reuse/standardize the shared Order Core status corresponding to **«Собран, не оплачен»** as payment-ready; approve its exact canonical code in Order Core rather than adding a P3-only status. | Keeps one order state machine and avoids a second status vocabulary. | OWNER DECISION REQUIRED |
| 2 | Who confirms P and when | P is currently supplied only by trusted server context at immutable snapshot creation. | Authorized employee of the exact warehouse confirms final quantities/terms and P after assembly/terms are fixed and before client payment is requested. ADMIN may correct only through an audited exception workflow, not silently. | Warehouse is the authoritative source of its payable terms; P must exist before payment. | OWNER DECISION REQUIRED |
| 3 | P changes before payment | v0.1 snapshots are immutable; conflicting replacement is rejected. | Use explicit **revision/supersede/cancel**, never in-place update. DUE snapshot may be superseded by a new immutable revision; PAID snapshot is locked and any later difference is a separate correction/refund process. | Preserves auditability and prevents historical terms rewriting. | OWNER DECISION REQUIRED |
| 4 | Internal Client ID ↔ shared orders | P2 snapshot stores trusted `client_id`; shared historical orders still commonly use Telegram ownership. | Add canonical Internal `client_id` linkage to shared Order Core. Keep snapshot client ID as compatibility proof during migration; do not use Telegram ID as business identity. | Needed for durable WEB/client ownership and wrong-client protection. | OWNER DECISION REQUIRED |
| 5 | `warehouse_order_number` source/uniqueness | P2 snapshot accepts a trusted nullable value; shared schema is not canonical yet. | Warehouse side assigns it. Persist canonically in shared Internal order data and enforce uniqueness **within warehouse**: `UNIQUE(warehouse_id, warehouse_order_number)`; allow null before assignment. | Different warehouses can use overlapping numbering; warehouse scope disambiguates. | OWNER DECISION REQUIRED |
| 6 | Warehouse membership / `CONFIRM_PAYMENT` / Acting-As | P2/P3 actor contexts are test contracts only. | Real server auth should require active warehouse membership + exact warehouse scope + explicit `CONFIRM_PAYMENT`. Acting-As should require ADMIN Act-As permission **and** the effective warehouse user's capability/scope/business guards; no privilege bypass. | Matches existing role/capability/resource-scope architecture. | OWNER DECISION REQUIRED |
| 7 | New Internal balance vs legacy settlements | Finance v1.4 new Internal balance is isolated; warehouse history deliberately does not auto-merge legacy partner settlements. | Make new `warehouse_internal_balance_*` domain authoritative for new Internal orders from an approved cutover point. Keep legacy settlements separate/read-only until an explicit reconciliation/migration is approved. Never sum both automatically. | Prevents double counting and ambiguous ownership. | OWNER DECISION REQUIRED |

## Recommended sequencing after owner decisions

1. Approve payment-ready shared status.
2. Approve P authority and revision rules.
3. Approve canonical Internal client linkage.
4. Approve warehouse order-number ownership/uniqueness.
5. Approve membership/capability model.
6. Approve finance cutover from legacy settlements.
7. Only then authorize real server/API wiring.

P3 does not implement any undecided item above as production behavior.
