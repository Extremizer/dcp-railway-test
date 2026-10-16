# EXTREMIZER — Theme 6 / Stage 9 isolated Linux mock runner

**Release boundary: NO LIVE DCP / NO RAILWAY CHANGE / NO DEPLOY.** Based on PR #46 at `b98e6f14e383824ef220c818d023d54415b78006`.

## Scope

The standalone `dcp_stage9_mock_linux_runner.py` accepts only `DP_RUNNER_TRANSPORT=fake` and an **explicit loopback HTTP mock server**. It reuses the existing `dp_sync_agent.run_once` signed v2 claim/result implementation, existing broker, fenced claim and atomic verified cache via server handler. It does **not** create any alternative cache writer, broker, PriceEngine, or supplier browser. Fake supplier returns `TECHNICAL_ERROR`, never a price. No Windows fallback.

## Required configuration (isolated mock host only)

- `DP_RUNNER_TRANSPORT=fake`
- `DP_RUNNER_SERVER_URL=http://127.0.0.1:<explicit mock port>` — loopback only, no production URL
- `DP_RUNNER_STATE_DIR=<absolute existing persistent writable directory>`
- `DP_RUNNER_LOCK_FILE=<absolute path inside state directory>`
- `DP_RUNNER_JOURNAL_FILE=<absolute path inside state directory>`
- `DP_RUNNER_STATUS_FILE=<absolute path inside state directory>`
- Existing signed-v2 key source required by the accepted agent, with a **synthetic isolated test token only**; never real OEMIXIBOT_TOKEN or DCP secrets.
- Start the disposable mock broker/HTTP fixture separately. Missing mock broker must fail closed.

Persistent host advisory `flock` prevents two processes sharing the same lock inode from running simultaneously. A single lock **cannot** prevent two deployments running on different isolated filesystems. The production-level admission/single-writer switch remains RELEASE HOLD and needs authoritative coordinated cessation/no-inflight proof before any future activation.

## State and health contract

- `runner.lock`: stable persistent inode, DO NOT delete/recreate during running upgrades.
- `inflight.json`: existing v2 journal. Presence on restart forces `quarantined` and no claims. Operator-only reconciliation; no unsafe automatic requeue.
- `health.json`: atomic/fsynced `{runner, supplier, transport, detail, updated_at, pid}`. `runner=alive` only means local process/cycle is running; `supplier=unavailable` is deliberately never DCP READY. `quarantined` means stop.
- Actual source health `AUTH_REQUIRED` / `CLOUDFLARE` belongs to accepted supplier classifier and requires STOP, not bypass. Because Stage 9 never connects to real supplier, neither status can be asserted from live observation. They must be tested through injected synthetic outcomes before broader activation.
- Existing agent journal persists before claim and lookup. Lost ACK, crash or uncertainty latches stop; no automatic retry of uncertain OEM.

## Deployment/config checklist — review only

1. Reconcile intended target commit and mock-only runtime entrypoint.
2. Verify network target is loopback fixture and fails validation on any external/production hostname.
3. Verify state mount persists journal/status/lock and all three have distinct absolute paths.
4. Verify single process on shared lock inode, plus externally coordinated owner/lease for cross-host overlapping deployments.
5. Verify synthetic signing key, no cookies, DCP browser profile, real tokens, Windows CDP or external egress.
6. Verify lifecycle tests and precise commit GitHub CI.
7. Obtain independent global writer cessation/current queue/no-inflight evidence BEFORE any proposed first switch.
8. Confirm external authorization for unattended DCP remains NOT PROVEN; no live DCP access requested.
9. Theme 1 owns dealer TTL24h / MSRP TTL720h, unchanged here.
10. Do not modify or start Railway `dcp-cloud-browser-isolated`, merge or deploy.

## Handoff Theme 6 → Theme 4

Stage 9 artifact is an **isolated mock runner only**, not autonomous production readiness. All release gates remain HOLD. Current Linux process flock addresses only one host/shared inode. Existing v2 components are reused; production URL and state defaults in the legacy agent are not altered globally. CI status must be checked at exact HEAD, not inferred from file creation.

## Stage C2 contract drift boundary (Theme 4 follow-up)

The isolated runner now requires an explicit allowlist of exact manufacturer/OEM
pairs before its fake supplier can be invoked. An absent allowlist rejects all
claims. AUTO, UNKNOWN, manufacturer guessing/enumeration and unapproved
identities are rejected, including claims from an otherwise valid signed v2 broker.
Rejection leaves the existing uncertain claim journal in place, quarantines
the runner and requires operator reconciliation. It does not rewrite the
broker's claim, infer category, route carts, or create a new routing engine.

This is a **mock fixture admission check**, not Theme 1 category routing approval.
No Parts/Accessories fallback, unverified replacement, or cart-derived dealer
price is authorized. Theme 1 retains all runtime routing and TTL ownership.
