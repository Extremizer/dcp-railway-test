# WEB2 POWERSPORTS Staging Modes

**Status:** plan only. No deployment authorized.

## Mode A — Visual staging

Purpose:
- owner/Theme 4 visual review of POWERSPORTS composition;
- exact Theme 7 horizontal logo;
- original garage photo;
- Desktop 1440 / Mobile 390;
- transparent OEM search;
- product/cart appearance using deterministic synthetic data.

App:

```bash
python -m uvicorn web2_staging_app:app --host 0.0.0.0 --port $PORT
```

Data:
- synthetic OEM/stock fixtures only;
- mock handoff;
- no production DB;
- no production Telegram;
- no live DCP.

Suggested environment:

```text
EXTREMIZER_ENV=visual-staging
EXTREMIZER_DCP_MODE=cache_only
EXTREMIZER_DISABLE_TELEGRAM_BOT=1
EXTREMIZER_DISABLE_LIVE_DCP=1
EXTREMIZER_ORDERS_DB_FILE=/tmp/web2-visual/orders.db
EXTREMIZER_POWERSPORTS_LOGO_PATH=/staging-assets/<theme7-approved>.png
EXTREMIZER_POWERSPORTS_LOGO_SHA256=<theme7-approved-sha256>
```

Garage photo:
- mount/provision exact approved original in staging-only assets;
- no production media dependency.

This mode is safe for owner HTTPS review because it cannot create a real Telegram order.

## Mode B — Functional staging

Purpose:
- prove the real WEB1 API handlers + WEB2 frontend + disposable DB;
- optionally prove Procenka checkout in isolated test execution;
- no production effects.

Host:

```bash
python -m uvicorn web2_runtime_app:app --host 0.0.0.0 --port $PORT
```

Required isolation:

- dedicated disposable/staging SQLite path;
- no production volume;
- no production DATABASE_URL;
- external network denied or explicitly stubbed;
- no live DCP credentials;
- no Telegram bot polling;
- no production Telegram token;
- no production supplier credentials.

Recommended environment baseline:

```text
EXTREMIZER_ENV=functional-staging
EXTREMIZER_DISABLE_TELEGRAM_BOT=1
EXTREMIZER_DISABLE_LIVE_DCP=1
EXTREMIZER_DCP_MODE=cache_only
EXTREMIZER_ORDERS_DB_FILE=/tmp/web2-functional/orders.db
EXTREMIZER_DATA_DIR=/tmp/web2-functional
EXTREMIZER_OEM_REFERENCE_DB_FILE=/tmp/web2-functional/reference.db
EXTREMIZER_POWERSPORTS_LOGO_PATH=/staging-assets/<theme7-approved>.png
EXTREMIZER_POWERSPORTS_LOGO_SHA256=<theme7-approved-sha256>
```

Important:
`web2_runtime_app.py` reuses the real WEB1 application and handlers, so Functional staging must be network/credential isolated before public HTTPS exposure.

## Functional verification

Use synthetic seeded catalog/DP and disposable DB, matching `test_web2_host_integration.py`:

- `/web2` loads;
- real `/api/v1/oem/{oem}` returns server price;
- real `/stock` works;
- real `/api/v1/handoff` stores revalidated handoff in disposable DB;
- no order is created at handoff stage;
- no external network;
- no real Telegram message;
- no live DCP.

The Procenka confirmation/atomic checkout remains proven through the isolated E2E suite; do not expose a real Telegram bot merely to demonstrate it.

## HTTPS

For both modes:
- separate Railway service;
- Railway-generated HTTPS domain;
- no production domain;
- no production Cloudflare routing;
- autodeploy decision belongs to Theme 4/Release Control;
- deploy exact approved HEAD only.

## Recommendation

First deploy Mode A after Theme 7 visual assets are accepted.

Mode B should be a separate service or separate deployment configuration and should not share databases/credentials/volumes with Mode A or production.
