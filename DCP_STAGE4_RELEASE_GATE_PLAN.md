# DCP Stage 4 — Admission freeze / paired release gate plan

PLAN ONLY / ISOLATED EVIDENCE / NO PRODUCTION AUTHORIZATION.
Accepted parent: PR #34 `e8d45e2f46bda0267b8595283befb704786142a9`.
Fresh GitHub release base: `probnik-runtime-20261005` @ `74ce11eb864ee4d852c60e9ffdb140ef73a46517`.
The exact final Stage 4 HEAD/tree/CI are recorded in the handoff and PR metadata. No runtime installation, production freeze, merge, deploy or real DCP lookup is performed by this plan.

## 1. Freeze contract

`dp_live_admission` persists OPEN/CLOSED, instance ID, revision, actor, approval digest and audit events. New schema bootstraps CLOSED with one audit event. Repeated initialization, health READY, restart, lease expiry and manual recovery never overwrite admission or open it. This changes the isolated candidate's initial live availability deliberately: a future deployment needs a separate explicit OPEN approval; a server-only release will not silently enable live lookups.

The common broker checks admission inside the same BEGIN IMMEDIATE transaction that can insert a new request or issue a fenced claim. Freeze uses the same SQLite write lock. If CLOSED wins, no enqueue/claim escapes; if enqueue/claim won before CLOSED, its row is preserved. CLOSED holds pending work, does not expire pending on a denied call, and blocks new claims. Stage 2 lease quarantine may still mark an expired claimed operation orphaned; this is uncertainty isolation, not cancellation or retry. A previously issued valid fenced claim can atomically complete while CLOSED.

Fresh verified cache hits remain available in the existing cache-first layers of Probnik, Procenka and the opt-in OEMixi shared gateway. Freeze only controls broker-backed live admission; it does not assign dealer TTL or change pricing/rounding/cards. The not-yet-proven installed OEMixi default/direct path is not switched by this patch. Proving all deployed consumers use this broker remains an integration prerequisite.

## 2. Control authority and audit

There is no public/admin HTTP freeze route. `dp_live_admission.py` supplies a trusted administrative CLI and function. Read-only plan is the default; apply requires an explicit signed approval file plus dedicated approval key provisioned by Release Control. The key is separate from producer signatures, Telegram credentials and consumer scopes. Provisioning/key custody is a future owner/RC decision; no production key is read or created here. Test key/approvals are isolated mocks only.

Approval fields: version=1, authority=release_control, exact open/close scope, actor, exact plan SHA, issued_at, expires_at (maximum 10 minutes), and reviewed independent evidence SHA-256. HMAC signs canonical exact approval bytes under its separate domain. The plan binds absolute DB path, unique admission instance, current mode and revision, and target mode. Apply revalidates the plan under BEGIN IMMEDIATE; stale/replayed/cross-instance approvals fail closed. Every mode transition and its audit record share one transaction; insertion/update/COMMIT failure rolls back everything. Raw keys or claim secrets never enter audit.

OPEN requires its own newly signed RC approval and an atomically empty unfinished queue. Approval signature proves RC authorization, not physical browser inactivity: RC must independently review the referenced evidence before signing. CLOSED requires a scoped signed approval as well. No actor can gain authority merely by declaring a role string; producer credentials cannot authenticate the administrative change.

## 3. Read-only quiescence preflight

`dp_live_quiescence.preflight()` opens SQLite URI mode=ro, PRAGMA query_only=ON and one read transaction. It reports admission mode/revision, pending/claimed/orphaned/other counts, request IDs, producer ownership, generation, token hash only, leases, health/circuit, historical last claim, check time and canonical snapshot digest. It does not initialize schema, expire requests, quarantine, revoke, retry, reset or change admission.

Required DB state for a paired switch/rollback review: CLOSED; no unfinished requests; READY within existing health freshness contract; no effective circuit block. A missing/incomplete DB or schema is BLOCKED. SQLite cannot prove process/task identity, producer singleton, browser inactivity or outcome of an uncertain HTTP delivery.

Absent independent operator evidence: BLOCKED / MANUAL EVIDENCE REQUIRED, even with an empty queue. A valid evidence reference must bind the exact snapshot digest, named independent operator, fresh observation (5-minute envelope), independent artifact SHA and explicit confirmations for producer stopped, singleton/no other producer, zero browser and result-delivery operations, and reconciled uncertainty journal. Supplied booleans are attestation claims only: the result is READY_FOR_RELEASE_CONTROL_REVIEW, never execution permission or automatic runtime proof. RC must inspect the referenced artifact and exact revisions.

