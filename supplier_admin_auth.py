#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared admin identity rules for Telegram and WEB ADMIN."""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import time


WEB_ADMIN_COOKIE = "extremizer_admin_session"
WEB_ADMIN_SESSION_MAX_AGE = 12 * 60 * 60


def telegram_admin_ids():
    raw = os.getenv("EXTREMIZER_ADMIN_TELEGRAM_IDS", "").strip()
    ids = {int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
    # Current production admin remains the backward-compatible default.
    if not ids:
        ids = {52637605}
    return ids


def is_telegram_admin(user_id):
    try:
        return int(user_id) in telegram_admin_ids()
    except (TypeError, ValueError):
        return False


def require_telegram_admin(user_id):
    if not is_telegram_admin(user_id):
        raise PermissionError("admin access required")
    return int(user_id)


def web_admin_token():
    return os.getenv("EXTREMIZER_WEB_ADMIN_TOKEN", "").strip()


def require_web_admin_token(token):
    expected = web_admin_token()
    if not expected:
        raise PermissionError("WEB admin token is not configured")
    if not token or not hmac.compare_digest(str(token), expected):
        raise PermissionError("invalid WEB admin token")
    return True


def _web_admin_session_key() -> bytes:
    token = web_admin_token()
    if not token:
        raise PermissionError("WEB admin token is not configured")
    return hashlib.sha256(
        b"extremizer-web-admin-session-v1\0" + token.encode("utf-8")
    ).digest()


def issue_web_admin_session(now: int | None = None) -> str:
    """Return a signed, non-secret browser session value.

    The raw admin token is never stored in the browser cookie.
    Rotating EXTREMIZER_WEB_ADMIN_TOKEN invalidates every existing session.
    """
    issued_at = int(time.time() if now is None else now)
    nonce = secrets.token_urlsafe(18)
    payload = f"v1.{issued_at}.{nonce}"
    signature = hmac.new(
        _web_admin_session_key(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload}.{signature}"


def verify_web_admin_session(
    value,
    *,
    now: int | None = None,
    max_age: int = WEB_ADMIN_SESSION_MAX_AGE,
) -> bool:
    raw = str(value or "").strip()
    parts = raw.split(".")
    if len(parts) != 4 or parts[0] != "v1":
        return False
    try:
        issued_at = int(parts[1])
    except (TypeError, ValueError):
        return False

    current = int(time.time() if now is None else now)
    # Small future tolerance handles harmless clock skew.
    if issued_at > current + 60 or current - issued_at > int(max_age):
        return False

    payload = ".".join(parts[:3])
    expected = hmac.new(
        _web_admin_session_key(),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(parts[3], expected)


def is_web_admin_authorized(
    *,
    header_token=None,
    session_cookie=None,
) -> bool:
    expected = web_admin_token()
    if not expected:
        return False
    if header_token and hmac.compare_digest(str(header_token), expected):
        return True
    try:
        return verify_web_admin_session(session_cookie)
    except PermissionError:
        return False


def require_web_admin_access(
    *,
    header_token=None,
    session_cookie=None,
):
    if not is_web_admin_authorized(
        header_token=header_token,
        session_cookie=session_cookie,
    ):
        raise PermissionError("invalid WEB admin session")
    return True

def issue_apply_csrf_token(session_cookie: str, order_id: str) -> str:
    """Bind a stateless CSRF token to the signed admin session and order."""
    if not verify_web_admin_session(session_cookie):
        raise PermissionError("invalid WEB admin session")
    payload = ("apply-v1\0" + str(session_cookie) + "\0" + str(order_id)).encode("utf-8")
    return hmac.new(_web_admin_session_key(), payload, hashlib.sha256).hexdigest()


def verify_apply_csrf_token(session_cookie: str, order_id: str, token: str) -> bool:
    if not token:
        return False
    try:
        expected = issue_apply_csrf_token(session_cookie, order_id)
    except PermissionError:
        return False
    return hmac.compare_digest(str(token), expected)
