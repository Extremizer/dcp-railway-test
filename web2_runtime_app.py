"""Explicit WEB2 host: reuse WEB1 APIs and startup without copying handlers."""
from web_app import app
from web2_ui_routes import attach_web2_ui

attach_web2_ui(app)

# T1 / owner-only RAM shared carts. Mount ONLY with explicit env gate.
from t1_pilot_shared_bridge import attach as _attach_t1_pilot_shared
_attach_t1_pilot_shared(app)

