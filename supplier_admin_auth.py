#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Shared admin identity rules for Supplier Orders."""
from __future__ import annotations
import hmac, os

def telegram_admin_ids():
    raw=os.getenv("EXTREMIZER_ADMIN_TELEGRAM_IDS","").strip()
    ids={int(x.strip()) for x in raw.split(",") if x.strip().isdigit()}
    # Current production admin remains the backward-compatible default.
    if not ids: ids={52637605}
    return ids

def is_telegram_admin(user_id):
    try:return int(user_id) in telegram_admin_ids()
    except (TypeError,ValueError):return False

def require_telegram_admin(user_id):
    if not is_telegram_admin(user_id): raise PermissionError("admin access required")
    return int(user_id)

def web_admin_token():
    return os.getenv("EXTREMIZER_WEB_ADMIN_TOKEN","").strip()

def require_web_admin_token(token):
    expected=web_admin_token()
    if not expected: raise PermissionError("WEB admin token is not configured")
    if not token or not hmac.compare_digest(str(token),expected):
        raise PermissionError("invalid WEB admin token")
    return True
