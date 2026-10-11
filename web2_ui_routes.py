"""Opt-in WEB2 UI routes for isolated staging.

This module does not import the production WEB1 app, mutate finance state,
or register routes until attach_web2_ui() is explicitly called.
The host application remains responsible for the existing /api/v1 endpoints.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEB2_DIR = Path(__file__).resolve().parent / "web2"


def attach_web2_ui(app: FastAPI, *, prefix: str = "/web2") -> None:
    """Mount the EXTREMIZER frontend on an explicitly selected ASGI app.

    Only static UI resources and the index are exposed. No database writes,
    checkout handlers, pricing rules, or production routing are modified.
    """
    if prefix != "/web2":
        raise ValueError("WEB2 prefix must be /web2")
    if not (WEB2_DIR / "index.html").is_file():
        raise FileNotFoundError(WEB2_DIR / "index.html")
    for filename in ("styles.css", "app.js"):
        if not (WEB2_DIR / filename).is_file():
            raise FileNotFoundError(WEB2_DIR / filename)

    existing = {getattr(route, "path", None) for route in app.routes}
    if "/web2" in existing or "/web2-static" in existing:
        raise RuntimeError("WEB2 routes already registered")

    app.mount("/web2-static", StaticFiles(directory=WEB2_DIR), name="web2-static")

    @app.get("/web2", include_in_schema=False)
    def web2_index() -> FileResponse:
        return FileResponse(WEB2_DIR / "index.html", media_type="text/html")
