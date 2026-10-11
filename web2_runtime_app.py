"""Explicit WEB2 host: reuse WEB1 APIs and startup without copying handlers."""
from web_app import app
from web2_ui_routes import attach_web2_ui

attach_web2_ui(app)
