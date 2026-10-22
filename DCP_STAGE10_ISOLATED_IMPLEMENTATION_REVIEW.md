# EXTREMIZER — Stage 10 isolated implementation review (Theme 6 → Theme 4)

**SOURCE BASE:** PR #64 exact commit `40b261fdd6b89f751dd0b94948353f50301a84fc`.  
**MODE:** isolated code/mock CI only. **NO LIVE DCP / NO RAILWAY / NO MERGE / NO DEPLOY / NO PRODUCTION ADMISSION.**

## Reconciled ancestry / selective reuse

Stage 9 and Stage 9 Layer C reconciliation diverge from PR #64. This branch starts from PR #64 exact HEAD, not from either obsolete line. The final three accepted Stage 9 Python artifacts were copied selectively; no historical cherry-pick or replacement of production core files. Stage 9 workflow was replaced with an isolated Stage 10 gate that reuses the accepted PR #64 combined test commands.

## Server-enforced ownership

`dcp_stage10_ownership_epoch.py` provides a single shared SQLite owner state with serializing `BEGIN IMMEDIATE`: monotonically increasing epoch, single owner, random nonce, ACTIVE/QUARANTINED/EMPTY, explicit revocation requiring independently attested cessation and no `claimed`/`orphaned` request. A timestamp never authorizes another owner: the system fails closed and old host ownership survives crashes until operator reconciliation.

Opt-in `EXTREMIZER_DP_OWNERSHIP_ENFORCE=1` gates the existing broker's `claim_next` inside its transaction, and `complete_result` inside the existing single atomic completion/cache transaction. An absent or stale ownership proof cannot acquire claims or commit Dealer FOUND. Existing token, claim_generation, owner, lease, Category Authority and source allow-list checks remain enforced; no second cache writer or broker was added.

The existing signed v2 HTTP claim endpoint accepts additional ownership epoch/nonce headers only when opt-in enforcement is enabled. The result carries ownership_proof in the exact HMAC-signed v2 JSON body. A separate Stage 10 loopback transport adapter composes the existing agent/journal with these fields for synthetic tests. **No signed-v2 remote ownership acquisition or administrative revocation endpoint is provided.** Mock tests obtain proof directly from disposable server DB. Real transport and least-privilege identity approval are **separate future work**.

## Linux mock producer & supplier boundary

Stage 9 local `flock`, explicit loopback target, absolute persistent paths and restart-safe journal remain. Stage 10 mock transport strictly permits signed v2 NEXT/RESULT to local server fixture. A deterministic fixture-only supplier adapter never executes Chromium, browser navigation, cart actions or supplier HTTP. It verifies PR #64 semantics: a same-origin `#persistant-nav a[href=/account/logoff]`, exactly one `#persistant-nav .displayname`, exact configured dealer identity, and fails closed for missing/mismatched identity or CF markers. A fixture-authenticated identity is **not** a verified Dealer Price. Polaris verified-cart route is separate; Parts pricing adapter is disabled. No MSRP substitution or producer-controlled routing.

## Proof and limitations

- Two independent processes contend for one SQLite ownership row; one winner only.
- Two simulated hosts contend for existing broker claims; stale owner and wrong nonce denied.
- Active in-flight claim and orphaned state block transfer; expiry of ownership timestamp is never takeover proof.
- Stale proof cannot write verified cache in existing atomic completion transaction.
- Existing real signed-v2 agent through loopback mock receives AUTH_REQUIRED and CLOUDFLARE; zero Dealer cache change and no retry after STOP.
- Lost result ACK leaves journal uncertainty and blocks restart / duplicate supplier call.
- PR #64 original 293 tests +32 subtests are executed unchanged alongside Stage 9 and Stage 10 safety suites.

**Remaining blockers:** proof of producer cessation remains externally supplied and not automatically attestable; an authenticated server-side ownership-acquisition/control path is not implemented; real Linux Chromium launch, supplier permission/auth, DCP source semantics and real browser identity are NOT PROVEN; cross-host first-switch admission and LIVE E2E remain HOLD. The current opt-in producer mechanism is NOT production-ready.

## Separate future staging / LIVE plan (NOT AUTHORIZED)

1. Theme 4 reviews this exact-head diff and verifies no unrelated runtime regressions.
2. Approve a least-privilege signed ownership acquisition protocol, independent operator proof of old host cessation, and secure nonce transfer; never use an expiry as cessation evidence.
3. In isolated Linux-only staging, provision immutable source, persistent lock/journal/status storage and loopback/mock broker first; demonstrate host overlap/restart/quarantine after deployment without DCP.
4. Resolve explicit authorized supplier unattended-access agreement; obtain account identity presence only, then separately review real identity verification and supplier-specific Dealer Price semantics.
5. Independently revalidate Category Authority, exact manufacturer/OEM, Polaris cart isolation, AUTH_REQUIRED/CLOUDFLARE, no cache mutation on unverified source, TTL policy and current backup/restore gates.
6. Only after Release Control approval: prepare a separate production cutover plan with global writer cessation, current queue/no-inflight proof, epoch handover, rollback and post-deploy verification. **This review grants no authorization for that stage.**

**RELEASE HOLD**: NO WINDOWS AGENT / NO REAL CART / NO DCP NETWORK / NO RAILWAY CHANGE / NO PRODUCTION DB/ENV / NO MERGE / NO DEPLOY / PHONE-FIRST PAUSED.
