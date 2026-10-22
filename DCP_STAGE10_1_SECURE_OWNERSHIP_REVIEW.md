# EXTREMIZER — Stage 10.1 / Secure Producer Ownership (isolated review)

Base: PR #65 exact commit `161b0c220c2725b2c4f67e9f61e0aebed6419f87`.  
Scope: isolated mock only; NO LIVE DCP, real Chromium, Railway changes, production credentials, real cart, merge or deploy.

## Security contract

1. **Least-privilege mock HTTP control.** `dcp_stage10_1_mock_http.create_mock_app` exposes only mock `/mock/ownership/acquire`, `/mock/ownership/status`, `/mock/ownership/revoke`. It is never imported/mounted by `web_app.py`; there is no production control route. It requires separate test-only server-held operator and producer root signing keys. Producer acquisition signatures are derived per owner; only explicitly server-allowlisted identities can be granted. Replay request nonces are persisted transactionally. Status uses a separate operator key and exposes epoch/state, not ownership nonce.

2. **Revoke and independent cessation.** The mock operator attestation is HMAC-authenticated over exact method/path/timestamp/body, scoped to `release_control:producer.revoke`, exact owner/epoch, bounded to 120 seconds, replay-protected via a DB hash. It is **not sufficient by itself**. An independently created matching unconsumed server-side cessation receipt is mandatory, must have exact owner and epoch, independent-controller authority marker, and freshness <=120 seconds. Revocation and receipt consumption occur in the same SQLite transaction, requiring no active/orphaned claims. Under `EXTREMIZER_DP_OWNERSHIP_ENFORCE=1`, legacy caller-controlled `cessation_confirmed`+arbitrary `evidence_ref` revocation is rejected.

3. **Signed-v2 GET integrity.** Under opt-in enforcement, `/internal/dp-sync/next` signs/verifies canonical epoch, owner nonce and a fresh 64-hex request nonce as the signed-v2 GET pseudo-body. The server records request nonces transactionally on the existing shared broker before any claim. Tampered header or replay must not yield another claim. The existing legacy GET HMAC behavior remains unchanged when enforcement is off. Result v2 continues signing the exact JSON body, including ownership proof; atomic completion retains token/generation/owner/lease/Category Authority/source guards.

4. **Mock process and network races.** Independent host processes contend for shared SQLite ownership; one winner only. A stale/foreign owner or wrong epoch is rejected. A delayed host, expired time/lease, or health flag never establishes cessation; quarantine and orphan/in-flight claims block unsafe handover. Stage 9 local flock, durable journal, quarantine and explicit manufacturer/OEM allowlist remain.

5. **Supplier safety.** Stage 10 disabled fixture-only adapter preserves PR #64 same-origin `/account/logoff` and single exact `#persistant-nav .displayname` guard. Polaris cart source remains separate from Parts search. No real browser, supplier requests or Dealer FOUND is authorized; no MSRP fallback or new broker/cache writer/PriceEngine.

## What is NOT proven / no production admission

- Independent cessation receipt in these tests is a **synthetic test-harness injection**, not real verifiable provider/host termination telemetry. No real host-controller authentication, immutable evidence chain, replica eviction or network-partition cessation has been demonstrated.
- Operator and producer key provisioning/rotation, separation across real services, server endpoint exposure and hardening have **not** been validated on Railway or a live environment.
- A configured Linux Chromium producer, authenticated DCP account, real price-source semantics and uninterrupted cutover are unproven.
- On/off opt-in flag is code/test-only. Feature may not be enabled in production from this PR.
- Any conditional network/live development requires a new Theme 4 authorization.

## Future mock-staging gate — requires separate authorization

Deploy a fully isolated non-production mock server + two independent mock Linux hosts with separate persistent disks, private signing keys and no DCP egress. Verify authority-side receipts from an independently authenticated host controller, crash/partition/restart replay, loser host denial, conservative manual recovery, immutable request journaling and rollback. Only after independent security signoff discuss real browser authorization and production cutover; positive Dealer FOUND and cache writes remain HOLD.
