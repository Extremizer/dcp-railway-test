# OEMixiBOT Secure Shared Gateway Transport v1

Status: **Stage C1 isolated prototype / disabled by default / no production registration**

## Purpose

Provide a single safe network path:

`Windows OEMixiBOT -> HTTPS consumer endpoint -> existing Shared Gateway -> Verified Cache / Shared Broker -> one existing Producer`

The consumer cannot access producer claim/finish operations and never receives DCP cookies or browser sessions.

## Endpoint

Prototype path:

`POST /internal/oemixibot/price-gateway/v1`

The router is built by:

`oemixi_secure_gateway_transport.build_router(...)`

It is **not imported or registered by production `web_app.py` in Stage C1**. A host must explicitly include the router.

## Request body

Only:

```json
{
  "manufacturer": "Arctic Cat",
  "oem": "2670-265"
}
```

Pydantic extra fields are forbidden.

Windows cannot send or control:

- Dealer Price;
- MSRP;
- source/provenance;
- freshness/TTL;
- request/claim IDs;
- producer ID;
- claim token/generation;
- admission state;
- DCP cookies/session/browser data.

## Authentication

Stage C1 reuses the repository's existing OEMixiBOT HMAC pattern rather than creating another independent auth system.

Root secret:

- supplied by host/runtime configuration;
- tests use an isolated fake secret;
- no production secret was read or copied.

A separate domain-separated key is derived:

`SHA256("extremizer-oemixibot-price-gateway-v1\0" + root_secret)`

Signed message:

```
oemixi-price-gateway-v1
POST
/internal/oemixibot/price-gateway/v1
<unix timestamp>
<nonce>
<SHA256(canonical request body)>
```

Headers:

- `X-OEMixi-Gateway-TS`
- `X-OEMixi-Gateway-Nonce`
- `X-OEMixi-Gateway-Sig`

Server checks:

- bounded clock skew;
- constant-time HMAC comparison;
- nonce syntax;
- one-time nonce claim in server SQLite;
- replay rejection.

## Replay protection

Server table:

`oemixi_gateway_replay_v1`

Purpose only: consumed request nonce + timestamp.

A nonce can authorize at most one request.

Old nonce rows are bounded by a short cleanup horizon.

Replay does not create a new Shared Gateway request.

## HTTPS

`HTTPSPriceTransport` requires an `https://` base URL.

Plain HTTP is rejected except when an explicit isolated test switch is used by tests.

## Request/response limits

Request:

- manufacturer max 64 chars;
- OEM max 40 chars;
- extra JSON fields forbidden;
- canonical body max 2048 bytes;
- Content-Length above max rejected.

Response:

- transport reads at most 16 KiB by default;
- oversized response -> `TECHNICAL_ERROR`.

Network timeout:

- explicit and finite;
- maximum accepted transport timeout is 15 seconds.

## Server authority

The request never supplies freshness.

Host constructs the router with explicit test/production policy values:

- `server_max_age_hours`;
- `msrp_max_age_hours`;
- `wait_seconds`.

The server alone calls existing `dcp_price_gateway.request_price()`.

Therefore server remains authority for:

- verified Dealer Price;
- cache-first decision;
- admission;
- live request need;
- shared request queue;
- producer coordination;
- result source/timestamp;
- manufacturer/OEM validation.

## Canonical Price Contract v1

Server reads Dealer and MSRP independently:

Dealer:

- existing Shared Gateway;
- existing `dealer_price_cache`;
- no second producer.

MSRP:

- read-only existing `oem_catalog_cache`;
- optional existing alias table;
- no live MSRP browser request in Stage C1.

Both are combined with:

`from_gateway_and_msrp(...)`

and returned through the accepted compatibility shape.

MSRP-only never creates a Dealer `FOUND`.

## Existing client compatibility proof

The compatibility response preserves server-generated evidence needed by existing consumers:

- `server_cache_committed`;
- `verified_cache_hit`;
- `cache_committed_at`;
- `request_id`;
- verified source/timestamp.

These values are copied only from the server's existing Shared Gateway result. Windows cannot submit them.

## Runtime binding

Accepted PR #38 binding remains unchanged:

`build_application(..., shared_price_gateway=transport, dealer_max_age_hours=...)`

The adapter is installed before Telegram polling starts.

No environment-variable auto-switch is added.

No hot-rebind path is added.

Bound mode continues to fail closed rather than use direct legacy browser lookup.

## Failure mapping

Authentication 401/403:

`TECHNICAL_ERROR / gateway_auth_failed`

HTTP 429:

`RATE_LIMITED / gateway_rate_limited`

HTTP 5xx:

`TECHNICAL_ERROR / gateway_5xx`

Transport timeout/network failure:

`TECHNICAL_ERROR / gateway_transport_error`

Malformed JSON:

`TECHNICAL_ERROR / invalid_gateway_json`

Oversized response:

`TECHNICAL_ERROR / response_too_large`

No failure path substitutes MSRP for Dealer Price.

## Stage C1 isolated E2E

Test path:

1. Windows-like OEMixi runtime.
2. `HTTPSPriceTransport`.
3. FastAPI test gateway.
4. HMAC + replay store.
5. disposable SQLite coordination DB.
6. existing `dcp_price_gateway.request_price()`.
7. existing `dp_live_bridge`.
8. mock producer posts through existing signed `/internal/dp-sync` completion API.
9. atomic verified Dealer cache commit.
10. Canonical Dealer/MSRP composition.
11. accepted `SharedDealerPriceAdapter`.
12. real `oemixibot_app.oem_message`.
13. dealer USD Telegram response.

No real Telegram polling, DCP, cart, money or production DB.

## Three-consumer proof

Probnik + Procenka + OEMixi use the same secure transport concurrently for the same OEM.

Result:

- one server `dp_live_requests` request;
- one producer claim;
- same verified Dealer Price;
- MSRP remains separate;
- direct OEMixi browser fallback is asserted forbidden.

## Production status

Not enabled.

Still required before release:

- owner topology decision;
- production endpoint registration decision;
- production credential provisioning/rotation;
- Dealer freshness policy;
- MSRP freshness policy;
- Railway/service reachability review;
- singleton bot runtime proof;
- Theme 6 first-switch/admission/recovery gates;
- maintenance/checkpoint/queue checks;
- Release Control + owner GO.
