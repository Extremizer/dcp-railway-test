# OEMixiBOT shared dealer-price runtime — isolated integration

Date: 2026-10-14. ISOLATED READY / NO PRODUCTION ACTIVATION.
Direct parent: accepted PR #37 `5a26ab78ebb8eb7862da7816580a577000d8579d`, tree `cd4d3bd2eb4a3f7052860bc8e77db444282e67ff`.
Final isolated HEAD/PR/exact CI are recorded in the handoff/PR. #31/#35/#36/#37 are preserved. The DCP first-switch HOLD remains.

## Runtime wiring

All sixteen price calls in `oemixibot_app.oem_message()` (first resolution and the existing manufacturer-identity retries) now use `_resolve_runtime_offer(oem, context)`.

`build_application(token=..., shared_price_gateway=<trusted callable>, dealer_max_age_hours=<approved policy>)` explicitly constructs the EXISTING `dcp_price_gateway.SharedDealerPriceAdapter` and stores it on that application's bot_data. There is no environment activation switch, new price engine, new broker, browser transport or price validation copy. An explicit None/non-callable gateway remains a bound failing adapter; it cannot select legacy fallback. Missing/non-finite/non-positive dealer policy fails before STORE initialization. There is no default production dealer TTL.

Alternatively, `bind_shared_price_gateway(application, callable, dealer_max_age_hours=...)` binds before application start. Binding an already running or previously bound application is denied. Per-application binding avoids a module-global resolver which could leak policy between two runtimes. No real second runtime is started in tests.

Bound mode path: actual OEM message handler -> runtime boundary -> existing resolve_offer(price_gateway=adapter) -> existing SharedDealerPriceAdapter -> supplied dcp_price_gateway.request_price transport callable -> verified fresh cache -> shared broker only when permitted -> atomic producer result/cache completion. Blocking broker waits run in asyncio.to_thread so they do not stall the Telegram event loop. Threading does not bypass the single SQLite coordination transaction/producer slot.

Cache miss, CLOSED, CF/AUTH, gateway errors and invalid price return existing failure statuses without direct browser fallback. Corrupt/null bound adapter fails closed as TECHNICAL_ERROR. Verified price remains USD; the existing dealer coefficient/display path is untouched. No MSRP/Retail/RRP substitution. Store/FinanceEngine/order handling and client RUB rules stay byte-identical.

## Explicit remaining direct paths

The application without a bound gateway, including unchanged `main()`/`build_application()` defaults, intentionally retains the legacy resolve_offer -> local DCP module path. This preserves the user's prohibition on automatically switching production. Standalone catalog callers that omit price_gateway and the standalone price module remain legacy/direct. The sixteen handler sites in a BOUND application have no such fallback.

The local module is still imported by the catalog for compatibility; import is not a browser lookup. Fail-closed tests install a direct-browser spy that raises on any call. The repeated identity-client blocks are preserved; they are identity queries to the existing backend, not a new DCP price transport. Their cleanup is outside this task.

## Shared transport / deployment topology boundary

The in-process gateway requires an operator-approved connection to the SAME server orders DB/coordination broker. A separate Windows runtime cannot share Railway's `/data` by pointing at a local DB of the same name. Creating another local queue is forbidden.

`DCP_OEMIXI_GATEWAY_CONTRACT.md` remains the existing external-transport proposal. Its authenticated/scoped HTTPS consumer endpoint is NOT implemented by this task. If Windows remains the selected host, that reviewed transport (cache read / optional live admission scope, bounded timeouts, separate consumer authentication, server-side policy) is a concrete activation blocker. The current injection boundary is testable without choosing/activating a production transport.

Alternatively, an owner could select a same-server runtime with shared DB access, but launcher/service wiring, singleton, credentials, supported dependencies, concurrency and shutdown must be reviewed and separately authorized first. Neither placement is selected by this patch. Existing `web1_runtime.py` intentionally does not launch OEMixiBOT.

## Mock integration evidence

`test_dcp_three_adapters.py` now exercises the actual OEMixi oem_message, runtime binding and catalog function rather than only passing an adapter to the catalog. Existing Probnik/Procenka adapter functions, shared gateway, SQLite broker and signed mock producer HTTP handlers remain real code. DOM/browser, Telegram output, local identity confirmation and external network are mocked; no polling, real order or production DB/cache is used.

- Three concurrent same-OEM consumers -> one queue row/claim; no second producer claim; consistent $31.25 raw DP, existing Probnik RUB rounding and OEMixi USD handler offer/display.
- Fresh verified cache -> no live enqueue/browser; cache remains available during CF and CLOSED in existing paired tests.
- CF/AUTH -> unavailable statuses; subsequent CF requests from all three add no queue records.
- CLOSED miss -> no price/queue mutation and no direct lookup.
- Gateway timeout -> TECHNICAL_ERROR, no browser fallback.
- Unverified source, wrong identity/currency, stale price and MSRP-only body -> NO_PRICE, no stored offer.
- Existing late-result, quarantine/recovery, single-writer and finance invariants retained.

