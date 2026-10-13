# DCP Stage 5 — Release Control dossier

**VERDICT: BLOCKED FOR ACTUAL FIRST SWITCH / ISOLATED REHEARSAL PASS / NO PRODUCTION CHANGE.**
Prepared 14 October 2026 (Europe/Warsaw). Runtime observations below use UTC.
Stage 1–4 accepted implementation is unchanged. This Stage 5 branch adds verification fixtures/tests/documentation only. A validation PR is not a second runtime release candidate.

## 1. Release identity and scope

Repository: Extremizer/dcp-railway-test. Fresh GitHub release base remains `74ce11eb864ee4d852c60e9ffdb140ef73a46517`; no base drift observed. ONE proposed runtime candidate: PR #35, OPEN/DRAFT/mergeable, HEAD `16e3fa34f1e2659aa6c1e6d85b914371253bfe6d`, tree `7e9a043c14c5b4f40f0eb126ac846b53417d0eae`, CI 7/7 SUCCESS. Ancestry: #32 `2ed283ac...` → #33 `3325f309...` → #34 `e8d45e2f...` → #35. Do not independently merge each stacked PR.

PR #31 WOPS remains Draft/HOLD, HEAD `ab04b1a1a2f44d4defa454a71fa6b9e8eab4e5a2`, excluded. No other theme is implicitly included. Stage 5 exact verification HEAD/CI is recorded in the validation PR and final handoff because this document cannot contain its own future commit SHA.

## 2. Exact legacy v1 paths / first-switch findings

Source audit uses the exact repository tree `11a545f1180635d1dcf0feffda5212907766e466` at deployed source SHA `74ce11eb...`. This is source evidence, not proof that every production file is unmodified on disk. `dcp_legacy_v1_fixture.json` pins selected original functions and original full-file SHA-256; no installed runtime code is imported in tests.

| Consumer / producer | v1 path | Admission implication |
| --- | --- | --- |
| Probnik | probnik_app._identity_and_price → dp_live_bridge.request_live_dp on identity/DP cache miss | Can enqueue while producer stopped |
| Procenka cache-only | enrich_found_result_with_dealer_price → shared bridge on DP miss | cache_only does NOT mean no live-DP enqueue |
| Procenka non-cache-only | dcp_private_price.get_dealer_price + finder search path | Independent browser path; stopping broker agent is insufficient |
| Shortage USA quote | manufacturer_callback → request_live_dp on cache miss | Another writer, independent of normal enrichment |
| WEB1 OEM API | build_oem_card → public catalog cache + existing DP cache | Audited current endpoint has no live-DP enqueue; do not assume arbitrary other routes are writers |
| OEMixi default | oemixibot_catalog.resolve_offer → oemixibot_dcp_price directly | Outside shared broker; active runtime NOT PROVEN |
| Local producer | dp_sync_agent run_once → v1 GET next → local price helper → v1 POST result | Agent retirement does not close consumer admission |
| Server runtime | web1_runtime starts main bot, Probnik, WEB and backup process; no OEMixi startup | Stopping one supervised child terminates the other children; selective maintenance cannot be assumed |

Legacy broker health enforcement is conditional on EXTREMIZER_DP_HEALTH_ENFORCE. Persisted READY can remain live-allowed for 5400 seconds after reporting stops. It admits pending work without a producer. Legacy timeout marks pending AND claimed older than 30 seconds finished, even if the actual browser operation could still run. Empty claimed count/TIMEOUT is not no-inflight proof. Legacy health v1 signature is not body-bound; READY writers and independent direct paths make a temporary health status an insufficient freeze.

**Confirmed by actual legacy code tests:** stopped mock agent + real cache-only Procenka function still enqueues; old claimed timeout can hide an externally active lookup. Stage 4 freeze exists only in the new runtime; adding its tables to an old DB does not make old code obey them.

## 3. Actual runtime evidence matrix

