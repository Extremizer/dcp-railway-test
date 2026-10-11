# WEB2 opt-in release boundary

WEB2 uses the real WEB1 FastAPI app through `web2_runtime_app:app`. The wrapper
only attaches `/web2` and `/web2-static`; existing API/admin/health handlers,
startup and `web_app.py` remain unchanged.

`web1_runtime.py` selects the wrapper only with `EXTREMIZER_WEB2_ENABLED=1`.
Without that flag the existing `web_app:app` target is used. Adding the code to a
release does not itself enable WEB2. Release Control chooses whether to enable
the flag in the intended environment. No Railway variable/configuration is
changed by this PR.

Validation boundaries:
- UI browser smoke uses fixture APIs and a simulated Telegram destination.
- Host integration imports the real WEB1 app and executes its startup, cached
  OEM pricing, stock API and handoff handlers on temporary SQLite. No API or
  pricing handler is mocked; external socket connections are forbidden.
- Launcher tests intercept child processes and verify both default and opt-in
  targets without starting Telegram or touching a live database.
- Live DCP, Telegram checkout and production post-deploy checks are separate.

Mass-list ingestion and the ten-field customs invoice are outside WEB2 v1.
This PR stays Draft; merge, flag activation and deployment belong to Release
Control after selection and validation of the exact release candidate.
