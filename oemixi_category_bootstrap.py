"""Server-only atomic Category Authority bootstrap. No consumer-provided proof accepted.

The evidence verifier is a trusted independent server/producer boundary.
This module never treats a displayed MSRP or manufacturer selection as evidence.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
import re
import sqlite3

import dcp_category_authority

ALLOWED_SOURCES = frozenset({"validated_public_msrp", "live_public_form_metadata"})
OEM = re.compile(r"[A-Z0-9-]{4,40}\Z")


def bootstrap_category(db_file, selected_manufacturer, requested_oem, *,
                       trusted_catalog_verifier=None):
    """Create authority only after independent trusted *exact* catalog evidence.

    Caller must provide a trusted verifier from the server/producer runtime,
    not raw user/browser-submitted data. No writes on failure.
    """
    if trusted_catalog_verifier is None:
        return {"status": "ENROLLMENT_VERIFIER_UNAVAILABLE"}
    try:
        from dealercostparts_manufacturer_finder_v6_6 import manufacturer_alias, normalize_oem
        manufacturer = manufacturer_alias(selected_manufacturer)
        oem = normalize_oem(requested_oem)
    except (ValueError, TypeError):
        return {"status": "INVALID_IDENTITY"}
    if not manufacturer or not OEM.fullmatch(oem):
        return {"status": "INVALID_IDENTITY"}
    if manufacturer != "Polaris":
        return {"status": "MANUFACTURER_NOT_ENABLED"}
    try:
        evidence = trusted_catalog_verifier(manufacturer, oem)
    except Exception:
        return {"status": "ENROLLMENT_VERIFIER_UNAVAILABLE"}
    if not isinstance(evidence, dict) or evidence.get("verified") is not True:
        return {"status": "CATEGORY_UNVERIFIED"}
    if evidence.get("manufacturer") != manufacturer or evidence.get("requested_oem") != oem:
        return {"status": "CATEGORY_MISMATCH"}
    current = evidence.get("current_oem")
    if current != oem or not isinstance(current, str) or not OEM.fullmatch(current):
        # Never bootstrap an alias without independent current/previous proof.
        return {"status": "ALIAS_AUTHORITY_REQUIRED"}
    catalog = str(evidence.get("catalog") or "").strip()
    source_kind = evidence.get("source_kind")
    source_ref = str(evidence.get("source_ref") or "").strip()
    verified_at = evidence.get("verified_at")
    try:
        stamp = datetime.fromisoformat(str(verified_at).replace("Z", "+00:00"))
        now = datetime.now(timezone.utc)
        if stamp.tzinfo is None or not (0 <= (now-stamp).total_seconds() <= 86400):
            return {"status": "CATEGORY_STALE"}
    except (ValueError, TypeError, OverflowError):
        return {"status": "CATEGORY_STALE"}
    if not catalog or source_kind not in ALLOWED_SOURCES or not source_ref:
        return {"status": "CATEGORY_UNVERIFIED"}
    msrp = evidence.get("msrp_usd")
    try:
        msrp = float(msrp)
    except (ValueError, TypeError):
        return {"status": "MSRP_UNVERIFIED"}
    if not math.isfinite(msrp) or msrp <= 0:
        return {"status": "MSRP_UNVERIFIED"}
    try:
        with sqlite3.connect(db_file, timeout=3) as c:
            c.execute("BEGIN IMMEDIATE")
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"oem_catalog_cache","oem_catalog_aliases"} <= tables:
                return {"status": "SCHEMA_UNAVAILABLE"}
            existing = c.execute(
                "SELECT catalog,source_kind,source_ref FROM oem_catalog_cache WHERE manufacturer=? AND current_oem=?",
                (manufacturer,oem)).fetchone()
            alias = c.execute(
                "SELECT current_oem,alias_kind FROM oem_catalog_aliases WHERE manufacturer=? AND alias_oem=?",
                (manufacturer,oem)).fetchone()
            if existing and existing[0] != catalog:
                return {"status":"CATEGORY_CONFLICT"}
            if alias and (alias[0] != oem or alias[1] != "current"):
                return {"status":"CATEGORY_CONFLICT"}
            c.execute(
                """INSERT INTO oem_catalog_cache
                (manufacturer,current_oem,name,catalog,msrp_usd,msrp_verified,
                 previous_oems_json,source_kind,source_ref,first_seen_at,last_verified_at)
                VALUES (?,?,?,?,?,1,'[]',?,?,?,?)
                ON CONFLICT(manufacturer,current_oem) DO UPDATE SET
                 catalog=excluded.catalog,msrp_usd=excluded.msrp_usd,
                 msrp_verified=1,source_kind=excluded.source_kind,
                 source_ref=excluded.source_ref,last_verified_at=excluded.last_verified_at""",
                (manufacturer,oem,evidence.get("name"),catalog,msrp,
                 source_kind,source_ref,verified_at,verified_at))
            c.execute(
                """INSERT INTO oem_catalog_aliases(manufacturer,alias_oem,current_oem,alias_kind)
                VALUES (?,?,?,'current')
                ON CONFLICT(manufacturer,alias_oem) DO NOTHING""",
                (manufacturer,oem,oem))
            authority = dcp_category_authority.resolve(c,manufacturer,oem)
            if authority is None or authority.get("resolved_oem") != oem:
                raise ValueError("CATEGORY_AUTHORITY_NOT_ESTABLISHED")
            c.commit()
            return {"status":"CATEGORY_VERIFIED", "manufacturer":manufacturer,
                    "oem":oem,"authority":authority}
    except (sqlite3.Error, ValueError):
        return {"status":"CATEGORY_UNVERIFIED"}
