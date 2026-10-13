# DCP Reliability Stage 2 — isolated server consistency candidate

Parent: PR #32 HEAD `2ed283ac68ac1cfbc1252bd9120f98d7d9494fd5`.
PR #32 remains Draft and unchanged. PR #31 remains HOLD and unchanged.
No merge, deploy, installed-agent update, real DCP lookup, browser operation or production cache write is authorized.

## Atomic completion

`POST /internal/dp-sync/result` verifies a protocol-v2 HMAC over method, path, timestamp, producer ID and SHA-256 of the exact request bytes before opening the result transaction. The body must agree with the authenticated producer ID. Pydantic coercion is not used for numeric trust decisions.

`dp_result_completion.complete_result` uses one connection and `BEGIN IMMEDIATE`. It validates request state, claim token/owner/generation and lease deadline, canonical manufacturer, query OEM, matched SKU, current OEM, finite positive DP, USD currency and an explicit dealer-row source. AUTO FOUND cannot contradict an existing confirmed identity. CF/AUTH cannot become FOUND or release live work.

The same transaction writes the requested/current OEM cache, AUTO identity, result completion and any CF/AUTH circuit state. There are no separate core-upsert calls or nested commits. Any validation, SQL or COMMIT error rolls back all result writes. Duplicate, late, stale-fence and conflicting results preserve previous verified prices and identity. Error results finish without writing DP/identity. A blocked AUTO result does not require a discovered manufacturer.

Accepted raw producer sources are the exact requested-OEM `live_dom:#add_<OEM> .c2 .dbl` / `server_html:#add_<OEM> .c2 .dbl`, or `catalog_product:exact_sku_price` for Ski-Doo. Retail, MSRP, RRP, generic unverified price labels and other currencies are rejected.

## Protocol v2 and producer prerequisites

The existing key derivation is retained, but the signed message is:

```text
v2\n<METHOD>\n<PATH>\n<UNIX_TIMESTAMP>\n<PRODUCER_ID>\n<SHA256(EXACT_BODY_BYTES)>
```

Headers: `X-DP-Protocol: 2`, `X-DP-Producer-ID`, `X-DP-Sync-Ts`, `X-DP-Sync-Sig`. Maximum timestamp skew is 60 seconds. GET next uses an empty body. Producer IDs identify a boot/session, not a browser fingerprint.

GET next returns `protocol_version`, `request_id`, manufacturer/OEM, a random `claim_token`, `claim_generation`, `producer_id`, and `lease_expires_at`. Result must echo the fence. FOUND must additionally include query OEM, matched SKU, current OEM if superseded, manufacturer, currency USD, parsed dealer price and explicit source. Keep claim tokens out of logs and handoffs.

An authenticated v1 agent receives `request: null / producer_protocol_upgrade_required`, so it cannot start work it cannot fence. A v1 result is rejected. **A server-only rollout intentionally stops new v1 live work.** The installed agent was not modified; protocol-v2 producer implementation/installation requires a later authorized task. Existing v1 health reporting remains available for the staged compatibility period.

## Orphan recovery contract

Client wait timeout is not producer loss: a 10-second client timeout leaves a valid claim in flight. The fixed candidate lease is 600 seconds, independent of client wait. Lease expiry, missing fencing or malformed deadline means uncertainty; the claim becomes `orphaned` at broker admission/claim. It is never automatically released or retried. READY alone cannot free it. All new live admissions remain fail-closed while an orphan exists; fresh cache remains usable.

Manual recovery is a privileged offline operation with no public HTTP route or background timer:

1. Read `dp_claim_recovery.recovery_plan`; it opens SQLite read-only and outputs the claim identity, generation, deadline and token hash, never the token.
2. Obtain separate administrative authorization for that exact plan SHA. Verify the OLD producer/browser operation really ended or its producer is inactive; lease expiry alone is insufficient. Preserve evidence and its SHA-256. No browser reload or implicit worker stop is part of this procedure.
3. Require a fresh non-future READY observed after quarantine, at most 60 seconds old, and newer than any blocking circuit observation. CF/AUTH, stale READY, changed plan and missing inactivity attestation deny recovery.
4. Explicitly invoke `revoke_orphan` / CLI `--apply`, with plan SHA, operator, evidence SHA and `--producer-inactive-confirmed`. The transaction increments the fence generation, clears its token, records the audit event and finishes the OLD request as `admin_revoked_without_retry`. Audit failure rolls back revocation.
5. Do not requeue the old OEM. Only a new explicit consumer request can later create a new claim. The old owner's delayed result remains rejected even when a new claim exists.