Pending/claimed/orphaned work blocks OPEN and release review. There is no automatic cancellation/requeue or pending cleanup mechanism in Stage 4. If CLOSED catches pending work, return BLOCKER and request a separately approved handling decision; do not delete or silently drain it. Stage 2 manual orphan revocation remains separate and does not unfreeze admission.

## 4. Isolated paired rehearsal

Mock RC OPEN -> broker live enqueue -> v2 fenced claim -> signed CLOSED during fake lookup -> reject new misses/new claims -> preserve fresh cache hits -> complete the existing claim -> empty DB queue -> signed v1 claim denied -> signed v2 GET smoke returns request=null while CLOSED -> separate signed mock RC OPEN.

Tests exercise real SQLite write locks, actual HTTP handlers, actual producer v2, fake browser and actual three adapter functions. Both transaction orderings for freeze/enqueue and freeze/claim are covered. A disposable SQL DP-write probe counts one server DP update and zero consumer price writes; usage metadata updates are distinct. No second browser lookup on lost ACK after COMMIT. Uncertain journal, orphan, unreconciled rollback, CF/AUTH and restart cases remain blocked or stopped. This is MOCK PASS, not deployed/runtime/live DCP evidence.

## 5. Release composition / conflict matrix

Fresh GitHub comparison: PR #34 is 4 commits ahead of current release base and 0 behind; merge base is exactly `74ce11eb...`.

| Candidate | Exact HEAD / ancestry | Decision |
| --- | --- | --- |
| Release base | 74ce11eb864ee4d852c60e9ffdb140ef73a46517 | Fresh GitHub identity only; do not infer a fresh Railway runtime check |
| PR #32 | 2ed283ac68ac1cfbc1252bd9120f98d7d9494fd5; includes first isolated commit 5b09d4ab37547d800c21ffcc254574f68a80ad99 | Accepted Stage 1 ancestor, Draft; do not independently double-merge |
| PR #33 | 3325f30925ddbb662507cc95b6322625098bb90f; direct parent #32 | Atomic/fenced Stage 2 ancestor, Draft |
| PR #34 | e8d45e2f46bda0267b8595283befb704786142a9; direct parent #33 | Producer/single-writer Stage 3 ancestor, Draft |
| Stage 4 | Direct parent exact PR #34; final SHA in handoff | Review ONE stacked release candidate/tree on current base; all inherited guards + exact CI |
| PR #31 WOPS | ab04b1a1a2f44d4defa454a71fa6b9e8eab4e5a2; separate branch from release base | HOLD / excluded / composition not tested |

PR #33 adds 15 files relative to #32; PR #34 adds/changes 14 relative to #33. The new Stage 4 broker patch is bounded; `web_app.py`, `extremizer_bot.py`, producer and FinanceEngine bytes are unchanged relative to accepted #34. Preserve existing WEB ADMIN and Procenka byte-scope guards and finance/checkout regressions; no whole-file resolution or guard reset.

Path intersections are overlap risks, not claims of a performed textual conflict resolution:

| Intersection | Shared paths | Requirement |
| --- | --- | --- |
| DCP stacked candidate vs WOPS #31 | .github/workflows/dcp-health-core-ci.yml; _prod_finance_switch_regression.py; dp_sync_agent.py; extremizer_bot.py; oemixibot_dcp_price.py; web_app.py | Separate future composition/producer protocol/source review; retain both relevant guards; do not include now |
| Stage 4 delta vs WOPS delta | .github/workflows/dcp-health-core-ci.yml | Workflow overlap only in this delta; inherited stack overlaps remain |
| Server v2 vs installed v1 agent | next/result signature + fencing contracts | Paired revision gate; no fallback to v1 |
| Shared gateway vs installed OEMixi | Existing default direct path vs opt-in broker | Runtime NOT PROVEN; separate transport/auth/config/TTL gate |

Release plan: select the final Stage 4 stack as one reviewed candidate against a freshly rechecked release base; if base moves, compose/rebase isolated and rerun exact CI. Do not merge #32/#33/#34 separately or automatically include any other theme. Resolve only reviewed overlapping hunks in any later composition, never replace whole common files. No merge is authorized now.

## 6. Operator evidence checklist

