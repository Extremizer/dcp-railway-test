"""Server-only, read-only category authority from existing verified OEM catalog tables."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime, timezone

# Existing trusted catalog writer paths, not a heuristic derived from labels.
ACCEPTED_SOURCE_KINDS = frozenset({
    "validated_public_msrp", "validated_public_msrp_cache",
    "live_public_form_metadata",
})

def resolve(conn: sqlite3.Connection, manufacturer: str, requested_oem: str) -> dict | None:
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not {"oem_catalog_cache", "oem_catalog_aliases"} <= tables:
        return None
    # Independent direct current identity must agree with the alias target.
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


# Manufacturer-first Parts SEARCH admission for a *new* OEM with no MSRP/catalog row.
# This is an explicit search route, NOT a verified catalog identity, and must not
# be persisted as MSRP authority. A price can be written ONLY after the separately
# signed, fenced producer returns a verified authorized DCP result.
def resolve_search_route(conn: sqlite3.Connection, manufacturer: str, requested_oem: str) -> dict | None:
    approved = resolve(conn, manufacturer, requested_oem)
    if approved is not None:
        return approved
    import re
    import dealercostparts_manufacturer_finder_v6_6 as finder
    # Polaris dealer price requires existing verified category + actual CART proof.
    if manufacturer == "Polaris":
        return None
    if manufacturer not in {c.manufacturer for c in finder.CATALOGS if c.kind == "parts"}:
        return None
    if not isinstance(requested_oem,str) or not re.fullmatch(r"[A-Z0-9-]{4,40}",requested_oem):
        return None
    return {
        "item_category": "Parts",
        "category_source_kind": "explicit_manufacturer_parts_search",
        "category_source_ref": "manufacturer_first_oem_request",
        "category_verified_at": "1970-01-01T00:00:00+00:00",
        "resolved_oem": requested_oem,
    }
