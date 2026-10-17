# EXTREMIZER RC1 FINAL RELEASE AUDIT — 2026-10-17

## Decision

RC1 DEPLOY DECISION: **NO RAILWAY DEPLOY REQUIRED / NO-OP RELEASE**

Do NOT switch the Railway service source branch to this RC1 branch.
Do NOT merge the 229-commit RC1-vs-production delta into the running Railway service.

Polaris is already live on the proven split topology:
- Railway keeps the currently deployed Category Authority server.
- Windows keeps the currently running OEMixiBOT + authenticated DCP browser provider.

## Exact-head CI

RC1 pre-audit HEAD:
- 4f25030147acc22bb2a617f72eb844da2c198929
- Theme1 Polaris Probe Cart Isolated #38986015980 — SUCCESS

All Polaris compile, transaction, identity, Category Authority cross-host, and Railway startup gates passed.

## Production topology

Railway project:
- accurate-insight
- production
- service: dcp-railway-test
- volume: /data
- start command: python -u web1_runtime.py

Current live Railway source:
- branch: release-category-authority-server-20261016
- commit: 3940961ba635124afac3620f9b6e1ae35c38f3ad
- deployment: 46265e3c-6988-4be2-880a-0a51af3e21f3
- status: SUCCESS / ONLINE
- current deployment canRollback=true / canRedeploy=true

Production freeze:
- release-freeze-production-pre-rc1-20261017
- 3940961ba635124afac3620f9b6e1ae35c38f3ad

## Railway Polaris delta

Required Railway-side Polaris functionality is already present in current production.

Byte-identical between current production 3940961... and RC1:
- web1_runtime.py
- oemixi_category_authority_transport.py

Current production web_app.py already mounts the signed read-only
/internal/oemixi/category-authority/v1 transport.

The production Category Authority endpoint was used successfully during the
real LIVE acceptance:
2205250 -> Railway -> 2206328 -> Parts -> Windows DCP -> $184.07.

Therefore:
**MINIMAL REQUIRED RAILWAY POLARIS DELTA = ZERO.**

No Railway code deployment is needed for Polaris.

## Why RC1 must not be deployed as a Railway branch

Direct comparison:

3940961... -> RC1 4f250301...

- diverged
- +229 commits / -8 commits
- 126 files
- approximately +15,965 / -314 lines

This is not a safe Polaris-only production delta.

## Windows live runtime freeze

Authoritative freeze branch:
- release-freeze-windows-polaris-runtime-20261017
- HEAD: 9d4dfd970cc29f1decd0c37cfb117866b491cd5a

Byte-stable recovery archive:
- release_artifacts/EXTREMIZER_WINDOWS_POLARIS_RUNTIME_FREEZE_20261017.zip
- SHA-256: 1253CF3AB62EBEC69CCE847FA16B6F1A27CEE016C7FE29D1ED875F1BC480AC79

Recovery verification:
- 12/12 file hashes match the live Windows runtime
- all frozen Python modules compile successfully
- startup script contains environment-variable references only; no secret values are stored

The live Polaris-only integration relative to the saved pre-Polaris Windows runtime is narrow:
- oemixibot_app.py: +131 / -2
- start_oemixibot_private.ps1: +2
- Polaris provider modules are frozen with the runtime package

## LIVE acceptance evidence

- requested OEM: 2205250
- resolved OEM: 2206328
- manufacturer: Polaris
- category: Parts
- admin dealer price: $184.07
- status: ADMIN_DEALER_CART_PRICE_VERIFIED
- source: polaris:authenticated_dealer_cart:normal_browser
- dealer_price_usd: NULL
- production_found: FALSE
- no unfinished probe journal
- OEMixiBOT remained online after acceptance

## Backup gate

Last successful verified backup before the current Railway deployment:
- snapshot: 20261016T203201Z
- DAILY: DAILY_20261016_203830
- journal: SUCCESS
- verification: VERIFIED
- restore test: PASS
- production_touched: false

A new post-deployment backup attempt was intentionally run during this audit.
It failed closed:
- SNAPSHOT_NOT_READY
- Railway SSH transport/authentication path did not become ready
- no new accepted snapshot was created
- no new DAILY checkpoint was created
- no false SUCCESS journal was written

The approved backup launcher was restored after diagnostic experimentation.

### Backup policy decision

Because RC1 requires **no Railway deployment and no /data mutation**, this open
backup transport issue does not require changing the already-live Polaris state.

However it is a HARD BLOCKER before the next real Railway deployment or
write-capable migration.

Before any future Railway deploy:
1. repair Railway SSH/snapshot transport in isolation;
2. create a fresh snapshot after the current production deployment;
3. require SUCCESS + VERIFIED + restore PASS;
4. only then permit deployment.

## Rollback

Railway rollback target:
- deployment 46265e3c-6988-4be2-880a-0a51af3e21f3
- commit 3940961ba635124afac3620f9b6e1ae35c38f3ad
- canRollback=true
- canRedeploy=true

Windows rollback:
- saved pre-Polaris OEMixiBOT runtime backup exists
- byte-stable live Polaris recovery archive is frozen in GitHub

## Final RC1 release state

POLARIS:
- implementation: CLOSED
- LIVE E2E: PASS
- Railway server: ALREADY LIVE
- Windows ADMIN provider: ALREADY LIVE
- release artifact: FROZEN
- recovery verification: PASS

ACTIONS:
- NO Railway branch switch
- NO Railway deploy
- NO main merge
- NO dealer cache write
- NO global Dealer FOUND persistence
- NO Stage C2 activation

RC1 is accepted as a **state-freeze / no-op deployment release** for Polaris:
the proven production topology remains unchanged.

The next actual Railway deployment remains blocked until the backup transport
gate is repaired and re-verified.