| Item | Fresh fact / source | Limit / gate |
| --- | --- | --- |
| Railway source | dcp-railway-test, production, source pin 74ce11eb864ee4d852c60e9ffdb140ef73a46517 | GitHub branch is not used as runtime proof |
| Deployment | 16eeacbe-d2a4-425a-8f45-6d87eb23d159, SUCCESS, created 2026-10-11T19:48:17.804Z | MCP replica count unavailable; do not restate 1/1 as a fresh measurement |
| Staged/pending | staged changes 0; pendingWork [] | No source switch performed |
| Start command / storage | python -u web1_runtime.py; /data volume | One service supervises multiple writers |
| Platform variables | EXTREMIZER_DCP_MODE and EXTREMIZER_DP_HEALTH_ENFORCE present | OAuth hides values: actual values NOT PROVEN in Stage 5 |
| Recent producer polling | 30 bounded HTTP samples: GET /internal/dp-sync/next 200, 2026-10-13T22:59:19Z–23:01:23Z | Not complete traffic counts, queue state, producer identity or absence of another producer |
| Windows observation | 2026-10-13T23:05:09.5261608Z, EXTREMIZER device online | Read-only audit; initial transient offline recovered |
| Agent process | python.exe PID 42360, parent cmd.exe 31088, start 2026-10-09T18:07:36.6664660Z | One matching process observed, not global singleton/no-browser-operation proof |
| Scheduled task | Extremizer DP Sync Agent; wscript.exe; working directory C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002 | Task Ready does not mean its detached agent is stopped |
| Startup wrapper | parent references START_DP_SYNC_AGENT.cmd in that directory; sets %~dp0 directory; references dp_sync_agent.py | Wrapper SHA 5036a67578fe4248bbbb2d40f1964fec4a470c82260e5fefe463af1bc7187a1b |
| Installed on-disk agent | 13515 bytes, SHA cdebd6be4c51bbda806534202dd1aef7b7624c2c7e87d4fe981385ba550b68f2; matches legacy source | In-memory loaded file revision cannot be guaranteed just by current disk hash |
| Local health | online; PID 42360; READY checked 2026-10-13T22:57:21+00:00; status updated 23:05:07+00:00 | READY does not prove idle / stopped / no queued work |
| Other local Python | No unmatched Python process in this bounded Windows observation | Other hosts/interpreters/services remain unproven; OEMixi runtime NOT PROVEN |
| Installed v2 dependencies | dp_sync_protocol.py, dp_result_completion.py, dp_live_admission.py absent in installed directory | New eight-source-file bundle is not installed |
| Local v2 journal | No v2 journal observed in installed v1 directory | v1 has no durable v2 uncertainty journal; absence is not no-uncertainty evidence |
| Production queue/health/file inventory | Read-only SSH inventory returned exit 1 with no usable JSON; no app imports or write commands in probe | NOT PROVEN. Subsequent minimal diagnostic result was not observed; not used as evidence |
| Backup/restore | Disposable test PASS; historical checkpoint 20261011T161707Z accepted in prior release | No fresh production checkpoint/round-trip/restore proof obtained or executed in Stage 5 |

Railway CLI on Windows reports 5.62.1. No CLI/agent install, update, start/stop, browser navigation or real DCP request was performed. Read-only audit shell processes are distinct from starting an agent. SSH used the existing registered key; no private key was extracted or new key created.

### Production runtime inventory: expected source vs actual disk

Actual container hashes remain NOT PROVEN. The following exact source hashes are from the repository at the platform's deployed SHA, not asserted as observed container files:

| File | Repository SHA-256 at 74ce11eb |
| --- | --- |
| web1_runtime.py | f54c3179ceb95ff577e15d49d7f3a8f09709971656f50d9d253e619a61fb63d1 |
| web_app.py | 255ad26b4ace8810b78df4ee821f363e435e286f4889f9764f226666c487ff3f |
| extremizer_bot.py | b6f6a67028e01ef7c5939f7c1d52ca005faff38b356df00389c99106c57f73e9 |
| probnik_app.py | edd1b4fa172221bfce598d8a275271b23867e2b72118e0f07f3f676e630b64c0 |
| dp_live_bridge.py | a312a34da99ce6172fe5b2afafcddd19ff799a8856c872af71c634fcdce2c3d7 |
| dp_live_health.py | e3f913c32694321b50a3189c699cae02a41120e65fef2ded16a9b757e11af4f6 |
| dp_sync_agent.py | cdebd6be4c51bbda806534202dd1aef7b7624c2c7e87d4fe981385ba550b68f2 |
| oemixibot_catalog.py | 186f0b6be0eae0d209e91739bdce2a16c87ec9e11669df232619c04af4fdaaaa |
| oemixibot_dcp_price.py | 9d8dc154e2ae4041afd1e6eb4862ba5760d5266a49bcbce0f5ae9160a45d0535 |
| dcp_private_price.py | a66e7cbcfe1b2cfeee9272cc309a284f67cc9e5f2604c7725b886b9f1c560b25 |
| oemixibot_finance.py | 17546efcea563f0f3e1942947f085e31ed6d88a9ee6401e2c18147171f57d863 |

## 4. Proposed first-switch safety plan — owner approval required, NOT EXECUTABLE GO

There is no demonstrated online legacy admission freeze. Prefer owner-approved full maintenance fallback over claiming that cache_only, agent stop, an HTTP block alone or zero rows proves cessation. It interrupts client bots/WEB and may pause the supervised backup process; timing, communication and restoration are owner decisions. An unsupported platform pause/scale-to-zero command is not invented here.

