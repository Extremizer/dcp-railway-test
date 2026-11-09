# EXTREMIZER / OEM Cache Bridge / Dedicated Railway staging gate

Status: **SOURCE READY / LOCAL SYNTHETIC QA PASS / RAILWAY DEPLOY HOLD**.
Control identity: **Arctic Cat 0627-084 only**.

## Target isolation
- Railway project: `ext-oem-cache-bridge-stage`.
- Environment: `staging`.
- No services, volumes, buckets, shared variables, production credentials or databases created.
- GitHub branch: `isolated/oem-cache-bridge-staging-20261109`.
- Application: `oem_bridge_stage_http.py` using only Python standard library and local bridge source.
- Endpoint: `POST /internal/oem-cache-bridge/v1`, health: `GET /health`.
- HTTPS termination, if ever deployed, is Railway's responsibility; no external endpoint exists yet.

## Behavior
- Accept only HMAC-signed synthetic identity for Arctic Cat / 0627-084.
- Require `OEM_BRIDGE_STAGE_SYNTHETIC_ONLY=1`, a fresh staging-only
  `OEM_BRIDGE_STAGE_KEY` of at least 24 UTF-8 bytes and Railway `PORT`.
- NEVER reuse a production token, Railway production variables or dealer credentials.
- Reject tampered, replayed, oversized and price-bearing requests.
- No cache, database, file writes, browser navigation, outbound requests or CART.
- Replay ledger exists **only in process memory**. NOT production-grade or durable.
- No path from this code to production caches, MSRP, orders or checkout.

## Verified local checks
Run `py -3 -B -m unittest discover -s <isolated_repo> -p test_oem_bridge_stage*.py -v`.
The HTTP test binds **only 127.0.0.1 on an ephemeral port**, sends synthetic input
and shuts the listener down on completion.

## FINANCIAL BLOCKER / mandatory STOP
Railway account/workspace inspection on 2026-11-09 reported **no compute hard usage limit**.
Railway compute hard limits are **workspace-wide**, not isolated to this staging project;
hitting a workspace limit could stop the production bot. Do not set such a limit here.
**No Railway service, deployment, domain, startup or public HTTPS test without a
separate direct OWNER financial GO accepting potential extra consumption.**

If separately authorized in the future:
1. Check actual current limits, spend and staging project isolation again.
2. Explicitly authorize expected budget and running duration; confirm shutdown path.
3. Create a *new empty service only in the dedicated staging project*.
4. Use this isolated branch, never production; set stage-only vars and the Python start command.
5. Perform exactly one synthetic HTTPS delivery, inspect only sanitized status.
6. Stop or delete only that dedicated staging service after the test; verify no residual workload.
7. Production rollout requires yet another separate OWNER GO.

Do not reuse any other preexisting staging services or change project-wide production settings.
