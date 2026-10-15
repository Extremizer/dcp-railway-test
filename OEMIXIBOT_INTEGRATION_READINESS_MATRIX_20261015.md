# OEMixiBOT / DCP Integration Readiness Matrix — 2026-10-15

Status: integration-readiness record only. No merge, deploy, production migration, Windows poller stop, LIVE DCP, or cart mutation.

## Theme 6 — DCP reliability chain

| Dependency | Exact HEAD | Readiness | Existing exact-head evidence | Combined test still needed | Current blocker | OWNER GO |
|---|---|---|---|---|---|---|
| PR #32 — challenge stop + shared request coordination | `2ed283ac68ac1cfbc1252bd9120f98d7d9494fd5` | isolated green | DCP Health, Startup Safety, Storage Writers, Dealer Finance, Supplier, WEB ADMIN all SUCCESS | Integrated Theme6→Shared Gateway request/claim rehearsal | Not yet accepted into one Release Control branch | Needed before production first-switch |
| PR #33 — atomic completion + fenced recovery | `3325f30925ddbb662507cc95b6322625098bb90f` | isolated green | 7 exact-head workflows SUCCESS | Combined atomic completion with Theme1 cache/commit provenance | Release Control integration | Needed before production first-switch |
| PR #34 — producer v2 + single-writer cache | `e8d45e2f46bda0267b8595283befb704786142a9` | isolated green | 7 exact-head workflows SUCCESS | One-producer proof with three consumers through final Gateway | Release Control integration | Needed before producer switch |
| PR #35 — admission freeze + paired release gates | `16e3fa34f1e2659aa6c1e6d85b914371253bfe6d` | isolated green | 7 exact-head workflows SUCCESS | Final Gateway must fail closed while admission CLOSED and pass only after approved OPEN | Admission first-switch decision | Needed for admission opening |
| PR #36 — legacy bootstrap + Release Control evidence | `2a2d64184465e66ef817f2f6e4dda8b414d3c467` | isolated green | 7 exact-head workflows SUCCESS | Combined bootstrap/checkpoint/queue preflight on integrated RC | Integrated candidate absent | Needed before production switch |
| PR #37 — passive document guard + transport isolation | `5a26ab78ebb8eb7862da7816580a577000d8579d` | isolated green | 7 exact-head workflows SUCCESS | Integrated passive-guard + Theme1 Gateway error propagation | Integrated candidate absent | Needed before LIVE DCP |

Unclosed Theme 6 dependency: the code is individually green, but **PR #32–37 have not yet been assembled and accepted as one Release Control integration point with Theme 1**. Theme 1 does not merge/cherry-pick this chain independently.

## Theme 1 — OEMixi / Shared Gateway / Railway chain

| Dependency | Exact HEAD | Readiness | Existing exact-head evidence | Combined test still needed | Current blocker | OWNER GO |
|---|---|---|---|---|---|---|
| PR #38 — shared price Gateway runtime | `4469af37b40acfe70a5ec7030acc0f023bdec6d4` | accepted isolated | exact-head 7/7 workflows SUCCESS | Included through later Theme1 chain | Not merged | Release GO required |
| PR #39 — Admin OEM Search | `f19921878d3a3664946f537f702154870a20e9c2` | accepted | CI #38707249796 SUCCESS | Admin search through final integrated Gateway | Integrated RC absent | Release GO required |
| PR #40 — Canonical Dealer/MSRP contract | `868efd70f56ddba70ce79e9e1705d51f1aec6007` | accepted | CI #38717965381 SUCCESS | Combined with final Theme6 producer/cache source | Integrated RC absent | No separate GO for mocks; release GO for production |
| PR #41 — Secure Gateway Stage C1 | `46199eb5adc88c358240e9ebf28af60682a96a6f` | superseded by accepted corrections | CI #38724191302 SUCCESS | Use PR #42 corrections in RC | Two P0 defects fixed downstream | Release GO required |
| PR #42 — Stage C1 targeted corrections | `ce6927c88689f1a13c382e6909061edc5cdd9659` | accepted | CI #38755968522 SUCCESS | Combined cache-hit/atomic-commit evidence with Theme6 PR #33/#34 | Integrated RC absent | Release GO required |
| PR #43 — Railway 24/7 candidate | `9fc7d69f60d1cba8ae3008c79c983c74402846c7` | accepted isolated | CI #38761029757 SUCCESS | Railway service rehearsal on integrated server Gateway | No production service/volume; no migration | OWNER GO required for real migration/cutover |
| PR #44 — Release Preparation | `b442c34014eeaaad13092e949f7d954696d13e48` | accepted | CI #38776868614 SUCCESS | READY→STOPPED follow-up + integrated Theme6 RC | Readiness follow-up found new defect, fixed in follow-up branch | OWNER GO required for Windows stop/fence transition/deploy |