Before any switch: independently inventory ALL writers/producers, including external/direct OEMixi paths and background/manual callers. Select and separately validate a supported operational maintenance mechanism. Obtain the owner window and exact actions/scope. No actor, stop command or current window is authorized by this dossier.

A possible controlled sequence, conditional on separately proven stop/drain mechanics:

1. Prevent new actions from all known legacy writers through an owner-approved service/process maintenance mechanism; independently confirm no unknown/direct writer. Blocking WEB ingress alone does not stop Telegram/background work.
2. Prove no old browser lookup/result delivery in flight and reconcile old requests while the old result receiver is available. Do not terminate an active agent merely because claimed count is zero.
3. Retire the old producer only after separate AGENT permission and inactivity proof. Its stop alone never substitutes for step 1.
4. If any pending/claimed/unknown result remains, STOP. Do not delete the production queue or requeue; obtain a separate handling decision. Supervisory all-child termination can break old result delivery, so a selective-stop/drain mechanism must be proved before using it.
5. With verified cessation and independent evidence, obtain the separately authorized fresh checkpoint and disposable restore proof. Only then consider SERVER DEPLOY for the exact v2 candidate.
6. New server initializes CLOSED. Verify actual revision/schema, no unexpected enqueue/claim and preserved cache/finance. Install/start the pinned single v2 bundle only after AGENT INSTALL/START GO and old producer retirement proof.
7. Signed v2 empty-queue smoke on CLOSED; independent review of queue/journal/browser/process evidence.
8. Separate ADMISSION OPEN approval; separate LIVE E2E GO with one OEM/request budget. An OPEN approval is not LIVE E2E permission.

**If operational cessation/drain is not proved, remain BLOCKED.** The mocks prove the conditional safe sequence and failure gates, not the physical maintenance mechanism on today's production. Selecting/implementing that mechanism is a separate owner-authorized task; no Stage 6 starts automatically.

## 5. Paired rehearsal / migration / rollback evidence

12 isolated Stage 5 tests use exact pinned legacy bridge/Procenka/v1 agent functions and actual v1/v2 HTTP handlers, fake browser and disposable SQLite. The test maintenance flags model independently stopped writers; they are not runtime feature flags or service commands.

Verified sequence: active v1 server/mock agent -> all mock writer cessation + independent artifact -> old mock producer stopped -> Online Backup + size-independent SHA round-trip/integrity -> v2 additive bootstrap CLOSED -> no enqueue/claim -> v1 denied + signed v2 empty smoke -> one mocked v2 producer -> separate signed RC OPEN -> separate mock LIVE E2E -> cache hit without second lookup.

Fault gates: only agent stopped, remaining/unknown writer, missing owner window or independent evidence, old stale claimed row, unreconciled queue, bootstrap audit failure, fenced rollback and absent LIVE GO stay BLOCKED. Existing Stage 3–4 suites cover lost ACK/journal, signed approval tampering/expiry/audit rollback, CF/AUTH and concurrency; they were used as compatibility regressions, not redeveloped.

Additive migration/restart preserves old request projection, verified DP cache, orders and finance ledger. Audit failure leaves admission fail closed and business projections unchanged. Some additive DDL can persist after init failure; FULL SCHEMA ROLLBACK is not claimed. Reconcile schema and retry initialization under separate authority; never open just to bypass an incomplete bootstrap.

Disposable restore PASS is not blanket rollback permission: a demonstrated later legitimate order is absent from an older checkpoint. Production restore requires accounting/reconciliation of all subsequent business writes. A v1 rollback with pending/fenced/orphan/unknown journal state is forbidden; the old server cannot enforce CLOSED, so downgrade cannot be used as implicit OPEN. Hold v2 CLOSED until separately reviewed recovery/rollback.

## 6. Release Control authority contract — proposed roles, no appointments/keys

| Role | Authority / duties | Separation |
| --- | --- | --- |
| Release approver | Owner-selected exact candidate, window, budget and separate GO decisions | Does not self-certify execution evidence |
| Admission signing authority | Approved open/close scope, exact plan/revision and independently reviewed evidence | Distinct from routine execution; no AI-generated production keys |
| Recovery operator | Approved plan/evidence, inactive producer confirmation, fenced revoke without retry | Cannot approve own recovery or silently reset state |
| Deployment operator | Executes only valid SERVER DEPLOY GO for exact SHA | Does not grant own GO or change scope |
| Agent installation operator | Pinned source/dependency package and singleton proof under separate GO | No second producer or implicit lookup |
| Independent reviewer | Runtime/queue/journal/checkpoint/hash evidence, post-gate review | Different from executing/signing the reviewed operation |

