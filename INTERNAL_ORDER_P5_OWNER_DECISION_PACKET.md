# INTERNAL ORDER P5 — OWNER DECISION PACKET

Status: OWNER DECISIONS OPEN. Recommendations below are proposals, not approved business rules.

| # | CURRENT FACT | RECOMMENDATION | OWNER DECISION | CONSEQUENCE | BLOCKER |
|---|---|---|---|---|---|
| 1. Exact payment-ready technical status | Existing runtime has statuses such as `new`, `working`, `confirmed`; P4 proposal conceptually uses “Собран, не оплачен”, but no exact technical enum was approved | Add one explicit technical status/state for “composition and quantities final, terms confirmed, snapshot active, unpaid”; do not infer from `confirmed` | **OPEN** | Eligibility evaluator can require an exact server state rather than UI wording | Real payment action wiring |
| 2. Who may confirm P | P4 prototype accepts an authorized terms context; no real identity/RBAC exists | Only authenticated warehouse-scoped actors with explicit `CONFIRM_PAYMENT`, with admin override only if separately approved/audited | **OPEN** | Determines trusted caller boundary and audit actor semantics | Real auth and payment-ready command |
| 3. Source of warehouse terms | P4 prototype uses versioned approved `internal_warehouse_terms_v01` records and source type/ref | Approve canonical warehouse terms source(s), approval owner, validity/revocation semantics, and source-version rules | **OPEN** | Defines what server-calculated P is legally/business-authoritative | New P confirmation |
| 4. Partial confirmed quantities | P4/P5 require confirmed quantities to exactly equal current payment-ready order quantities | Keep fail-closed until an explicit partial composition/revision model is approved | **OPEN** | Avoids paying against a composition different from shared order truth | Partial fulfillment/payment |
| 5. Revision / supersede | Current snapshot is one immutable row per order; conflicting second snapshot is rejected | Adopt append-only versioned snapshots with explicit supersede relation; never UPDATE historical intent | **OPEN** | Enables pre-payment changes while preserving evidence | Any terms/quantity/route correction after snapshot |
| 6. Old unpaid snapshots without fingerprint | P2/P3 snapshots remain readable; P4 payment mutation fails closed without proof | Keep nonpayable; do not auto-backfill. Migrate only through an owner-approved evidence/revision process | **OPEN** | Preserves historical reads without inventing evidence | Paying legacy unpaid Internal orders |
| 7. Canonical client ID | Shared checkout authority currently uses `orders.telegram_user_id`; Internal snapshot stores a string `client_id` | Define one canonical client identity format and server resolver; snapshot exact immutable canonical ID | **OPEN** | Prevents identity mismatch between Telegram/web/future accounts | Trusted snapshot creation integration |
| 8. Canonical `warehouse_order_number` | Read contract allows null; current snapshot stores nullable value | Keep nullable until warehouse assigns one; define uniqueness/scope and whether correction requires snapshot revision | **OPEN** | Avoids inventing numbers while preserving warehouse reconciliation | Warehouse UI/edit semantics |
| 9. Warehouse auth / RBAC / `CONFIRM_PAYMENT` | P5 uses isolated dataclass flags/permission set only; this is not authentication | Real server must derive warehouse id, actor, subject and permission from authenticated session/RBAC; browser must never supply trusted fields | **OPEN** | Converts proof boundary into enforceable security boundary | Any public/server payment action |
| 10. New Internal balance vs legacy settlements cutover | P2–P4 authoritative Internal balance/snapshot path is deliberately independent of legacy partner settlements for P; existing history remains | Define cutover date/scope and reconciliation policy; never silently mix legacy settlement P into new Internal payment intent | **OPEN** | Prevents double authority/double accounting | Production rollout and reconciliation |

## Required owner outcome

P5 can be accepted without answering these decisions because real payment wiring remains blocked.

Before any P6/payment-action implementation, at minimum decisions 1, 2, 3, 5, 7, 9 and 10 must be explicit. Decisions 4, 6 and 8 may remain fail-closed/null if the owner explicitly chooses that behavior.
