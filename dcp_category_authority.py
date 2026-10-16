"""Read-only server-side category authority from existing OEM catalog tables.

No client category, manufacturer hints, warehouse, dealer cache, or inferred route.
The verified MSRP catalog is category evidence only, never Dealer Price evidence.
"""
from __future__ import annotations
from datetime import datetime, timezone
import sqlite3

# Conservative accepted catalog attestation markers, not user-supplied routing.
ACCEPTED_SOURCE_KINDS = frozenset({
    "validated_public_msrp", "verified_public_msrp",
    "validated_public_msrp_cache", "verified_public_catalog",
})

def resolve(conn: sqlite3.Connection, manufacturer: str, requested_oem: str) -> dict | None:
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    if not {"oem_catalog_cache", "oem_catalog_aliases"} <= tables:
        return None
    aliases = conn.execute(
        """SELECT a.current_oem,a.alias_kind,c.catalog,c.msrp_verified,
                  c.source_kind,c.source_ref,c.last_verified_at
             FROM oem_catalog_aliases a JOIN oem_catalog_cache c
              ON c.manufacturer=a.manufacturer AND c.current_oem=a.current_oem
            WHERE a.manufacturer=? AND a.alias_oem=?""",
        (manufacturer, requested_oem),
    ).fetchall()
    if len(aliases) != 1:
        return None
    current, alias_kind, category, verified, kind, ref, checked = aliases[0]
    if (not current or not category or not str(category).strip()
        or verified != 1 or kind not in ACCEPTED_SOURCE_KINDS
        or not ref or not str(ref).strip()):
        return None
    if current != requested_oem and alias_kind not in ("previous_verified", "verified_previous"):
        return None
    if current == requested_oem and alias_kind not in ("current", "exact"):
        return None
    try:
        dt = datetime.fromisoformat(str(checked).replace("Z", "+00:00"))
        if dt.tzinfo is None or dt > datetime.now(timezone.utc):
            return None
    except (TypeError, ValueError, OverflowError):
        return None
    # A current SKU may not be mapped to conflicting catalog/category evidence.
    conflicts = conn.execute(
        """SELECT COUNT(DISTINCT COALESCE(NULLIF(TRIM(c.catalog),''),'#MISSING#'))
             FROM oem_catalog_aliases a JOIN oem_catalog_cache c
              ON c.manufacturer=a.manufacturer AND c.current_oem=a.current_oem
            WHERE a.manufacturer=? AND a.alias_oem=?""",
        (manufacturer, requested_oem),
    ).fetchone()[0]
    if conflicts != 1:
        return None
    return {
        "item_category": str(category).strip(),
        "category_source_kind": kind,
        "category_source_ref": str(ref).strip(),
        "category_verified_at": str(checked),
        "resolved_oem": str(current),
    }
