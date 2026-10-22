# EXTREMIZER — isolated Linux Chromium LIVE-access candidate

**Scope**: code and Docker build only. No deploy, DCP requests, login, cart operations or production change.

Base reused without modification: existing Chromium launcher `dcp_cloud_browser_host.py`, owner-operated Telegram-signed recovery `dcp_phone_recovery_app.py`, persistent profile + singleton lock. PHONE-FIRST remains unchanged. No Windows cookies.

**Build command**
```sh
docker build -f Dockerfile.dcp-live-isolated -t dcp-live-isolated:candidate .
```
**Start-command proposal (not executed)**
```sh
xvfb-run -a python -u /app/dcp_linux_live_isolated.py
```
An entirely **new** Railway service in a separately authorized isolated staging environment is the only proposed destination. Mount a new, non-shared volume at `/data`. Never attach to the existing PHONE-FIRST service or volume. Set exact immutable source commit. No staging deployment authorized.

**Configuration names (values never shown)**: `DCP_CLOUD_BROWSER_HOST_ENABLED=1`, `DCP_CLOUD_PRODUCER_ENABLED=0`, `DCP_PHONE_STAGING_MOCK_ONLY=0`, `DCP_PHONE_RECOVERY_ENABLED=1`, `DCP_CLOUD_STATE_DIR=/data`, `DCP_CHROMIUM_EXECUTABLE=/usr/bin/chromium`, `DISPLAY` (set by xvfb), `PORT`; confidential `DCP_RECOVERY_TELEGRAM_BOT_TOKEN`, `DCP_RECOVERY_OWNER_ID`, `EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT`; `EXTREMIZER_DP_OWNERSHIP_ENFORCE=1`. **No secrets committed.**

**Health versus dealer readiness**: existing `/health` reports recovery availability and explicitly `producer_running:false, admission_opened:false`. Successful health/CDP does NOT prove dealer authentication, identity, server ownership admission, or a valid price source.

**P0 integration now included as source candidate (not deployed):** `dcp_linux_cdp_identity.py` performs actual loopback CDP read-only preflight, verifies the exact HTTPS `/cart` context and rejects challenge markers, then injects Linux callbacks into the existing `polaris_dealer_session_identity_guard.verify_dealer_session` (unique `.displayname`, same-origin `/account/logoff`, constant-time confidential account comparison). `dcp_linux_live_app.py` adds protected, owner-triggered `/recovery/open-dealer` only when `DCP_LIVE_NAVIGATION_GO=1`; Chromium starts on `about:blank` and makes no DCP request on startup. `/admission/status` distinguishes browser/dealer/journal/broker status. Signed-v2 `CloudV2Producer.acquire` is allowed only after actual dealer proof and durable `ADMISSION_PENDING` journal write; broker claims and price completion remain entirely disabled. `dcp_linux_admission.py` maintains atomic fsync journal/lock and restart quarantine, including inability to resume if an owner acquisition remains uncertain. No actual LIVE dealer session or broker receipt is proven by CI.

**Runtime source integrity:** the Docker build injects exact `DCP_SOURCE_HEAD` into `/app/dcp_build_source_head`; process startup requires `DCP_EXPECTED_SOURCE_HEAD` to match it. This does not prove independent OCI registry digest provenance. A build from a mutable base tag is not an immutable reviewed image until separately pinned/attested.

**New safe config names:** `DCP_EXPECTED_SOURCE_HEAD`, `DCP_LIVE_NAVIGATION_GO` (must remain OFF before separate LIVE GO), `DCP_STAGING_BROKER_ORIGIN`, `DCP_STAGING_BROKER_EXPECTED_HOST`, `DCP_STAGING_PRODUCER_ID`, `DCP_STAGING_SIGNING_KEY_B64`. No values committed. Expected dealer ID remains secret runtime configuration. The staging broker endpoint must be independently approved; never point it at production.

**Owner login step after distinct staging GO**: after the separate staging and LIVE navigation approvals, use the NEW isolated service owner-protected recovery UI and its authorized `/recovery/open-dealer` action to navigate to DCP; manually sign in on the Linux Chromium profile. Do not export cookies from Windows. Then run a strictly read-only actual identity audit; AUTH_REQUIRED, Cloudflare or mismatch means STOP.

**No execution of LIVE DCP allowed until verified dealer admission and separate go.** Existing PHONE-FIRST remains untouched.
