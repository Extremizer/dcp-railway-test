# DCP Stage 3 paired rollout / rollback plan

STATUS: PLAN ONLY / NO PRODUCTION AUTHORIZATION.
Baseline: PR #33 `3325f30925ddbb662507cc95b6322625098bb90f`, direct parent PR #32 `2ed283ac68ac1cfbc1252bd9120f98d7d9494fd5`.
PR #31/WOPS is excluded. PR #32 and #33 remain Draft. Stage 3 includes their ancestry; do not independently merge all three. Any change in release base requires separately reviewed composition and CI.

## 1. Release identity and approval records

Before execution record the exact reviewed Stage 3 server commit/tree, exact agent bundle revision, SHA-256 of every agent file, Python/dependency revisions and existing installed agent SHA/process/task identity. The handoff records the final Stage 3 HEAD and agent file hash; do not substitute branch-tip names at installation. `dp_sync_agent.py` requires its pinned helper bundle: `dp_sync_protocol.py`, `dp_result_completion.py`, `dp_live_bridge.py`, `dp_live_health.py`, `oemixibot_dcp_price.py` and manufacturer finder plus their dependencies. Installing one file alone is forbidden. Preserve the current installed bundle/task for evidence; no credential values belong in manifests.

Release Control grants separate explicit approvals for server merge/deploy, Windows agent installation/start, optional OEMixi gateway integration, and live E2E. This document grants none. Record actor, time, approved SHA, allowed actions and expiry for every gate.

## 2. Quiescence and old producer exclusion

Agree a short admission window with the owner: no new client/dealer live-DP requests; cache-only offers may continue if separately confirmed not to enqueue misses. There is no newly implemented global admission-freeze switch in Stage 3. If coordination cannot prove no new requests, return BLOCKER and do not switch.

Observe queue counts and exact rows: pending, claimed, orphaned, active browser operation, producer PID/session, health and circuit state. Required before switching: pending=0, claimed=0, orphaned=0 and positively confirmed no in-flight browser/result HTTP operation. Client TIMEOUT and lease expiry do not prove producer inactivity. Do not automatically retry or delete claims. Existing orphan recovery requires its own approved plan SHA, inactive-producer evidence, fresh READY and audit event via the Stage 2 recovery contract.

After separate installation permission, disable only the old producer task and confirm its process has terminated, with no lookup in flight. Do not stop bots, production workers or manipulate browser tabs. The Windows mutex name is unchanged; old and new agents must never run together. The existing Windows mutex implementation is retained; actual installation singleton proof is a later gate; a non-Windows production producer requires a separate singleton design.

## 3. Verified checkpoint and DB recovery preparation

Before server changes obtain a fresh authorized SQLite Online Backup of the active orders DB, source inventory and any other databases required by current Release Control policy. Record size, SHA-256, integrity, verified storage round-trip and restore validation on a disposable copy. Preserve the old server/agent files and queue/fence snapshot without claim secrets in public evidence. Do not run a production restore to validate this plan.

Additive Stage 3 schema adds nullable `dp_live_requests.cache_committed_at`; only the atomic server transaction sets it for FOUND. Confirm existing business tables and client/dealer ledgers unchanged on disposable migration. A DB restore is a separate destructive gate: it must account for legitimate business writes since checkpoint; never blindly overwrite later orders/finance with an older snapshot.

## 4. Paired switch sequence

1. Approve exact composed server release and its CI; record agent bundle hashes.
2. Close live admission and prove quiescence. Obtain verified checkpoint.
3. Obtain explicit old-agent retirement/installation permission; confirm old agent stopped and no in-flight work. Keep authorized Chrome session unchanged.
4. Separately deploy the v2 server, with the new producer still stopped. Existing v1 claim/result requests fail closed; v1 health only remains compatible. Expect live misses unavailable during this intentional gap; verified fresh cache stays usable.
5. Verify schema, startup, health, exact deployed SHA, queue counts, finance fingerprint and business projections. No DCP lookup.
6. Install only the approved pinned new bundle after its own GO, without starting a second agent. Preserve any inflight journal; never delete it merely to make startup succeed.
7. While admission remains closed, perform the separately approved smoke: local signature compatibility tests, v1 health contract, and signed v2 GET next only after fresh empty-queue proof. It must return `request=null`. Do not run the continuous agent loop as a 'smoke' while uncontrolled requests can arrive.
8. Start the single new producer and reopen admission only after Release Control approves the controlled live E2E window. No real lookup is implied by isolated CI or installation.

