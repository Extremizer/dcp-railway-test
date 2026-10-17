"""Opt-in WEB2 UI routes for isolated staging.

This module does not import the production WEB1 app, mutate finance state,
or register routes until attach_web2_ui() is explicitly called.
The host application remains responsible for the existing /api/v1 endpoints.
"""
from __future__ import annotations

from pathlib import Path
import hashlib
import os
import re

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

WEB2_DIR = Path(__file__).resolve().parent / "web2"

# Owner-approved square source/reference asset. This remains the immutable
# original reference; it is not the final POWERSPORTS header treatment.
CANONICAL_LOGO_SHA256 = "4448f9d137dff46fa8f9874871e7f9b4cbe68ee9813e927cff9130d2b6418b30"

# Theme 7 will provide the exact approved horizontal PNG + SHA-256 later.
# Until both variables are present and match, /web2-powersports-logo is 404.
POWERSPORTS_LOGO_PATH_ENV = "EXTREMIZER_POWERSPORTS_LOGO_PATH"
POWERSPORTS_LOGO_SHA256_ENV = "EXTREMIZER_POWERSPORTS_LOGO_SHA256"


def _verified_png_path(path: Path, expected_sha256: str) -> Path | None:
    expected = expected_sha256.strip().lower()
    if re.fullmatch(r"[0-9a-f]{64}", expected) is None:
        return None
    if not path.is_file():
        return None
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return None
    digest = hashlib.sha256(data).hexdigest()
    if digest != expected:
        return None
    return path


def _verified_canonical_logo_path() -> Path | None:
    raw = os.getenv("EXTREMIZER_CANONICAL_LOGO_PATH", "").strip()
    if not raw:
        return None
    return _verified_png_path(Path(raw), CANONICAL_LOGO_SHA256)


def _verified_powersports_logo_path() -> Path | None:
    raw_path = os.getenv(POWERSPORTS_LOGO_PATH_ENV, "").strip()
    raw_sha = os.getenv(POWERSPORTS_LOGO_SHA256_ENV, "").strip()
    if not raw_path or not raw_sha:
        return None
    if raw_sha.lower() != APPROVED_POWERSPORTS_WORDMARK_SHA256:
        return None
    return _verified_png_path(Path(raw_path), raw_sha)


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

    @app.get("/web2-brand-logo", include_in_schema=False)
    def web2_brand_logo() -> FileResponse:
        path = _verified_canonical_logo_path()
        if path is None:
            raise HTTPException(status_code=404, detail="canonical logo unavailable")
        return FileResponse(path, media_type="image/png")

    @app.get("/web2-powersports-hero", include_in_schema=False)
    def web2_powersports_hero() -> FileResponse:
        """Serve only the SHA-matched, owner-approved sharp garage image."""
        path = _verified_powersports_garage_path()
        if path is None:
            raise HTTPException(status_code=404, detail="powersports garage unavailable")
        return FileResponse(path, media_type="image/png")

    @app.get("/web2-powersports-logo", include_in_schema=False)
    def web2_powersports_logo() -> FileResponse:
        """Serve only the exact Theme 7 horizontal PNG once its SHA is approved.

        There is intentionally no fallback to the square reference asset and no
        generated/reconstructed logo path.
        """
        path = _verified_powersports_logo_path()
        if path is None:
            raise HTTPException(status_code=404, detail="powersports logo unavailable")
        return FileResponse(path, media_type="image/png")

# Approved garage asset, supplied to isolated staging (no fallback or redraw).
APPROVED_POWERSPORTS_GARAGE_SHA256 = "24b1f57e51142cdd385d33a585f3b72b6706b64188eba5830508eedbe5b11a48"
APPROVED_POWERSPORTS_WORDMARK_SHA256 = "ac993368c8f07e3f635526dcb296003fa12c8b6f0835fab5744eedab91c0fecb"
POWERSPORTS_GARAGE_PATH_ENV = "EXTREMIZER_POWERSPORTS_GARAGE_PATH"
POWERSPORTS_GARAGE_SHA256_ENV = "EXTREMIZER_POWERSPORTS_GARAGE_SHA256"

def _verified_powersports_garage_path() -> Path | None:
    raw_path = os.getenv(POWERSPORTS_GARAGE_PATH_ENV, "").strip()
    raw_sha = os.getenv(POWERSPORTS_GARAGE_SHA256_ENV, "").strip()
    if not raw_path or not raw_sha:
        return None
    if raw_sha.lower() != APPROVED_POWERSPORTS_GARAGE_SHA256:
        return None
    return _verified_png_path(Path(raw_path), raw_sha)
