# EXTREMIZER RC1 COMPOSITION — 2026-10-17

## Purpose

Define the first common EXTREMIZER Release Candidate without giant-merging historical development branches.

RC1 is intentionally narrow.

## RC1 code checkpoint

Release Control base:
- branch: release-control-base-20261017
- HEAD: c9de8f385e16e5e31f14cca27650c64b49ffe95c

Polaris selective RC:
- branch: release-control-polaris-rc-20261017
- HEAD: b78cc11b7d40be4d0944c08ca8db79cf5c7b8ef0
- draft PR: #47
- CI: #38983583734 — SUCCESS
- mergeable: TRUE

RC1 composition branch:
- branch: release-control-rc1-20261017
- parent code HEAD: b78cc11b7d40be4d0944c08ca8db79cf5c7b8ef0

## INCLUDED IN RC1

### 1. Release Control base — inherited accepted code

The current base already contains, by ancestry, the previously integrated and accepted lines below.
They are not to be cherry-picked or merged again.

Inherited:
- PR #29 integration bundle, which already absorbed:
  - PR #24 WEB2 client OEM search v1
  - PR #25 unified Probnik/Procenka offer card
  - PR #26 Storage Stage 2A
  - PR #28 supplier fulfillment + warehouse settlements
- DCP reliability chain PR #32–#37
- OEMixi shared pricing/runtime chain PR #38–#45
- the five main-only WEB2 pre-deploy CI commits replayed onto Release Control base

Important:
"present in RC1 code" does not mean every dormant/cutover feature is activated in production.
Existing release/cutover holds remain in force unless explicitly lifted.

### 2. Polaris Dealer Price selective production slice — active RC1 target

Included as the only new functional slice on top of the Release Control base.

Frozen LIVE-proven source:
- release-freeze-polaris-dealer-price-20261017
- source HEAD: 30e6ca2c25759fb614930c45f7c8c9e292a4bbe4

Selective review:
- PR #47
- 20 functional commits + 1 release-control manifest commit
- 13 review files total
- CI #38983583734 — SUCCESS

LIVE acceptance:
2205250
→ Railway Category Authority
→ 2206328
→ Parts
→ Windows authenticated DCP
→ Dealer cart
→ $184.07

Safety state:
- status = ADMIN_DEALER_CART_PRICE_VERIFIED
- dealer_price_usd = NULL
- production_found = FALSE
- no unfinished probe journal
- dealer cache write remains HOLD
- global FOUND remains HOLD

## EXCLUDED FROM RC1

### WEB2 Client M4 — DEFER TO RC2 SELECTIVE SLICE

Latest accepted checkpoint:
- branch: web2-mobile-responsive-m4-20261017
- HEAD: 1713deb76a37fd0292c8094867fb1f699f8ab726
- CI #38983069580 — SUCCESS
- CI #38983069411 — SUCCESS

Reason to defer:
- independent client UX work, not required for Polaris release;
- branch is significantly divergent from Release Control base;
- device-level Android/iOS keyboard/touch acceptance remains open;
- final visual integration with Theme 3 is still a separate stage.

Decision:
Do not giant-merge M4 history.
Later extract a dedicated WEB2 selective slice onto an accepted RC base.

### Internal Order Core P5 — DEFER

Known heads:
- internal-order-p5-trusted-persistence-20261014
  d631e30ce398bacb7195ca518e2260067a51c36f
- internal-order-p5-trusted-terms-20261014
  40e70c4cd619e4f244e65c5a078cae7cf34ca74f

Reason:
- large divergent development line;
- changes touch trusted persistence/order semantics;
- not required to ship Polaris Admin Dealer Price;
- requires its own dependency-closed selective release audit.

### Client weight / independent WOPS RRP — DEFER / RECONCILE

PR #31:
- head: ab04b1a1a2f44d4defa454a71fa6b9e8eab4e5a2

Reason:
- three commits remain outside the current Release Control ancestry;
- independent client pricing/weight behavior;
- must be reconciled against later code before selective extraction.

### Bulk Order / Customs Stage 1 — DEFER

PR #27:
- head: c4332f00474c3ddb97f98d218c99232038f2404b

Reason:
- independent new subsystem;
- no dependency on Polaris;
- adding it increases RC1 blast radius without release necessity.

### Probnik five-chat welcome — DEFER

PR #30:
- head: 02ef8cb3904c265bd6deb9ead89c721010b5be66

Reason:
- independent behavioral feature;
- not required by RC1;
- branch is divergent and should be selectively reviewed separately.

### DCP autonomy feasibility — EXCLUDE FROM RUNTIME RELEASE

PR #46:
- head: b98e6f14e383824ef220c818d023d54415b78006

Reason:
- explicitly feasibility/validation evidence;
- unattended DCP access and production runner are not proven;
- not a runtime release candidate.

## RC1 PRINCIPLE

RC1 is not a collection of every green branch.

RC1 is:
1. one accepted common Release Control base;
2. one new production-required selective functional slice: Polaris;
3. no unrelated feature expansion.

This minimizes blast radius and makes rollback/review attributable.

## RC1 HOLDS

- NO direct giant merge to main.
- NO production deploy yet.
- NO dealer cache write.
- NO global Dealer FOUND persistence.
- NO Stage C2 activation.
- NO WEB2 M4 merge into RC1.
- NO Internal Order P5 merge into RC1.
- NO DCP autonomy runtime activation.

## NEXT RELEASE SLICES AFTER RC1

Recommended order after RC1 is accepted:
1. WEB2 M4 selective slice and device-level acceptance.
2. Internal Order Core P5 selective audit/slice.
3. Client weight / WOPS RRP reconciliation.
4. Other independent product features (Bulk/Customs, Probnik welcome, etc.).

Each must be dependency-closed and reviewed against the then-current Release Control base.