Owner must name identities/capabilities. A small-team exception to separation requires an explicit documented owner decision; it is not assumed. Every approval identifies actor, exact artifact/revisions, environment/action scope, evidence, start/expiry and result. Admission approval already expires within 10 minutes; deploy/install/live/recovery windows need explicit owner expiry, not indefinite GO. An expired approval is not renewed by READY/restart.

Audit retention proposal: keep append-only approval/event/evidence chain for the complete release/claim/recovery lifecycle, with owner-selected retention period and access policy; hashes alone do not replace the independent artifacts. Neither retention period nor operators were assigned here.

Revocation: cancel unexecuted GO and stop subsequent gates; never undo an in-flight operation blindly. Current Stage 4 HMAC uses a shared signing/verification secret: an executor holding that secret can also mint signatures. Cryptographic separation of signer and executor is therefore NOT implemented. Immediate per-approval revocation registry/key version is also absent. Before production authority rollout the owner must choose a dedicated controlled signer/verifier arrangement or asymmetric verification and an audited revocation policy (or explicitly accept a documented trusted-operator limitation). No key is created, read, rotated or installed in Stage 5. Mock test keys are not production approval authority.

Separate GO types: SERVER DEPLOY; AGENT INSTALL/START (and any old-agent retirement explicitly scoped); ADMISSION OPEN; LIVE E2E; RECOVERY/ROLLBACK. CI is none of these approvals.

## 7. Pinned server/agent source manifests / other themes

Runtime release remains exact PR #35. Producer source SHA: `a63b45406261e7d23fc9d0c782088a9f3874d66b2e1d2e930e54b1c12bff0895`. Eight-file source manifest SHA: `2a6334d2e03f2f81d2aeca3c088ae4ac9bbdc536d13920cfe41b615cf6ff8b5a`. Full per-file manifest is in DCP_STAGE4_RELEASE_GATE_PLAN.md. Includes new dp_live_admission.py; installed directory lacks required v2 dependencies. Source manifest is not a Windows installer/Python dependency hash; exact dependency/install artifact evidence remains required.

| Other work | Observed overlap with #35 | Decision |
| --- | --- | --- |
| #31 WOPS | workflow, finance guard, agent, Procenka, DCP browser helper, web_app | HOLD / excluded; no whole-file resolution or combined CI claim |
| #30 Probnik welcome | _prod_finance_switch_regression.py | Draft/excluded; preserve both guard sets in a future separately approved composition |
| #27 Bulk/Customs | No direct changed-file overlap in current PR list | Excluded; absence of overlap does not prove runtime/schema compatibility |
| Other unpublished Theme 1/2 work | No exact final candidate provided in this Stage 5 handoff | Not included or declared compatible |

No changes to web_app.py, extremizer_bot.py, agent, broker/admission/preflight, FinanceEngine or accepted scope guards relative to #35. Stage 5 validation adds only pinned fixture, mock helpers/tests, dossier and CI test step.

## 8. CI and owner GO/NO-GO matrix

PR #35 exact-head CI 7/7 SUCCESS rechecked: DCP Health Core; Dealer Finance; Startup Safety; WEB ADMIN; Storage; Supplier Fulfillment; WEB2. New verification HEAD/CI and local totals appear in the final handoff.

| Gate | Current result | Required owner/RC decision/evidence |
| --- | --- | --- |
| Isolated code + mock rehearsal | PASS | Review limitations; not physical shutdown proof |
| Actual all-v1-writer cessation | NOT PROVEN / NO-GO | Choose/validate maintenance mechanism and window |
| Independent browser/HTTP no-inflight | NOT PROVEN / NO-GO | Independent current evidence; not READY or zero rows |
| Actual production queue/health/container bytes | NOT PROVEN / NO-GO | Authorized read-only access with usable results |
| Old producer/source/singleton | One observed v1 process + disk hash | Fresh retirement/no-other-host evidence under separate permission |
| Candidate package | Source pinned; installed bundle not updated | Approved dependency/install package, exact pair |
| RC authority / revocation | Proposal only / NO-GO | Owner role assignments/key-custody design/revocation policy |
| Fresh checkpoint/restore | Disposable PASS; production freshness NOT PROVEN | Separate fresh verified production evidence |
| SERVER DEPLOY / AGENT INSTALL / OPEN / LIVE / RECOVERY | NOT AUTHORIZED | Separate exact scoped GO, never implied by this dossier |

**Final actual first-switch verdict: BLOCKED. RC dossier and conditional mock evidence are ready.** No service stop, producer start/stop, production freeze/cache/DB write, merge/deploy, real DCP, browser control, OEMixi switch, WOPS inclusion or production key action occurred. STOP / HANDOFF; no Stage 6 or rollout.
