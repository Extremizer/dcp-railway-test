# EXTREMIZER — Theme 6 → Theme 4 / Stage 9 Safety Closure

**RELEASE HOLD.** Isolated mock-only branch; no live DCP, production, Railway mutation, browser, Windows or cart.

## Synthetic supplier outcomes — actual accepted signed-v2 producer

Focused `test_dcp_stage9_safety_closure.py` invokes the existing `dp_sync_agent.run_once()` using the existing HTTP handler, signed protocol, broker, fenced claim and atomic result completion on a disposable SQLite fixture. Fake supplier returns only `AUTH_REQUIRED` or `CLOUDFLARE` and an unused MSRP decoy. Assertions: one invocation, producer stop latch, no second lookup, request finished with exact denial status, zero dealer price, null cache-commit timestamp, no cache rows, matching broker circuit breaker and no fallback. The server may **safely complete the denial status**; this is NOT an unsafe FOUND completion. No separate runtime completion path was introduced.

## Cross-host fencing matrix — code audit

| Scenario | Existing guard | Race possible? | Evidence | Remaining gap |
| --- | --- | --- | --- | --- |
| Old producer alive / new starts | Shared broker `BEGIN IMMEDIATE`; only one active `claimed` row | YES: both processes may exist and sequentially claim different work after completion | `dp_live_bridge.claim_next` checks active claimed row, not owner deployment | No exclusive producer epoch / host ownership |
| Old owns in-flight / new starts | Active claimed row blocks all new claims | NO simultaneous **new claim** while lease valid; YES concurrent processes | `claim_next` claimed-row check; two-owner fixture | No global stop acknowledgement |
| Old host network-delayed | Old claim remains active then orphaned on expiry; journal prevents unsafe agent restart | NO automatic reclaim of orphan; YES delayed host may remain alive | `_quarantine_claims`, journal and lease test | Must prove old producer cessation, not merely lease expiration |
| New deployment healthy before old exits | No deployment-level admission epoch; Linux `flock` only shared inode | YES | Isolated `LinuxSingleton`, `claim_next` checks per-row fence only | External coordinated rollout ownership missing |
| Old resumes after new took ownership | Exact token/owner/generation protects completion | NO stale completion after claim lost/revoked; YES old process may request a *new* claim | `finish` conditional UPDATE; existing wrong-generation/409 tests | Global producer-incarnation fencing missing |
| Hosts lack shared filesystem/volume | Independent flock locks have no shared inode; broker serializes active requests | YES as producer overlap; NO concurrent active claims at same moment | `LinuxSingleton` local file lock; shared broker transaction | Persistent coordinated owner/epoch across hosts missing |

**Verdict: CROSS_HOST_FENCING GAP REMAINS.** The existing broker guarantees active-claim exclusion and stale completion rejection, NOT exclusive old/new producer lifetime or handover. Minimal missing primitive for future authorization: server-enforced **global producer ownership generation/epoch with atomic acquire, explicit release/revocation and claim admission bound to current epoch**, plus old-host cessation/reconciliation evidence. Do not implement without Theme 4 approval. A merely healthy new deployment, expired lease or isolated local flock does not establish safe first switch.

## Boundaries

No change to accepted exact manufacturer/OEM fixture identity guard, Theme 1 Category Routing or TTL. No automatic distributed lock or new coordinator, broker, cache writer or PriceEngine. Check exact GitHub CI HEAD before claiming PASS. After handoff STOP.