Theme 1 current release boundary remains:

- one Railway OEMixi replica;
- dedicated persistent volume;
- server-side poller fence;
- Windows stop independently proven;
- fence transition only after stop evidence;
- Shared Gateway only;
- no direct DCP fallback.

## Theme 5 — Stage 2 category-routing evidence

Archive SHA-256:

`bac8e534af575e9c74be145e36dd27b3db9359956b160ac22681cb0fea2adeda`

| Dependency | Exact identity | Readiness | Existing/new tests | Combined test still needed | Current blocker | OWNER GO |
|---|---|---|---|---|---|---|
| Category Routing Contract | `DCP_CATEGORY_ROUTING_SPEC.md`, archive above | research contract accepted for mocks | 16/16 fixture coverage after follow-up | Future Stage C2 route-specific tests only after confirmed evidence | LIVE routes UNKNOWN/BLOCKED | Required before any cart-dependent live validation |
| Cart 2206328 Plan | `DCP_CART_2206328_TEST_PLAN.md`, archive above | PLAN ONLY | OWNER-GO/no-mutation and cart-baseline/conflict fixture guards | Controlled real test in a separately authorized maintenance window | Shared real cart; stable line attribution not independently verified | **Explicit OWNER GO mandatory** |
| 16 Mock Fixtures | `DCP_THEME1_MOCK_FIXTURES.json`, archive above | isolated test evidence | 9 existing + 7 new non-duplicate safety regressions = 16/16 | No LIVE combined test authorized | OWNER_SCREENSHOT is not LIVE verification | No GO needed for mocks; GO required for real cart/live route |

## Combined Release Control test set required next

After Theme 4 accepts this follow-up, Release Control should assemble one non-production integration branch and run only the combined checks not already proven independently:

1. Theme6 PR #32–37 + Theme1 PR #38–44 + this follow-up, preserving dependency order.
2. Three consumers → one final Shared Gateway → one Theme6 claim/producer.
3. Cache hit vs new atomic completion provenance through the integrated Theme6 implementation.
4. Admission CLOSED → all consumers fail closed with no direct fallback.
5. Approved OPEN rehearsal → exactly one bounded producer request.
6. Railway readiness lifecycle: STARTING → READY → POLLING STOPPED → NOT READY → restart.
7. Server poller fence remains WINDOWS throughout non-production rehearsal unless an isolated disposable fence DB is used.
8. Theme5 16 fixtures remain mock-only; no category fan-out and no cart mutation.
9. FinanceEngine/client checkout regressions after integration.

## Current blockers before production

- Theme6 PR #32–37 not yet accepted as one integrated Release Control chain.
- No combined Theme6 + Theme1 Release Control branch/CI yet.
- Server Gateway mount not enabled in production.
- OEMixi Railway service and dedicated volume not created.
- Real three-DB migration not authorized.
- Windows poller has not been stopped and must remain running now.
- Poller fence must not transition to RAILWAY before independent Windows-stop evidence.
- Theme5 LIVE category routes remain BLOCKED/UNKNOWN.
- Any DCP cart mutation requires a separate OWNER GO.

## Release rule

This matrix is **not** production authorization.

NO MERGE / NO DEPLOY / NO PRODUCTION MIGRATION / NO WINDOWS POLLER STOP / NO LIVE DCP / NO CART MUTATION.
