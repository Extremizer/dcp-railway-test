# WEB2 / PR #24 — staging configuration and E2E plan (no deployment)

## Gate
- PR remains Draft. No merge, staging deployment, production deployment, or production flag switch.
- No Railway staging environment has been selected or verified by this document.
- Production EXTREMIZER_WEB2_ENABLED must not be changed.
- Mock-only local app: uvicorn web2_staging_app:app --host 127.0.0.1 --port 8082.
- Real WEB1 wrapper web2_runtime_app:app must not be deployed to a shared environment before isolation proof.

## Required staging configuration — all must be verified before deployment
1. Identify the exact Railway project, environment, service, branch and SHA; confirm they are separate from production.
2. Use a dedicated fresh database and dedicated volume/storage bucket or isolated prefix. Verify effective DB paths, mount IDs and bucket identities are not production.
3. Do not reuse production Telegram tokens or start bot polling/webhooks. Use a web-only process, not web1_runtime.py (it starts bots and backups).
4. Audit effective environment variables including inherited/shared values, with secrets redacted. Block production credentials, backup targets and supplier/DCP side effects.
5. Restrict staging access; record hostname and rollback procedure (stop staging only).
6. Obtain Release Control approval of this concrete verified configuration before any deployment.

## E2E acceptance matrix
| ID | Scenario | Expected | Allowed test environment |
| --- | --- | --- | --- |
| OEM-01 | Verified Ski-Doo 417300574 | Identity and cached USD-derived RUB price | Temporary SQLite |
| OEM-02 | MSRP higher than client price | MSRP and benefit visible | Temporary SQLite/browser mock |
| OEM-03 | MSRP absent or not higher | MSRP and benefit hidden | Temporary SQLite/browser mock |
| OEM-04 | Missing/stale dealer price | No USA offer; no MSRP price substitution | Temporary SQLite |
| OEM-05 | Unknown OEM | 404 and clear client error | Temporary SQLite |
| OEM-06 | Second manufacturer and alias | Correct manufacturer isolation | Temporary SQLite |
| OEM-07 | Replaced OEM | Requested and canonical OEM consistent | Temporary SQLite |
| OEM-08 | Fresh priced warehouse-only OEM | RF offer without USA offer | Temporary SQLite |
| OEM-09 | Stale/unpriced warehouse row | No purchasable offer | Temporary SQLite |
| OEM-10 | Warehouse refresh | Updated offers and stock | Browser mock |
| CART-01 | USA and RF cart, quantity, removal | Correct totals and quantity cap | Browser mock |
| CART-02 | Tampered price snapshot | Server price revalidation, no order charge | Temporary SQLite |
| CART-03 | Invalid warehouse/quantity | Server rejects request | Temporary SQLite |
| CART-04 | Actual Telegram checkout | Real bot receives handoff | NOT AUTHORIZED |
| LIVE-01 | Live supplier DCP | Real price/error response | NOT AUTHORIZED |

## Exit criteria
- One exact HEAD has all relevant isolated CI PASS.
- Missing scenarios remain explicitly pending; mock PASS is not live E2E.
- Release Control receives exact staging resource identities, verified isolation evidence, redacted variable audit, SHA, results and rollback plan.
- No merge, deployment or production writes by WEB2 topic.