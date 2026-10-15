# DCP autonomous price infrastructure — isolated feasibility

Date: 2026-10-15. Scope: Theme 4 → Theme 6. **RELEASE HOLD.**

Direct source parent: PR #37, `5a26ab78ebb8eb7862da7816580a577000d8579d`,
tree `cd4d3bd2eb4a3f7052860bc8e77db444282e67ff`.
PR #35 remains the previously accepted runtime candidate; this validation does
not replace it, merge the DCP stack, or modify existing PR branches.

## Verdict

**WINDOWS-OFF INTERNAL MOCK PIPELINE: PASS.**
**AUTHORIZED UNATTENDED DCP ACCESS: NOT PROVEN / EXTERNAL BLOCKER.**
**FULL AUTONOMOUS RUNTIME / LIVE E2E / FIRST SWITCH: NOT READY.**

Twelve focused Linux tests reuse accepted producer v2, signed HTTP handlers,
SQLite broker, atomic completion and three actual adapter function bodies.
They do not start a browser, connect to Windows, call DCP or start Telegram.
They also record two implementation gaps instead of disguising them as rollout
acceptance: public MSRP reader freshness and Linux process singleton.
Green validation CI is not permission to deploy and is not proof of real DCP
availability. All Telegram bots running continuously and new supplier prices
being available continuously are separate capabilities.

## 1. Access alternatives and evidence

### Official/authorized API or price feed — preferred if supplier grants it

Public indexed searches were made for the official DCP domain using API,
integration, data feed and EDI terms, with a second search engine to check recall.
No usable official API specification, feed contract, onboarding instructions or
unattended service credential scheme was found. Results that mention API oil
standards or oil-feed parts are not API evidence. The indexed homepage identifies
vNext Technologies as site platform; that does not establish that DCP exposes an
API or that a platform integration can access this account's dealer prices.

**NOT PROVEN does not mean no private partner API exists.** No undocumented
endpoint probing, account login, API requests, DCP product lookups, or contact
messages were performed. Public search results are discovery only, not supplier
approval. Official site reference: https://www.dealercostparts.com/ .

Before building an API/feed producer, supplier must confirm:

1. Permission for automated read-only use and redistribution/display of the
   relevant account's dealer prices; brands/categories and account price tiers.
2. Documented endpoint or delivery transport, schema, version and access scope.
3. Dedicated unattended credentials and renewal/revocation rules, without
   borrowing a person's browser session or exposing Telegram credentials.
4. Exact manufacturer, query OEM, matched/current SKU, dealer-price field,
   USD currency, verification time and supersession mapping. Retail/MSRP must be
   separate. A changed transport is not entitled to forge the existing browser
   `server_html` provenance; it needs a separately reviewed source allowlist.
5. Rate/concurrency limits, daily quota, 429/Retry-After policy and availability.
   CF/AUTH means STOP; no challenge retry, hidden fallback or alternate identity.
6. Freshness semantics: the time the supplier price was verified, not simply
   when an old feed file was re-downloaded. Files older than 24h cannot provide
   fresh DP by refreshing only their ingestion timestamp.
7. Authorized hosting/egress and a supplier test/sandbox path for limited E2E.

No credentials or messages are requested/sent by this document.

### Authorized server-side browser — technically feasible, access conditional

Proposed single producer container on Railway owns a dedicated supplier-approved
browser profile and the accepted v2 worker. It connects locally to its own
browser and sends signed claims/results to the existing server. It never
connects to the owner's Windows CDP or imports Windows cookies/session storage.

Initial authentication and renewal must have an explicitly approved operator
procedure, or an unattended mechanism offered by the supplier. No such procedure
has been executed. If periodic human reauthentication is required, browser hosting
removes Windows dependence but **does not guarantee unattended price retrieval**.

Cloudflare explicitly says automated browsers and browser automation frameworks
are unsupported for solving production challenges:
https://developers.cloudflare.com/cloudflare-challenges/reference/supported-browsers/ .
This does not itself ban an owner-authorized integration, and it is not a license
to solve challenges automatically. On challenge or AUTH_REQUIRED: STOP, retain
uncertainty/fencing evidence, serve only still-fresh verified cache, return the
existing unavailable contract on miss. No stealth, fingerprint spoofing, proxy
rotation, CAPTCHA service, navigation fallback or Windows fallback.

