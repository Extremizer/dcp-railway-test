# DCP reliability — isolated candidate, 2026-10-13

Base: `74ce11eb864ee4d852c60e9ffdb140ef73a46517`.
No production deployment, installed-agent update, browser navigation, or live lookup is authorized by this candidate. PR #31 remains unchanged and on HOLD.

## Confirmed current architecture

| Consumer | Current production path | Shared broker? |
| --- | --- | --- |
| Probnik | Verified identity/cache -> SQLite live queue -> Windows agent -> authorized DCP Chrome | Yes, on a cache miss |
| Procenka | `EXTREMIZER_DCP_MODE=cache_only`; fresh DP cache -> same queue -> same agent | Yes for Parts cache misses; non-Parts uses cache only |
| OEMixiBOT | `oemixibot_catalog.resolve_offer` -> direct local `oemixibot_dcp_price` | No; active runtime was not established by this audit |

Railway runtime was verified read-only: `web1_runtime.py`, `extremizer_bot.py`, `probnik_app.py`. Windows installed agent code matches the accepted release for the agent and browser producer. The local OEMixi application files differ from the release; no running OEMixi process was established. Do not call the three deployed bots unified.

Known Parts lookup: zero DCP requests if the exact authorized DOM supplies DP; otherwise one background GET. Ski-Doo Accessories fallback can add a search POST and detail GET. AUTO discovery can traverse all 12 Parts manufacturers (up to 14 application requests including the Ski-Doo fallback), until FOUND or a blocking status. The old challenged background fetch also navigates the owner's tab; Cloudflare's own verification traffic is not counted here.

## Confirmed defects and isolated changes

* `oemixibot_dcp_price.py`: remove challenged-fetch top-level navigation and its 15-second retry loop; return CLOUDFLARE immediately. Require a local health file; match the exact DCP origin/path/query; reject nonpositive/nonfinite prices. Existing authorized exact DOM remains the first lookup path.
* `dp_sync_agent.py`: evaluate challenge markers only on DCP-origin pages; latch blocking health before result delivery, so delivery failure cannot leave READY; pace cross-manufacturer AUTO lookups (default five seconds).
* `dp_live_bridge.py`: canonical manufacturer/OEM key; transactional SELECT+INSERT; one global claimed request; minimum lookup-start gap (default five seconds); expire pending requests older than 30 seconds at admission/claim; reuse a verified result for five seconds; persist CF/AUTH circuit in the same transaction as its result. A circuit clears only on a strictly newer READY observation. Invalid DP or MSRP/Retail/RRP provenance cannot become FOUND.
* `test_dcp_reliability.py`: mock producer, real disposable SQLite, concurrent broker consumers, cache-hit execution of actual client functions, blocked/READY recovery, long blockage, no navigation, failure-before-result delivery.
* `.github/workflows/dcp-health-core-ci.yml`: add the new mock regression suite to the existing DCP check.

The five-second settings are provisional candidate defaults, not a vendor-approved HTTP request quota. One admitted lookup can include several HTTP requests. The shared limit does not govern the current direct OEMixi path until its migration. No claim that total production traffic has already fallen is made.

## Recovery and remaining integration gates

1. On CF/AUTH: stop live work. Continue serving fresh verified cache through existing consumer contracts. No timed auto-search retries, tab reload, CAPTCHA solving, or secret export.
2. The owner manually recovers an existing DCP session. The agent performs passive DOM health checks; only authorized, unchallenged DCP yields READY. Local blocked polling is already 15 seconds; READY checks are 30 minutes. No fresh search is needed to establish READY.
3. Before installation, specify recovery for an orphaned claimed request. This candidate deliberately does NOT expire an in-flight claim and start another lookup while the old browser operation may remain active. A dead agent can therefore hold the slot; explicit reconciliation is required.
4. Migrate OEMixiBOT to a signed cache-first price gateway backed by this same broker. Do not reuse its identity-only endpoint as a price endpoint. Confirm its actual process, working directory, deployed revision and freshness policy first. Then run end-to-end mock tests through all three real adapters.
5. Make the existing signed server result endpoint atomic with request admission, verified cache/identity writes and completion. It currently writes cache before `finish`; a late/duplicate result can write cache before a 409 response. This candidate leaves `web_app.py` unchanged and does not close that pre-existing consistency risk.
6. Review the alternate `dcp_private_price`/Playwright paths before any switch out of production cache-only mode. They are not active in the observed Railway configuration.
7. Review overlaps with held PR #31 (`dp_sync_agent.py`, `oemixibot_dcp_price.py`, DCP workflow). Do not merge branches by whole-file replacement or silently resume WOPS.
8. Separate permissions are required for server integration, installed local agent update, runtime switch and controlled live validation. This candidate is NOT merge/deploy ready.

## Observed evidence, not inferred success

Read-only production measurement at `2026-10-13T19:35:35Z`: 14 queue records since UTC midnight — FOUND 10, CLOUDFLARE 1, TIMEOUT 3. Two AUTO keys each had two requests; observed same-key overlap was zero. This does not disprove the SELECT/INSERT race. READY -> TECHNICAL_ERROR transitions: 10, including CDP timeouts and URLError. Queue records are not HTTP request counts and do not identify all caller bots.

At 17:01:17Z the CF event contains `DCP top-level navigation is still waiting for human verification.` The subsequent sequence CF -> AUTH_REQUIRED -> CF -> READY confirms instability, not the number or root cause of human CAPTCHA prompts.

At 19:45:15Z one DCP tab showed authorized=true, challenged=false, cookies enabled and successful JavaScript execution; another tab timed out on Runtime.evaluate. No clearance cookies, tokens, passwords or private headers were read. Current consent does not authorize reproducing a challenge by navigating the owner's browser.

## Local validation

58/58 DCP/card/startup tests; 23/23 dealer/runtime tests; five atomicity/checkout/finance script checks PASS. New reliability suite: 24 tests. All use mocks/disposable SQLite. FinanceEngine fingerprint remains `17546efcea563f0f3e1942947f085e31ed6d88a9ee6401e2c18147171f57d863`; approved non-UI client, checkout and WEB ADMIN guards pass unchanged.

GitHub CI must be reported for the exact candidate HEAD separately. Green CI does not authorize production.

## Vendor channel

Public official DCP API documentation or partner price feed was not established. A public homepage retrieval returned 403; indexed product references to API are oil classifications, not an integration API. Ask the supplier about an authenticated dealer-price endpoint/feed, permitted caching and rate limits, service credentials, authorized browser automation, and any vendor-managed challenge exception. Do not assume these facilities exist; do not send a request without owner instruction.

Primary Cloudflare documentation explains that challenge clearance can differ in level, expire or be re-evaluated; passage does not exempt rate limits. Those are possible explanations, not proof of DCP's configuration:

* https://developers.cloudflare.com/cloudflare-challenges/concepts/clearance/
* https://developers.cloudflare.com/cloudflare-challenges/challenge-types/challenge-pages/challenge-passage/
* https://developers.cloudflare.com/cloudflare-challenges/troubleshooting/challenge-solve-issues/

Status: ROOT CAUSE CONTRIBUTORS ANALYZED / SAFE HARDENING MOCK-TESTED / PRODUCTION UNCHANGED / INTEGRATION HOLD.