The three-consumer fixture uses 168 hours as an existing TEST value only; runtime-binding fixtures use 1/2-hour values. None is a production dealer freshness decision.

`test_oemixi_shared_price_runtime.py` exercises actual build_application without polling, injected adapter identity, policy failures before STORE.init, independent applications, no hot rebinding, missing/corrupt adapter fail closed, manufacturer retry reuse and all sixteen call sites. A byte-scope guard reconstructs the exact parent app outside the bounded import/helper/build/call-site changes and requires parent Git blob `513b487c55dda09ca3fdeb371f5dd880473dc3f9`. No unrelated client texts/callback/cart/finance bytes may change.

Local results: three-consumer suite 13 PASS; runtime binding suite 10 PASS; existing dealer/runtime 23 PASS; other DCP/card/startup compatibility 146 PASS; five finance/checkout scripts PASS. New runtime tests are included in Dealer CI (its existing isolated dependencies), actual-handler integration in DCP CI. Exact GitHub results belong to the final HEAD, not the parent's previous 7/7.

## Read-only runtime provenance

Windows device EXTREMIZER `968012c5-5a8c-4ccd-bbef-b9b681f3ef0f` was online. Bounded known-directory audit at `2026-10-14T11:04:51.2012546Z`, corrected Python-vs-wrapper observation at `2026-10-14T11:06:29.4563735Z`:

| Evidence | Result / limit |
| --- | --- |
| Local directory | `C:\Users\ASUS\Desktop\Работа\DCP Railway Test\vk_prod_redeploy_20261002` |
| Installed on-disk oemixibot_app.py | 90,907 bytes; SHA256 `65bfd9ffc8ca43249d8b5d9ab6e9eea5623e0bef03e8e15c97093ce053e01f6f`; matches historical uploaded source, not accepted PR #37 app |
| Installed on-disk catalog | SHA256 `65f104a0dda39aef044a5f67182f59c3d543637ded6376f7f2397f21a687420c` |
| Installed on-disk store | SHA256 `aa08db573e1202d33dfed31fd101b03432a659245e73cd5632c194f5242e2f6b` |
| dcp_price_gateway.py | Not found in the two inspected known directories; not a whole-disk absence claim |
| Startup wrapper | `start_oemixibot_private.ps1`; SHA256 `1286e69b8bab7372d67b030071bd04911b7f7656dabed3d70440d44116941b6b`; references oemixibot_app.py and DB config name, no shared binding/freshness names |
| Python OEMixi process | None matching in the bounded observation; active runtime/revision NOT PROVEN |
| Matching wrapper PIDs | 7068 powershell / 21672 cmd; each had only conhost child, no OEMixi Python child observed; wrappers do NOT prove a running bot |
| Scheduled tasks | No OEMixi matches in inspected task inventory |
| Environment/credentials/browser | Values not read or emitted; browser untouched |
| Other hosts | NOT PROVEN |

Fresh Railway OAuth metadata: known dcp-railway-test source pin `74ce11eb864ee4d852c60e9ffdb140ef73a46517`, deployment `16eeacbe-d2a4-425a-8f45-6d87eb23d159` SUCCESS, startCommand `python -u web1_runtime.py`, no staged changes. Exact deployed source's web1_runtime does not spawn OEMixiBOT even if its token variable exists. This is source/config provenance, not a container process trace or evidence no OEMixi runtime exists elsewhere. No container SSH/browser/DB interrogation was needed.

Overall installed active OEMixi runtime/protocol/shared gateway: NOT PROVEN. On-disk legacy files/wrapper: PROVEN within the stated scope. No application, agent, worker or browser was started/stopped/reconfigured by this audit.

## Future activation gates — not execution permission

1. Owner selects actual host/runtime and identifies exact source/bundle/dependencies plus singleton; old on-disk upload is not interchangeable with the accepted repo tree and may differ in finance/store behavior.
2. Approve explicit dealer freshness and legitimate shared transport/scopes. Reuse the single server DB/broker; no new local queue; remote HTTPS implementation remains required if the chosen bot is separate.
3. Review composed release against current base and other themes. WOPS #31 excluded; #36 remains verification dossier; #35/#37 preserved, not automatically replaced.
4. Resolve existing DCP first-switch gates: maintenance/all legacy writers ceased, no-inflight/queue proof, verified fresh checkpoint, RC authority and approved recovery plan. No cache_only/agent-stop shortcut.
5. Separate GO for server, producer/agent, selected OEMixi configuration/start, admission OPEN and LIVE E2E. New injected binding is set BEFORE polling, not an online hot switch. Confirm no direct price callers remain in that selected configuration and deny fallback on transport error.
6. Future smoke/live tests require their own exact OEM/request budget and write permissions. Uncertain delivery/CF/AUTH -> STOP; no forced lookup, CAPTCHA bypass or browser control. Never rollback blindly to direct/v1 with unreconciled work.

No FinanceEngine changes, merge, deploy, installed-agent update, real DCP, real Telegram order, browser control or production cache/config writes. STOP / HANDOFF Theme 4.