The selected **conditional technical architecture** is this browser worker,
because it can reuse the accepted browser parser and v2 pipeline. Actual supplier
access remains an external gate. If a sanctioned API/feed is granted, select that
transport instead through a reviewed producer-output/source contract; do not run
both production producers or create a second broker.

## 2. Existing Railway browser service

Read-only `describe-service` on 2026-10-15:

| Field | Observed |
| --- | --- |
| Project | `a3132471-7898-4ab9-9a04-f83ef59fd56b` |
| Environment | `81afdae3-ef3d-4b29-b3ca-dd4a7a69d4a7` / production |
| Service | `0a32502d-dd8e-45be-88e2-228941919bd6` / dcp-cloud-browser-isolated |
| Latest deployment | `c13f0868-1fe3-4e30-aa43-7bfdd6a75974`, SUCCESS, 2026-09-29 |
| Source/build input | Not exposed/configured in returned source config |
| Explicit start command | Absent |
| Variable names | Empty |
| Volumes | Empty |
| Domains / TCP proxies | Empty |
| Configured replicas | One, sfo |
| Staged patch | None |

This is a reusable service identity in principle, not a validated worker. Latest
deployment SUCCESS does not establish a running price producer, authenticated
profile, pinned image or working network. No service logs with potential session
data, browser state or credentials were extracted. Nothing was created, deployed,
started, restarted, stopped, mounted or configured.

If later approved, it would need a reviewed immutable source/image, compatible
Linux browser dependencies, explicit start command, resource budget, dedicated
persistent state, approved auth/secret delivery, private communication to server,
singleton enforcement, restricted CDP, and a health policy that does not imply
successful DCP lookups. Its current production-environment placement must be
reviewed before using it as a sandbox; the name 'isolated' is not isolation proof.

Railway volumes provide persistent runtime directories, and the CLI can inspect
volume files: https://docs.railway.com/volumes . This is a design requirement only;
no volume operation occurred. Downloading an active raw SQLite file remains
insufficient for an authoritative current queue snapshot.

## 3. Windows-independent topology

| Role | Location / invariant |
| --- | --- |
| Probnik, Procenka, OEMixiBOT | Target: Railway 24/7; actual OEMixi rollout remains Theme 1 scope |
| Shared cache and broker | Existing server and its SQLite; one coordination transaction path |
| Consumers | Fresh DP cache first, explicit dealer TTL 24h; no browser fallback |
| Single producer | One Railway worker, signed v2 GET claim / POST result; no Telegram bot launch |
| Browser transport | Its own approved local profile; CDP loopback only, no public debugging endpoint |
| Atomic writes | Existing server result completion owns verified DP/identity/request COMMIT |
| MSRP | Independent verified source, 720h; not a DP replacement or this worker's output |
| Recovery | Operator reconciliation + persisted admission/claims/journal; no automatic second lookup |

Producer never mounts or copies the application's live DB; it communicates through
the existing signed protocol. No additional PriceEngine, queue, cache authority
or broker is proposed. One runtime replica alone is insufficient: prevent overlap
with deployment replacement and the legacy Windows/direct consumers as well.

## 4. Concrete readiness gaps found against PR #37

1. `dp_sync_agent._acquire_singleton()` returns true on non-Windows. Existing broker
   prevents a second active fenced claim, but this is not Linux process singleton
   enforcement. Future isolated runner needs an exclusive persistent host lock
   (and coordinated deployment ownership) before any worker loop. Cross-host
   legacy/direct paths remain first-switch blockers.
2. Agent server URL is hardcoded to production; journal/status default beside the
   source bundle. A future reviewed entrypoint must explicitly pin server target
   and persistent writable journal/status paths and fail closed on ambiguity.
   Mock overrides them only in disposable test memory/files. No cloud runner or
   deployment artifact is supplied by this validation patch.
3. Existing health source says `windows_dp_sync_agent`; a future cloud runtime
   needs accurate provenance and reviewed credentials/scopes. Existing v2 signing
   derives a key from OEMIXIBOT_TOKEN, and Stage 2 retains v1 health auth. Do not
   silently treat that as an approved autonomous least-privilege credential plan.
4. Dealer cache defaults in accepted consumers/gateway are 168h. The mock explicitly
   supplies 24h consistently; production/candidate defaults were not changed and
   must not be described as already enforcing the owner's new policy.