This is **fail-closed + operator-attested recovery**, not proven automatic crash detection. The server cannot independently establish that an external browser operation stopped. Authorization/evidence is trusted at the privileged administrative boundary. The CLI was tested only on disposable SQLite; no production recovery was executed.

## Actual adapter mock integration

The tests execute the actual source bodies of Probnik `_identity_and_price`, Procenka `enrich_found_result_with_dealer_price` and cache helpers, and OEMixi `resolve_offer` / identity adapter. Their external dependencies are mocked; no bots are started.

`dcp_price_gateway.request_price` is a read-only verified-cache-first backend with one shared queue miss. `SharedDealerPriceAdapter` requires an explicit freshness policy and checks manufacturer/OEM, USD, verified source, price and age. `oemixibot_catalog.resolve_offer` accepts explicit `price_gateway=` injection only. Its installed/default direct-DCP path is unchanged. There is no new production HTTP price route or runtime switch.

Tests prove three actual adapter requests -> one signed mock producer claim, cache hits without DCP, CF/AUTH stop without MSRP substitution, crash quarantine, READY without automatic orphan release, explicit administrative recovery, and rejection of duplicate/old-owner results. Client RUB rounding and dealer USD remain separate. The 168-hour policy in fixtures matches existing clients and does NOT approve dealer production TTL; that policy/runtime still require confirmation.

## Runtime audit limits

Read-only Windows process/task/service audit did not establish a running OEMixiBOT. The DP agent PID 42360 is established; its scheduled task points to `vk_prod_redeploy_20261002`. Candidate-folder OEMixi app/catalog files and local READY health exist, but neither proves an active bot, its launch path or runtime revision. Its static catalog uses direct DCP. Freshness policy for a real OEMixi runtime is NOT PROVEN. No second bot was started and no installed file was changed.

## Scope and compatibility

FinanceEngine, Probnik/Procenka source, WEB2 UI and business/order/stock modules are unchanged. The financial regression retains the original FinanceEngine and Probnik/checkout guards. WEB ADMIN byte preservation is narrowed only for the explicit DCP result model, next/result handlers and two exact new imports; all other bytes remain pinned to the accepted Point 10 base. Negative tests reject unrelated/admin/health/signature/route changes and repurposed DCP routes.

PR #32 is the parent, not an independent production merge. PR #31 has overlapping result model/handler and scope guard changes; WOPS/explicit MSRP transport must later be composed with the new atomic/fenced protocol without source mixing. No combined #31/#32/Stage2 build is claimed. Do not use whole-file conflict resolution or activate WOPS.

Before staged rollout: implement/test producer v2 separately; identify every actual OEMixi runtime and approve its freshness/gateway transport; require zero active/orphaned claims before switching versions; agree manual recovery authority/evidence; validate held-PR conflicts. A rollback to the old unfenced result handler while v2 claims/producers exist is unsafe and requires a separate quiescence/rollback plan. No rollout or rollback is authorized here.

## Validation

Local: 102/102 DCP/HTTP/recovery/actual-adapter/card/startup tests (42 new Stage2 tests); 23/23 dealer/runtime; five atomic client-finance/checkout guards PASS. SQL-trigger failures at cache alias, identity, request completion and a deferred foreign-key COMMIT failure leave exact prior snapshots unchanged. FinanceEngine SHA-256 remains `17546efcea563f0f3e1942947f085e31ed6d88a9ee6401e2c18147171f57d863`.

GitHub CI must be reported on the exact Stage2 candidate HEAD. WEB2 CI is applicable because the server file changed; no WEB2 UI change or enablement occurs.

Target: SERVER CONSISTENCY PASS / MOCK INTEGRATION PASS / RUNTIME NOT PROVEN / NO PRODUCTION CHANGE / STOP AFTER HANDOFF.
