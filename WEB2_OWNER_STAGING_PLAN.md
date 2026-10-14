# WEB2 Owner HTTPS Staging Plan

**Status:** plan only — deployment is NOT authorized.

## Goal

Expose the unified WEB2 client MVP through a normal HTTPS Railway URL while keeping the environment isolated from production.

## Railway service

Create a NEW Railway service in the existing project or a dedicated staging project.

Recommended service name:

`web2-owner-staging`

Source:

- repository: `Extremizer/dcp-railway-test`
- branch: `web2-unified-procenka-owner-staging-20261014`
- automatic production deploy: OFF
- no production volume attached
- no production database URL copied
- no production Telegram bot token
- no live DCP credentials

## Start command

```bash
python -m uvicorn web2_staging_app:app --host 0.0.0.0 --port $PORT
```

This is the first owner-visible HTTPS staging mode. It serves WEB2 plus synthetic isolated OEM/stock/handoff fixtures and does not start the Telegram bot.

## Environment

Required safe variables:

```text
PYTHONUNBUFFERED=1
EXTREMIZER_ENV=staging
EXTREMIZER_DCP_MODE=cache_only
EXTREMIZER_DISABLE_TELEGRAM_BOT=1
EXTREMIZER_DISABLE_LIVE_DCP=1
EXTREMIZER_ORDERS_DB_FILE=/tmp/web2-owner-staging/orders.db
```

Do NOT set:

- production `DATABASE_URL`;
- production SQLite volume path;
- production Telegram token;
- supplier credentials;
- Cloudflare/DCP live cookies or credentials;
- production WebDAV/backup credentials.

## Storage

Initial owner preview requires no persistent production-compatible storage.

Use disposable service-local `/tmp` SQLite only. Restart may reset staging data by design.

If persistence is later required, create a dedicated staging volume and a separate staging DB path; never mount the production volume.

## HTTPS

Use Railway's generated public domain for this isolated service after Theme 4 authorizes deployment.

No custom production domain and no Cloudflare production routing in the first owner preview.

## Safe verification sequence

1. Deploy the exact approved unified HEAD only after Theme 4 authorization.
2. Open `/staging-health`.
3. Open `/web2`.
4. Search synthetic `417300574`.
5. Verify USA/RF offers, cart and `29 200* ₽`.
6. Exercise staging handoff; it must remain mocked/non-Telegram in `web2_staging_app.py`.
7. Restart service once and confirm no dependency on production data.
8. Confirm environment contains no production credentials.

## Later integration preview

A second, separately authorized staging mode may use `web2_runtime_app:app` with a dedicated disposable/staging SQLite DB and network-denied/mocked external services to prove the real WEB1 handlers + Procenka path through HTTPS.

That mode is NOT part of the first public owner preview and must not use live Telegram or live DCP.

## Canonical logo

The owner-approved source is:

- file: `лого окончательный!(6).png`
- SHA-256: `4448f9d137dff46fa8f9874871e7f9b4cbe68ee9813e927cff9130d2b6418b30`
- dimensions: 1254×1254 PNG

Only the original may be used. No crop, reconstruction, generated replacement or horizontal redraw.
