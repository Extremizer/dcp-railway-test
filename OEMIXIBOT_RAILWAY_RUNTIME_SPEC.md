# OEMixiBOT Railway 24/7 Runtime Candidate

Status: isolated development candidate only. No deploy, no production polling.

## Approved owner decisions

- Target topology: Railway.
- Windows is not a permanent OEMixiBOT host.
- Dealer Price freshness: 24 hours.
- MSRP freshness: 720 hours.
- Production switch remains blocked pending Release Control and owner GO.

## Current Railway production facts

Read-only project inspection on 2026-10-15 confirmed:

- project: `accurate-insight`;
- production has service `dcp-railway-test`;
- `dcp-railway-test` runs one replica in `sfo`;
- current start command: `python -u web1_runtime.py`;
- current healthcheck: `/health`;
- current persistent volume: `dcp-railway-test-volume` mounted at `/data`;
- no OEMixiBOT service exists yet;
- no staged changes existed during the audit.

A different Railway service cannot treat its own `/data` as the same SQLite filesystem.

## Target service

New separate service:

`oemixibot`

Same Railway project/environment as the authoritative DCP service.

Recommended release settings:

- source repo: `Extremizer/dcp-railway-test`;
- approved Release Control branch/commit only;
- start command: `python -u oemixibot_railway_runtime.py`;
- healthcheck path: `/ready`;
- healthcheck timeout: 120 seconds;
- restart policy: `ALWAYS`;
- sleep/serverless mode: disabled;
- replicas: exactly 1;
- region: same operational region as its persistent volume;
- dedicated persistent volume mounted at `/data`;
- public domain: not required for Telegram polling worker.

Railway Config-as-Code is not used for this new service candidate. Current Railway documentation states that new services cannot opt into the legacy railway.json/toml Config-as-Code flow, so service-specific settings should be applied through service settings/API at Release Control.

## Runtime entrypoint

`oemixibot_railway_runtime.py`

Responsibilities:

1. validate required configuration;
2. enforce approved 24h/720h policies;
3. acquire a singleton polling lock;
4. verify persistent OEMixi SQLite is writable/healthy;
5. build existing `oemixibot_app.py`;
6. inject secure Shared Gateway before polling;
7. expose `/health` and `/ready`;
8. run exactly one Telegram poller;
9. release singleton lock on graceful shutdown.

No new dealer bot implementation exists.

## Required environment variables

Secrets are not committed.

Required:

- `OEMIXIBOT_TOKEN`
- `OEMIXIBOT_GATEWAY_SECRET`
- `OEMIXIBOT_GATEWAY_BASE_URL`
- `OEMIXIBOT_DB_PATH=/data/oemixibot.db`
- `OEMIXIBOT_IDENTITY_DB=/data/oemixibot_identity.db`
- `EXTREMIZER_OEM_REFERENCE_DB_FILE=/data/oem_reference.db`
- `OEMIXIBOT_SHARED_IDENTITY_ONLY=1`
- `OEMIXIBOT_DEALER_MAX_AGE_HOURS=24`
- `OEMIXIBOT_MSRP_MAX_AGE_HOURS=720`

Railway provides `PORT`.

## Gateway topology

Target:

`OEMixiBOT Railway worker -> Railway private network -> authoritative dcp-railway-test gateway -> existing Verified Cache / Shared Broker / Single Producer`

Recommended internal URL shape:

`http://dcp-railway-test.railway.internal:8080`

Railway private networking encrypts service-to-service traffic using its private WireGuard network. Application-level HMAC + timestamp + nonce remains required, so private network reachability alone is not authorization.

Public plain HTTP remains forbidden.

## Shared Gateway server mount

Candidate module:

`oemixibot_railway_gateway_mount.py`

It mounts the existing secure Stage C1 router onto an authoritative FastAPI service by calling the existing:

`dcp_price_gateway.request_price()`

No second queue, broker, producer, or DCP browser is created.

The module is not imported by current production `web_app.py`; therefore the endpoint remains disabled until Release Control explicitly integrates it.

## Identity authority

A separate Railway OEMixi service must not inspect a copied server orders/coordination DB.

Railway runtime requires:

`OEMIXIBOT_SHARED_IDENTITY_ONLY=1`

This disables local `_production_candidates()`.

Unresolved identity continues through the existing shared identity API and is then persisted in:

`/data/oemixibot_identity.db`

## Persistent data

Dedicated OEMixi volume contains:

- `oemixibot.db`
- `oemixibot_identity.db`
- `oem_reference.db`
- singleton lock file

No DCP coordination DB is mounted into the OEMixi service.

## SQLite / replicas

OEMixi uses SQLite for dealer state. Therefore this candidate requires:

- exactly one Railway replica;
- exactly one polling process;
- one attached persistent volume.

The runtime additionally uses a non-blocking `flock` on:

`oemixibot.db.polling.lock`

A second process on the mounted filesystem fails startup.

Horizontal replica scaling is not approved for this SQLite runtime.

## Health / readiness

`/health`:

process health.

`/ready`:

returns success only after:

- singleton acquired;
- persistent DB opened successfully;
- application constructed with Shared Gateway binding.

Railway healthcheck should use `/ready`.

## Restart

The worker itself performs graceful Telegram application shutdown on SIGTERM/SIGINT through python-telegram-bot's polling lifecycle.

Railway service candidate uses restart policy `ALWAYS` for 24/7 operation.

No background browser or DCP agent is started by this entrypoint.

## No direct DCP fallback

Railway runtime always constructs `build_application()` with an explicit Shared Gateway plus `OEMixiCanonicalSharedPriceAdapter`.

Legacy direct DCP remains unbound in this runtime.

Existing no-fallback regressions remain required for Release Control.

## Production cutover HOLD

Do not create/deploy/start the new service yet.

Before cutover:

1. Theme 6 first-switch/admission/producer gates green;
2. secure gateway endpoint explicitly mounted in the authoritative server candidate;
3. scoped credentials provisioned;
4. dedicated OEMixi Railway volume created;
5. Windows source DB paths confirmed;
6. final verified DB bundle created;
7. Windows poller stopped under owner GO;
8. prove Windows poller is stopped;
9. start exactly one Railway poller;
10. admin/dealer read-only smoke;
11. finance/order integrity check;
12. Release Control sign-off.
