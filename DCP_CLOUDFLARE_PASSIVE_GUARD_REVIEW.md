# DCP Cloudflare — passive document guard / resumed investigation

Date: 2026-10-14. ISOLATED ONLY / RELEASE HOLD PRESERVED.

Parent: exact accepted PR #35 HEAD `16e3fa34f1e2659aa6c1e6d85b914371253bfe6d`, tree `7e9a043c14c5b4f40f0eb126ac846b53417d0eae`.
PR #36 is validation evidence, not this patch's parent. No Stage 6 or first-switch execution.
Final branch/HEAD/PR/CI are recorded separately in the handoff; inherited Stage 3/4 manifests remain historical identities for their exact candidates.

## Confirmed code defects, not a proven live CAPTCHA root cause

1. DCP tab-list metadata can become stale before Runtime.evaluate. Parts/Accessories JS previously started fetch without checking the CURRENT document origin, challenge or authorization. Inert Node execution of the actual expression demonstrates a request even when the current page is a foreign origin, an active challenge or a sign-in page.
2. Passive health preflight returned href but did not validate it. A foreign page with Sign Out could publish READY. A mock reproduces this; this is a health classification error, not evidence that such a navigation occurred on the owner's computer.
3. Non-200/non-403 transport responses were parsed as search data. An authorized 429/5xx response could become NOT_FOUND, NO_PRICE or FOUND. Ski-Doo Parts 503 could start the Accessories fallback. A mock demonstrates the extra call. Actual challenge frequency is not measured by these tests.

Six tests failed against the accepted parent before the patch; one success-path control passed. The completed suite has 12 cases. No live browser or DCP traffic was used.

## Bounded fix

`oemixibot_dcp_price.py`: inside each actual DOM/Parts/Accessories expression, inspect current origin and document BEFORE search. A foreign origin fails closed; CF/auth blocks return their statuses with zero fetch. Recheck before Accessories detail fetch after its awaited search. Non-200/non-403 responses become TECHNICAL_ERROR and do not trigger fallback or provide a price. 403 retains existing conservative CLOUDFLARE handling. Normal authorized 200 parsing/source/price contract is unchanged.

`dp_sync_agent.py`: validate the CURRENT probe href with the existing exact-host predicate before publishing READY. Missing/foreign href is TECHNICAL_ERROR. This does not clear producer STOP, reconcile its journal, reopen admission or restart the installed agent.

`test_dcp_reliability.py`: the unrelated-WOPS fixture now includes the real href returned by the production JS. No expected status or guard weakened.

`test_dcp_passive_guard.py`: actual Python functions plus real JS evaluated in Node with inert fetch/DOM/location. Tests cover 429/5xx including forged usable-price bodies, Parts-to-Accessories stop, auth/challenge/current-origin changes, healthy single fetch, challenge between Accessories search/detail, challenge classification and passive READY detection. No dependency on a running browser.

`.github/workflows/dcp-health-core-ci.yml`: Node 22 setup and the new mock-only suite.

## Already accepted mechanisms preserved

Fresh verified cache-first; normalized request deduplication; one broker claim; pacing; CF/AUTH circuit; no navigation fallback; producer uncertainty journal/STOP; fenced late-result rejection; atomic single cache writer; persisted admission CLOSED/OPEN and explicit signed approval remain unchanged. Fifteen protected server/consumer/broker/finance/scope files are byte-identical to PR #35. FinanceEngine fingerprint stays `17546efcea563f0f3e1942947f085e31ed6d88a9ee6401e2c18147171f57d863`.

Local evidence: 12 new cases PASS; combined DCP/HTTP/adapter/card/startup 168 PASS; dealer/runtime 23 PASS; five client finance/checkout scripts PASS; syntax/whitespace PASS. Initial local runs lacked dependency path, then the dealer test encountered a sandbox SOCKS-proxy dependency; using existing isolated ci-deps and removing proxy variables only for that test process resolved these environment errors. GitHub CI is reported only after the exact remote HEAD completes.

## Request bounds and remaining architecture gaps

