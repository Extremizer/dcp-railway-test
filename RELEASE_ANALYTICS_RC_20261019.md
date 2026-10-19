# EXTREMIZER ISOLATED ANALYTICS RC — RELEASE MANIFEST
Date: 2026-10-19
Status: DRAFT / PRODUCTION HOLD / NOT ACCEPTED

## Identity
Production/review base SHA: 67829c99d22e282b446ff3610de945da54b3c2bb
Included unified analytics candidate: PR #56 exact 02add8e4d4aff03003c4acb475a7a4d126990e43
RC branch: release-isolated-admin-analytics-rc-20261019
DRAFT PR: #57, target release-isolated-admin-analytics-base-20261019
RC HEAD: see GitHub PR #57 head (this manifest is committed after the implementation)
No merge, no deploy, no flag activation.

## Slice matrix
PR #56: INCLUDED — three-bot analytics core/producer, transport, opt-in read API; existing 79/79 combined at candidate SHA (not RC exact-head).
ADMIN analytics page: INCLUDED — unified_analytics_admin_page.py, web_app.py mount, supplier_web_admin.py flag-aware navigation, synthetic UI tests.
PR #53: EXCLUDED — older-base POWERSPORTS; historic head ancestor of production; do not replay.
PR #54: EXCLUDED — older-base Polaris; historic head ancestor of production; do not replay.
PR #55: EXCLUDED — signed-v2 currency older-base head ancestor of production; diff already in historical baseline; separate production readiness gate remains required.
Backup AUTO: UNCHANGED — Oct 18/19 nightly accepted; offsite DR evidence SOURCE GAP.

## Access / flags
EXTREMIZER_UNIFIED_ANALYTICS_ENABLE=1 required for route /admin/analytics; default absent/off means no new page or navigation link.
Uses supplier_admin_auth WEB_ADMIN session/RBAC via dependency. No Telegram identifiers in output. Read-only unified_analytics_core.query uses SQLite mode=ro.
Same canonical core.ORDERS_DB_FILE; no new reporting DB.
The canonical analytics schema may need explicit initialization before flag-enabled use; no migration authorized.

## Test obligations
Expected exact-head combined Linux: unified analytics three producer, admin API/UI, checkout/Finance, OEMixi gateway, startup/compile.
UI synthetic pytest added; do not claim browser screenshot or visual acceptance until actually run.
Required: anonymous/session invalid/403, filters, pagination, no PII, legacy rows, empty/errors, 360px and desktop browser verification.
CI and browser final result: PENDING. See PR #57 Checks tab.

## Runtime topology / deployment
Authoritative production SHA supplied: 67829c99d22e282b446ff3610de945da54b3c2bb for Railway services dcp-railway-test and oemixibot; no live switches authorized. Fresh exact identity/topology and predeploy backup gate MUST be checked before eventual release.
Flags OFF for existing production contract; do not infer current deployment config from code.

## Rollback / blockers
Before future release: preserve existing production SHA, verify backup SUCCESS→VERIFIED→restore PASS and rollback evidence, review staged diff, exact-head CI, visual acceptance and production identity. If failure: restore previous deployed SHA/config and verify health, requests and canonical DB without mutation by UI.
BLOCKERS: exact-head Linux combined PASS not confirmed; browser UI screenshots/desktop/mobile evidence missing; full legacy security integration and feature-flag-off regressions pending; offsite recovery destination SOURCE GAP; fresh predeploy gate pending.
NO MERGE / NO DEPLOY / NO PROD DB WRITES / NO S3 PUT-DELETE / PR #56 UNCHANGED.