5. Actual `get_oem_catalog_cache_result()` from PR #37 returns a verified 721h-old
   MSRP row. `test_records_unmet_msrp_720h_requirement_in_actual_accepted_reader`
   reproduces this gap; it intentionally records observed behavior, **not a pass
   of the 720h requirement**. Consumer policy integration must reject stale/missing
   or invalid timestamps before public display; current autonomous plan does not
   repair Theme 1/other shared consumer files or hide the gap.
6. Queue counts, global writer cessation, independent no-inflight and current
   process inventory remain NOT PROVEN as accepted in Stage 7. Historical
   DAILY_20261015_010450 evidence (91 finished rows, last health CLOUDFLARE at
   01:00:48Z) supplied by Theme 4 is historical only, not re-audited or substituted
   for a current snapshot. SSH exit-255 attempts were not repeated.

These findings concern the new autonomy/TTL requirements. Accepted Stage 1–5
fixes are not rewritten. Existing consumer integration PRs remain untouched.

## 5. Focused Linux simulation evidence

Command: `python -B -m unittest -v test_dcp_autonomy_feasibility.py`.
Local result: **12/12**, no skips or expected failures. Linux Python 3.12,
disposable SQLite and fake DCP producer response. Underlying fixture denies
external socket connections and routes urllib requests only to in-process actual
HTTP handlers; simulated server URL uses `.invalid`. Windows was not powered off
or controlled: 'Windows-OFF' means no Windows dependency in the simulated path.

| Evidence group | Proven scope |
| --- | --- |
| Three concurrent consumers | Actual accepted adapter function bodies, one broker row, one fake dealer lookup, signed v2 result, atomic verified cache, client RUB rounding/dealer USD |
| Producer exclusivity | Second claim denied while active, including a different OEM; does not prove deployment/process singleton |
| Fresh DP / CLOSED | 23h DP served to all three without lookup even CLOSED |
| Stale DP / CLOSED | 25h DP rejected; fresh MSRP 999 is not a DP fallback |
| CF / AUTH | All consumers fail closed; producer latched STOP; passive READY does not clear it |
| Lost commit ACK | Cache committed once; journal blocks reboot/main and second lookup; consumers can use verified committed cache |
| Crash | Journal persists, lease expiration quarantines, replacement cannot claim, no auto-requeue |
| Duplicate completion | Replayed signed body gets 409 and does not change server snapshot |
| Invalid result | Retail/MSRP-only or RUB output cannot write verified dealer cache |
| Linux singleton diagnostic | Records that Windows mutex is not a Linux process lock |
| MSRP 720h diagnostic | Records unmet 720h policy in accepted reader, including 719h/721h fixtures |

Gateway opt-in injection uses accepted OEMixi catalog adapter, not evidence that
the installed OEMixiBOT app or newer Theme 1 runtime is configured. No hidden
production consumer switch is implied. Finance/order sentinel rows are unchanged;
no FinanceEngine, pricing, stock, order, checkout or WEB2 code changes exist.

The three-file delta is this dossier, the focused test, and a CI step running the
test. Existing CI checks stay enabled. Exact published HEAD/CI is recorded in the
final handoff, not fabricated inside this source commit.

## 6. Required decisions before any LIVE E2E / cutover

- Supplier's authorized unattended access/hosting/authentication agreement and
  usable transport; without it, real 24/7 new-price access is BLOCKED.
- Owner approval to implement the remaining isolated Linux runner, persistence,
  truthful health provenance and scoped credential contract; none is installed.
- Coordinated consumer policy work: DP 24h and MSRP 720h, without stale substitution;
  review against Theme 1's current shared gateway work before changing shared files.
- Stage 7 current authoritative queue/health/container snapshot plus all writer
  inventory; no-inflight evidence independent of SQLite, safe maintenance/barrier.
- Verified fresh checkpoint/restore and rollback that preserves subsequent business
  writes; old producer/direct paths cannot overlap the new worker.
- Separate owner GO for isolated Railway configuration/deploy/auth setup, future
  server deploy, legacy retirement and cloud producer start. No default activation.
- Admission remains CLOSED for first v2 bootstrap; empty queue signed smoke without
  real DCP first. ADMISSION OPEN and one bounded LIVE E2E require separate GO.
- CF/AUTH: STOP. Uncertainty: preserve journal, quarantine/reconcile. No v1 fallback,
  no deadline-based replay, no automatic OPEN or browser reauthentication.

**STOP / HANDOFF. No merge/deploy/production DB change/live DCP/browser control/
cart mutation/Windows agent stop or update has been performed.**