| Code path | Application HTTP bound, not Cloudflare internal traffic | Limitation |
| --- | --- | --- |
| Fresh verified cache | 0 DCP searches | Actual deployed TTL/config not freshly re-audited here |
| Already open exact authorized OEM DOM | 0 DCP searches | Current DOM identity remains validated by producer/server contracts |
| Known manufacturer Parts miss | 1 background GET | Response may block; no navigation/retry |
| Ski-Doo Parts miss + unique Accessories match | Up to 3 requests: Parts GET, search POST, detail GET | One broker lookup is not one HTTP request |
| AUTO manufacturer discovery | Up to 12 Parts catalog attempts + 2 Ski-Doo requests | Stops on CF/AUTH/technical error; known identity avoids the fan-out |
| OEMixi default resolve_offer | Direct local price module | Shared broker single-flight/rate/freeze do not apply to this default path |

OEMixi default handlers still call resolve_offer without an injected SharedDealerPriceAdapter. Shared gateway is opt-in; actual installed runtime/freshness remains NOT PROVEN. This patch does not switch it. A direct caller observing unchanged local READY can call again; the shared broker's circuit is not a cross-process local gate. The CURRENT-document guard prevents requests during a visible challenge/auth loss, but it is not a global direct-path rate limiter or a latch after a challenge confined to a background response. This is an already documented integration limitation, not a claim that all three installed bots are coordinated.

## Recovery meaning

Passive preflight reads existing DOM only; it may observe genuine authorized DCP READY after human recovery. An already loaded authorized DOM alone does not prove a formerly blocked background endpoint now accepts requests. No automatic test search is added. READY is observation only: producer STOP/journal and CLOSED admission retain their independent authorization/reconciliation gates. A stopped v2 agent exits instead of polling indefinitely; passive recovery demonstrated by the callable preflight is not a proof of a currently running post-STOP recovery service.

## Live evidence boundary

The earlier 2026-10-13 audit recorded health at 17:01:17 UTC with a top-level-navigation challenge detail. That establishes historical fallback use, not the cause/count of two human checks. Stage 5 later recorded an installed v1 agent and missing v2 modules; neither observation is a fresh runtime claim for this run. The new passive guards and accepted PR #32–35 changes are not installed by this task.

No fresh browser DOM/network trace, Ray ID correlation or production queue inventory was obtained here. Double verification may involve fallback, separate endpoint checks, independent direct consumers, AUTO fan-out, stale/changed tabs or uncertain producer state; these remain hypotheses until an approved bounded observation correlates events. Do not claim Cloudflare solved or a deployed improvement.

Cloudflare's official [challenge troubleshooting](https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/) also lists network, browser/script configuration and detection errors as possible loop causes. These are general possibilities, not findings about DCP or this user's browser. No browser settings, extensions, network or session were changed. Avoid collecting raw HAR: it can contain credentials; only separately permitted redacted diagnostic fields belong in a future evidence report.

## Future owner-assisted observation — PLAN, not execution

First request separate owner permission for one observation, one OEM (Arctic Cat 2670-265), a five-minute window and a maximum application-search budget. Owner operates their own DCP page and CAPTCHA; agent does not navigate, reload, focus or solve it. First phase is passive: correlate timestamp, redacted tab identity, manual verification count, visible Ray ID/status and existing authorized session with permitted sanitized logs. Read no cookies/tokens/credentials/headers. No automatic lookup or production cache write in this phase.

If one later controlled search is required, request a distinct LIVE E2E GO that names the exact deployed server/agent revisions, allowed cache write, consumer, OEM and one attempt. This task does not authorize that GO. Do not force a cache miss or use AUTO/Accessories/multiple bots. Stop at the first challenge/auth loss, context change, timeout or uncertain ACK. No second search, refresh or retry. If no new application request occurs between the owner's two checks, do not attribute them to a duplicated bot lookup. Existing first-switch blockers and RC authority/checkpoint gates must be resolved before testing the uninstalled v2 candidate.

## Compatibility / boundaries

The delta is directly based on PR #35, not composed with #36 or other themes. Server, Procenka, Probnik, WEB2, orders, stock and finance bytes stay unchanged. WOPS #31 remains HOLD/excluded; overlaps with inherited stack and this price/agent/workflow delta require separate future composition. No whole-file resolution, gateway activation, TTL assignment or WOPS activation.

Any future agent package must use the revised complete bundle; installing one file or reusing the old bundle digest is forbidden. Runtime candidate PR #35 is preserved as accepted until Theme 4 explicitly accepts a replacement composed candidate. New Draft PR/CI alone grants no rollout authority.

STOP / HANDOFF. No merge/deploy/install/update/restart/browser control/live DCP/production writes/new release stage.