## 5. Separate live E2E gate

Require one owner-approved manufacturer/OEM, request budget and permitted browser access; no navigation/reload/focus, checkout or business writes. Record request ID, producer ID, generation, token hash only, source identity, USD dealer price, signed HTTP result, atomic cache commit and consumer output. Confirm one lookup shared by all configured adapters, no competing consumer upsert, client RUB/dealer USD behavior unchanged. Stop on any unexpected challenge, unknown result outcome, identity/source discrepancy or concurrency. Real OEMixi runtime must first be identified and configured; mocks are not live OEMixi evidence.

## 6. Cloudflare / authorization / uncertain delivery

CLOUDFLARE and AUTH_REQUIRED are reported before result delivery and stop the producer. It must not search again or automatically solve a challenge. The operator performs the ordinary authorized manual verification; passive READY alone does not clear the producer stop latch. Restart is controlled after the server circuit state and prior operation are reconciled.

The disk journal is durably written before GET claim, before DCP lookup and before POST result. A GET/POST timeout, 401/409, malformed response, crash or failed ACK leaves STOP/reconciliation required. A lost POST ACK may mean the server already committed. Never rerun DCP or replay result blindly; no automatic v1 fallback. Journal contains producer/request/generation/time/phase only, never cookies, tokens, claim_token or prices.

## 7. Recovery authority and evidence

Release Control appoints the operator authorized to reconcile queue and local journal. Read the exact server request row, atomic `cache_committed_at`, health/circuit, recovery events and installed process state. Prove old operation inactive. Cases:

- Finished atomic FOUND: verify original identity/source/price and commit proof; archive journal plus evidence; no second lookup or cache write.
- Finished non-FOUND: verify terminal state and health; archive journal, no automatic retry.
- Claimed/orphaned: do not assume inactivity from a lease. Use Stage 2 manual recovery only with approved plan and inactivity evidence; revoke fence without requeue.
- Unknown GET claim outcome: reconcile by producer owner/time and queue snapshot, not guessed request ID.

After reconciliation, journal archival/removal and restart require explicit operator approval and an audit record (actor/time, journal hash, server row/fence hash, recovery plan/evidence SHA). There is deliberately no automatic journal recovery endpoint or timer in Stage 3. If proof is insufficient, keep STOP.

## 8. Rollback

First close admission and stop only the new producer after verifying no in-flight lookup/delivery; preserve journal and queue. A server-only rollback to v1 while v2 claimed/orphaned requests exist is forbidden. Never downgrade request fences, delete queue rows, reset generations or clear a circuit to force progress.

Prefer holding v2 server in fail-closed cache-first operation while diagnosing. Rollback to the old server AND old producer requires exact paired revisions, positively reconciled all v2 claims/orphans, approved checkpoint strategy and a separate Release Control GO. Restore a DB only when explicitly approved with subsequent business-write reconciliation. A pending uncertain result must be resolved before either producer starts. Successful rollback is health/identity/schema/business verification, not a new automatic DCP lookup.

## 9. Closure evidence

Record approved/deployed server SHA and agent bundle SHA, process/task singleton evidence, protocol smoke, quiescence before/after, checkpoint identity, client/dealer regression, cache single-writer evidence, all stop/recovery events and remaining blockers. WEB2 stays OFF; WOPS, FinanceEngine, business sends/Apply/settlements and cleanup remain out of scope. Stop after each gate and return results to Theme 4.
