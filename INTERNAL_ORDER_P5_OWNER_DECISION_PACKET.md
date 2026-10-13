# INTERNAL ORDER P5 — OWNER DECISION PACKET

Status: recommendations only. No OWNER DECISION is asserted here.

| # | Topic | CURRENT FACT | RECOMMENDATION | OWNER DECISION | CONSEQUENCE | BLOCKER |
|---|---|---|---|---|---|---|
| 1 | Exact payment-ready technical status | Current shared statuses include legacy `new/working/waiting/confirmed/executing/completed/cancelled`; no approved exact mapping to Internal «Собран, не оплачен» | Add an explicit technical Internal payment-ready state or an unambiguous mapped state only after final composition/terms confirmation | OPEN | Eligibility can use one stable server status | Real action eligibility cannot hard-code production status |
| 2 | Who may confirm P | P4/P5 use mock authorized warehouse context only | Warehouse member with explicit terms-confirm/CONFIRM_PAYMENT-class permission; ADMIN Acting-As retains actual actor | OPEN | Defines trusted caller for terms command | No real auth/RBAC |
| 3 | Warehouse terms source | P5 proves versioned source type/ref + per-OEM unit terms | One approved versioned warehouse terms source per confirmation; preserve immutable evidence | OPEN | P becomes reproducible/auditable | Production source registry not chosen |
| 4 | Partial confirmed quantities | P5 prototype requires confirmed qty == payment-ready order qty | Resolve partial confirmation into final active composition before payment-ready snapshot | OPEN | Avoids paying stale/rejected qty | Composition/revision workflow missing |
| 5 | Revision/supersede | Snapshots are immutable; no revision engine | New immutable revision, old snapshot superseded, old payment intent invalidated | OPEN | Safe terms changes pre-payment | Schema/transition not approved |
| 6 | Old unpaid snapshots without fingerprint/provenance | P2/P3 rows can be readable; P4/P5 mutation proof missing | Keep nonpayable until explicit re-confirmation/migration; never automatic backfill | OPEN | Prevents fake historical proof | Legacy unpaid order handling |
| 7 | Canonical client ID | Existing shared order owner is Telegram ID; P2 snapshot carries client_id | Add canonical Internal Client relation to shared Order Core for new Internal orders | OPEN | Cross-check snapshot owner against order owner | Identity migration/design |
| 8 | warehouse_order_number | P3/P4 read contract supports nullable value | Warehouse-assigned, nullable until known, unique within warehouse; audited correction | OPEN | Stable role-relative order number | Shared schema/correction policy |
| 9 | Warehouse auth/RBAC/CONFIRM_PAYMENT | Only mock contexts exist | Exact warehouse membership + read/terms/payment permissions; Acting-As is permission intersection, not bypass | OPEN | Can create trusted server contexts | Real auth/RBAC absent |
| 10 | New balance vs legacy settlements | New `warehouse_balance_v14` and legacy settlement ledger are separate | Explicit cutover/reconciliation; new ledger forward source of truth after approved cutover | OPEN | Prevents double counting | Migration/reconciliation decision |

## Important P5 rule

Until decisions 1/2/3/9 are closed, a production payment-action adapter cannot
truthfully return PAYABLE even when `payment_status=DUE`.
