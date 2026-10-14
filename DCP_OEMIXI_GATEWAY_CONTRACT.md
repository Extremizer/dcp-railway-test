# OEMixi shared gateway integration contract — isolated readiness proposal

Runtime: NOT PROVEN. No installed bot or agent is changed. `SharedDealerPriceAdapter` is explicit opt-in dependency injection into the actual `oemixibot_catalog.resolve_offer` function. Its missing/non-callable gateway, exceptions and malformed responses return TECHNICAL_ERROR without a dealer price and without direct-browser fallback. The existing installed/default path is not silently switched.

## In-process server gateway

`request_price(orders_db, canonical_manufacturer, normalized_oem, max_age_hours=<explicit dealer policy>, wait_seconds=<bounded>)`: read verified fresh cache first; then use the same SQLite broker and atomic producer completion. No separate browser. FOUND on live miss requires persisted server commit proof and verified cache reread. A cache hit has `verified_cache_hit=true`; it does not claim a new server commit.

Dealer policy must be approved explicitly as a finite positive number of hours. There is no production dealer TTL decision in this patch. 168-hour client fixtures are not dealer policy. Configure/validate policy when constructing the adapter; missing policy is configuration failure, never a fallback to client defaults.

## Proposed external transport (not an existing production endpoint)

For a separate local OEMixi runtime, propose authenticated HTTPS `POST /internal/dcp-price/resolve` through a dedicated transport callable. This route, server consumer authentication and deployment are NOT implemented/authorized by this Stage 3 patch. Required implementation/review is a rollout blocker before connecting that runtime.

Request schema: canonical `manufacturer`, normalized `oem`, explicit bounded `max_age_hours` and `wait_seconds`, correlation ID. Server clamps freshness/wait to the authenticated bot policy; callers cannot enlarge access by supplying arbitrary TTL. Response schema: status, manufacturer, query_oem, current_oem, finite positive dealer_price_usd, currency=USD, exact verified_local_dcp source, last_verified_at, request_id when applicable; atomic completion proof only when actually known. Error responses never expose a dealer price or claim secrets. Cache hit and atomic completion are distinct provenance.

Authentication: separate revocable per-bot consumer credential over TLS, stored outside source, never Telegram credentials, cookies or producer HMAC. Allowed scopes are `dp.cache.read` and optional `dp.live.request`; no `/dp-sync/next`, `/dp-sync/result`, recovery, health-write, finance, stock, order or admin-write scopes. Consumer identity restricts manufacturer access and approved dealer freshness. Separate permissions to enqueue live work from cache-only read. The server must enforce rate limiting, shared broker health/circuit admission and audit sanitized request/status/timing. Do not reuse the producer's ability to complete requests as a consumer credential.

Transport: bounded connection/read timeouts; no automatic retry of uncertain enqueue, no direct-DCP fallback when unavailable. Fail closed on 401/403/409, unknown body, incorrect identity/currency/source or expired data. Only authorized operator confirms recovery after session health is READY. No MSRP/Retail/RRP substitution. Credentials and sensitive headers are never logged.

## Evidence limits and integration checklist

Actual Probnik `_identity_and_price`, Procenka `enrich_found_result_with_dealer_price`, and OEMixi `resolve_offer` functions are exercised with the shared gateway, disposable SQLite and signed mock producer. They prove contract compatibility, one queued lookup and client RUB/dealer USD preservation; they do not prove installed OEMixi runtime, real HTTPS consumer auth or live DCP.

Before activation: identify actual process/service/start path/source SHA; choose explicit dealer TTL; implement/review transport and scoped authentication; run mock HTTP auth/scope regressions; prove no independent direct DCP path in the selected configuration; obtain separate owner/Release Control GO. Do not start another OEMixiBOT to establish identity.