| Gate | Required evidence | Current Stage 4 status |
| --- | --- | --- |
| Server revision | Exact approved final candidate SHA/tree, CI, later actual deployed SHA | Candidate only; no deploy |
| Agent revision | Exact source bundle manifest, Windows Python/dependency versions and approved install package hashes | Source manifest below; installer/runtime dependencies NOT PROVEN |
| Installed producer | Read-only process/task identity, launch path/source hash, producer ID, boot time; current vs proposed revision | Not inspected again in Stage 4; no installation/change |
| Singleton | Old producer stopped after separate permission, no second task/process, Windows mutex evidence | Runtime NOT PROVEN |
| In-flight | Independent zero browser lookup/result delivery evidence; journal reconciliation; DB snapshot digest | MANUAL EVIDENCE REQUIRED |
| Recovery authority | Named RC-approved operator, scoped approval signing/key custody, audit retention, evidence refs | Owner/RC decision required |
| Backup/restore | Fresh authorized Online Backup, size/SHA/integrity/storage read-back, disposable restore; later business-write reconciliation | Not executed in Stage 4 |
| Health smoke | Existing health contract + signed v2 next on CLOSED; no lookup, no browser control | Mock PASS only |
| Admission OPEN | Fresh scoped signed RC approval, empty queue, independently reviewed runtime evidence | No production approval or toggle |
| Live E2E | Separate OEM/request budget/window/browser-read consent; one producer/lookup, atomic cache and consumer proof | Not authorized/performed |
| OEMixi | Actual runtime/start/source, dedicated gateway transport/auth/scopes, explicit dealer freshness | Runtime NOT PROVEN; dealer TTL not assigned |

Producer source SHA-256 remains `a63b45406261e7d23fc9d0c782088a9f3874d66b2e1d2e930e54b1c12bff0895`. Stage 4 changes a shared dependency: the agent source bundle now includes `dp_live_admission.py`; do not reuse the seven-file Stage 3 manifest or install one file alone.

| Pinned source | SHA-256 |
| --- | --- |
| dp_sync_agent.py | a63b45406261e7d23fc9d0c782088a9f3874d66b2e1d2e930e54b1c12bff0895 |
| dp_sync_protocol.py | 356a245695c2190d97cf3af762fd78a34871b89fc0840c473ed0d0c0111a3576 |
| dp_result_completion.py | 786175eae393ee9104b1efbbc4b3e9ecb11a0c14723edf79b24e15b96d180c68 |
| dp_live_bridge.py | ddef8c57095abe929bfbf9bdf4199f002dc0b4bf81ff947ceb1ba91ca0af7277 |
| dp_live_health.py | e3f913c32694321b50a3189c699cae02a41120e65fef2ded16a9b757e11af4f6 |
| dp_live_admission.py | 916c1e9ae15bedbee3856e84c0155fa5dc74791ec791bedee545b9c75f78eed2 |
| oemixibot_dcp_price.py | 118e3be7f799a4c58939f34bbbaaea2081c2a8a548b27630cdd30bf2e5c25465 |
| dealercostparts_manufacturer_finder_v6_6.py | 9b47d72fa2e7d1635acca681d1c75c52e14ee8e4bfa38103a4836364d526306f |

Canonical sorted JSON path->SHA manifest digest: `2a6334d2e03f2f81d2aeca3c088ae4ac9bbdc536d13920cfe41b615cf6ff8b5a`. This is a source manifest, not a Windows installer or Python dependency hash.

## 7. Future paired execution and rollback boundaries

This Stage 4 plan supersedes only the admission-window gap in the historical `DCP_STAGE3_ROLLOUT_PLAN.md`; its other paired approval/rollback boundaries stay intact.

After separate authorization, close admission on the running compatible server with an exact signed plan. Since current production base lacks this new mechanism, bootstrap deployment of the v2 server is its own gate: coordinate existing v1 admission/producer before switch, never assume the new freeze protects old code. Prove no in-flight old operation, verify backup, and obtain server/old-agent retirement permissions. The new server initializes CLOSED. Recheck actual runtime/source/schema and raw admission state; install/start approved single v2 bundle only after separate permission. A CLOSED v2 smoke returns no work. Independently reconcile pending/orphan/journal evidence, then obtain new signed OPEN and separate live E2E GO. Cache-first availability is not permission to reopen live access.

Rollback first preserves CLOSED and journal/claims. Any unfinished v2 request, uncertain delivery or missing independent browser proof blocks v1 rollback. The old server has no admission gate; removing the new server cannot be used as an implicit OPEN. Prefer fail-closed v2 hold; any paired downgrade or DB restore requires separate RC approval, fence reconciliation and later business-write accounting. Do not discard schema/audit, reset revisions, delete requests or replay old claims.

FINAL: READY FOR RELEASE CONTROL REVIEW (isolated evidence only). Production gates remain HOLD. STOP after handoff; no Stage 5 or rollout.
