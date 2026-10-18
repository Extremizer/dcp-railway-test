# OEMixi Trusted Evidence Receiver — NON-PRODUCTION PLAN ONLY

**Status:** Isolated candidate; NOT DEPLOYED. Theme 4 must explicitly authorize each external action.

## Isolation
- Create a **new dedicated Railway service** in a separate non-production environment. Never attach receiver routes to current production `dcp-railway-test`.
- Code entrypoint: `oemixi_evidence_receiver_runtime:app_factory`.
- Start command: `uvicorn oemixi_evidence_receiver_runtime:app_factory --factory --host 0.0.0.0 --port $PORT`.
- Dependencies: `fastapi>=0.115,<1`, `uvicorn>=0.30,<1`, `httpx>=0.27,<0.29` (the last is for test only).
- Service routes: `GET /health` and `POST /internal/oemixi/catalog-evidence/v1`; FastAPI OpenAPI/docs disabled. Unsupported path/method → 404/405.

## Dedicated persistent storage
- Attach a **new, isolated Railway persistent volume** to this non-production service.
- Mount volume at `/var/lib/oemixi-evidence`.
- Set `EXTREMIZER_EVIDENCE_RECEIVER_DB_PATH=/var/lib/oemixi-evidence/trusted_evidence.sqlite3`.
- Application factory explicitly initializes the evidence schema **on startup before serving** via existing `initialize_evidence_store()`; POST never creates tables.
- Do **not** reuse Category Authority, enrollment nonce, Shared Price, or production DB paths.
- Restart/volume remount verification is required before any real producer traffic; synthetic CI only checks same-file router reconstruction.

## Independent secret
- Set `EXTREMIZER_EVIDENCE_PRODUCER_HMAC_SECRET` to a **new dedicated value** of at least 32 characters in the **non-production receiver** and separately authorized producer environment.
- Do not derive from, copy or reuse any `OEMIXIBOT_GATEWAY_SECRET`, DP admission key, bot token, or existing runtime credential.
- Never put its actual value in files, logs, test fixtures or HANDOFF.

## Read-only rollout gates (no action authorized here)
1. Theme 4 reviews exact source HEAD and targeted CI; approves isolated Railway service creation separately.
2. Verify `/health` reports dedicated receiver and production service is unchanged.
3. Verify restart with same Railway volume preserves nonce replay rejection.
4. Verify producer's authorized source and separate secret provisioning.
5. Only under separate approval perform producer → isolated non-production receiver HTTPS evidence test; no Category Authority, identity or Stage B.
6. Keep existing live producer off, unless explicitly separately authorized.

**Constraints:** NO PRODUCTION CHANGE, NO MERGE, NO DEPLOY, NO LIVE POST, NO STAGE B, NO CART MUTATION.
