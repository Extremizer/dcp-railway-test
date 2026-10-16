"""Server-only, read-only category authority from existing verified OEM catalog tables."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

ACCEPTED_SOURCE_KINDS = frozenset({
    "validated_public_msrp", "validated_public_msrp_cache",
    "live_public_form_metadata",
})

def resolve(conn: sqlite3.Connection, manufacturer: str, requested_oem: str) -> dict | None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"oem_catalog_cache", "oem_catalog_aliases"} <= tables:
        return None
    direct = conn.execute(
        "SELECT catalog FROM oem_catalog_cache WHERE manufacturer=? AND current_oem=?",
        (manufacturer, requested_oem)).fetchall()
    aliases = conn.execute(
        """SELECT a.current_oem,a.alias_kind,c.catalog,c.msrp_verified,
                  c.source_kind,c.source_ref,c.last_verified_at,c.previous_oems_json
             FROM oem_catalog_aliases a JOIN oem_catalog_cache c
               ON c.manufacturer=a.manufacturer AND c.current_oem=a.current_oem
            WHERE a.manufacturer=? AND a.alias_oem=?""",
        (manufacturer, requested_oem)).fetchall()
    if len(aliases) != 1:
        return None
    current, alias_kind, category, verified, kind, ref, checked, previous_json = aliases[0]
    if (not current or not str(category or "").strip() or verified != 1
        or kind not in ACCEPTED_SOURCE_KINDS or not str(ref or "").strip()):
        return None
    if direct and (len(direct) != 1 or current != requested_oem
                   or str(direct[0][0] or "").strip() != str(category).strip()):
        return None
    if current == requested_oem:
        if alias_kind != "current":
            return None
    else:
        if alias_kind != "previous" or direct:
            return None
        try:
            previous = json.loads(previous_json or "[]")
        except (TypeError, ValueError):
            return None
        if not isinstance(previous, list) or requested_oem not in previous:
            return None
    try:
        verified_at = datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
        if verified_at.tzinfo is None or verified_at > datetime.now(timezone.utc):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    return {
        "item_category": str(category).strip(),
        "category_source_kind": kind,
        "category_source_ref": str(ref).strip(),
        "category_verified_at": str(checked),
        "resolved_oem": str(current),
    }
