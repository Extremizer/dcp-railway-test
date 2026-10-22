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

**Outstanding admission (P0):** this existing cloud browser host intentionally contains no producer broker claim/completion path; the new entrypoint keeps that safety gate. Before a genuine LIVE-capable broker producer is accepted, implement and independently verify secured signed-v2 producer integration, journal and ownership proof against the live broker, plus read-only `polaris_dealer_session_identity_guard.verify_dealer_session` (same-origin /account/logoff, exact displayname and trusted preflight). An environment assertion is NOT identity evidence. No positive Dealer FOUND, price source, card/cart mutation, or production update.

**Owner login step after distinct staging GO**: open the independently protected recovery UI of the NEW isolated service and authenticate DCP manually on its Linux Chromium profile. Do not export cookies from Windows. Then run a strictly read-only actual identity audit; AUTH_REQUIRED, Cloudflare or mismatch means STOP.

**No execution of LIVE DCP allowed until verified dealer admission and separate go.** Existing PHONE-FIRST remains untouched.
