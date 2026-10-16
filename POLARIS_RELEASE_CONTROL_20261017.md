# POLARIS RELEASE CONTROL — 2026-10-17

## Frozen live-proven candidate
- Source branch: release-polaris-cross-host-integration-20261017
- Frozen source HEAD: 30e6ca2c25759fb614930c45f7c8c9e292a4bbe4
- Freeze branch: release-freeze-polaris-dealer-price-20261017
- Source CI: #38980237145 — SUCCESS
- LIVE acceptance: PASS

## Live acceptance evidence
- requested_oem: 2205250
- resolved_oem: 2206328
- manufacturer: Polaris
- item_category: Parts
- admin_unit_price_usd: 184.07
- status: ADMIN_DEALER_CART_PRICE_VERIFIED
- source: polaris:authenticated_dealer_cart:normal_browser
- dealer_price_usd: NULL
- production_found: FALSE

## Selective release base
- Historical accepted modern base: 214e7761a84d4413ba4a81bfd9b3e9e554362e60
- Main-only CI commits replayed onto the Release Control base:
  - c8951b5 CI: register manual WEB2 pre-deploy gate
  - 8bb536f CI: fix WEB2 runner import path and summary
  - 0b33f4f CI: gate exact manually selected branch and commit
  - bde5d62 CI: add explicit WEB2 target ref input
  - a86d914 WEB2: run exact candidate against frozen regression suite
- Release Control base branch: release-control-base-20261017

## Polaris release slice
The Polaris slice is exactly the 20 commits from the accepted 214e7761... line through
30e6ca2..., replayed on top of release-control-base-20261017.

Selective functional surface before this manifest/CI-trigger commit:
- 20 commits
- 12 files
- +1348 / -9

The slice contains only:
- production Category Authority wiring and seed;
- signed Railway read-only Category Authority endpoint/client;
- Windows ADMIN Polaris provider wiring;
- compensated probe-cart mutation enablement;
- strict restored-cart acceptance;
- canonical OEM probe after Railway replacement resolution;
- tests and isolated Polaris CI wiring.

## Release policy
DO NOT merge release-polaris-cross-host-integration-20261017 directly into main.
DO NOT giant-merge the 406-commit divergent history into main.

Use this review boundary:
release-control-base-20261017
→ release-control-polaris-rc-20261017

Future common-release composition must add only accepted component slices onto the Release Control
base/RC, each with exact source/base HEADs, dependency check, CI gate and no implicit branch-history merge.

## Holds
- NO production deploy from this branch yet.
- NO dealer cache write.
- NO global production FOUND persistence.
- NO Stage C2 activation.
- NO main merge.
