# EXTREMIZER WEB2 M4 SELECTIVE RELEASE — 2026-10-17

## Purpose

Rebuild the accepted Theme 2 client milestones as a dependency-closed release slice on top of the final RC1 checkpoint, without merging the divergent WEB2 development history.

## Release base

- Base branch: release-control-rc1-20261017
- Base HEAD: 757d6b773bac1c5dd25f5360bce5535fefddf1e2

## Accepted Theme 2 evidence

M1:
- source HEAD: 9f0020741b641872aa977c24408c58a0fc71eca7

M2:
- source HEAD: 8acc760c40143f5c1951a8eb48c31c2b1ae07b24

M3:
- source HEAD: 6c86ab12035ecf492590affaaa4998840a96c7d0

M4:
- source HEAD: 1713deb76a37fd0292c8094867fb1f699f8ab726
- original CI #38983069580 — SUCCESS
- original checkout compatibility CI #38983069411 — SUCCESS

## Why this is file-level selective

The M4 branch and RC1 have divergent history. A direct merge would carry unrelated WEB2 finance/read-model/P2/P4 work.

The accepted functional line is:
- POWERSPORTS prep
- functional hardening
- M1 search
- M2 product card
- M3 cart
- M4 responsive

Only the runtime/test files needed to reproduce that client line are included here.

No FinanceEngine, OrderEngine, Polaris, DCP, Category Authority, production DB, or checkout backend files are changed.

## Runtime files

- web2/app.js
- web2/index.html
- web2/styles.css
- web2_staging_app.py
- web2_ui_routes.py

## Regression files

- test_web2_staging.py
- test_web2_client_mvp.py
- test_web2_functional_hardening.py
- test_web2_search_ux_m1.py
- test_web2_product_card_m2.py
- test_web2_cart_m3.py
- test_web2_mobile_m4.py

Existing RC1 tests reused unchanged:
- test_web2_browser.py
- test_web2_ui_routes.py
- test_web2_host_integration.py
- test_web2_contract.py

## Checkout boundary

The historical test_web2_procenka_checkout_e2e.py is NOT part of this selective slice because it targets a separate newer checkout/backend line that expects APIs not present in the current RC1 backend.

This slice does not alter checkout backend semantics.

Handoff compatibility is gated here by the current RC1 host integration and WEB2 contract regressions.

## Device-level holds

The accepted M4 limitations remain:
- physical Android/iOS virtual keyboard behavior not yet verified;
- physical touch ergonomics not yet verified;
- instrumental CLS not measured;
- separate long-cart stress test not performed.

These are pre-production/device acceptance items, not reasons to widen this code slice.

## Release boundary

NO MERGE.
NO DEPLOY.
NO PRODUCTION.

Exact-head GitHub CI is required before Release Control acceptance.
