"""Independent signed Retail enrichment of existing verified DP cache entries.

No new queue/schema. GET is read-only; POST writes only the existing catalog
cache via COMMON CORE. Never requests DCP, imports files or creates orders.
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from threading import Lock

from fastapi import Header, HTTPException
from pydantic import BaseModel, Field

from client_offer_metadata import verified_msrp
from wops_retail import SOURCE, DETAIL_URL, identity

MAX_AGE_HOURS = 168
_WRITE_LOCK = Lock()


def _fresh(checked_at, hours=MAX_AGE_HOURS):
    try:
        checked = datetime.fromisoformat(str(checked_at))
        if checked.tzinfo is None:
            return False
        age = (datetime.now(timezone.utc) - checked.astimezone(timezone.utc)).total_seconds()
        return 0 <= age <= hours * 3600
    except (TypeError, ValueError):
        return False


def candidates(db_file):
    path = Path(db_file)
    if not path.is_file():
        return []
    with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as c:
        c.row_factory = sqlite3.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "dealer_price_cache" not in tables:
            return []
        catalog = {}
        if "oem_catalog_cache" in tables:
            catalog = {(r[0], r[1]): (r[2], r[3], r[4]) for r in c.execute(
                "SELECT manufacturer,current_oem,msrp_verified,last_verified_at,msrp_usd FROM oem_catalog_cache")}
        rows = c.execute("SELECT manufacturer,oem,last_verified_at,source FROM dealer_price_cache "
                         "WHERE dealer_price_usd>0 ORDER BY last_verified_at DESC").fetchall()
    result = []
    for row in rows:
        if (not identity(row["manufacturer"], row["oem"]) or not _fresh(row["last_verified_at"])
                or not str(row["source"] or "").startswith("verified_local_dcp:")):
            continue
        cached = catalog.get((row["manufacturer"], row["oem"]))
        if cached and cached[0] == 1 and _fresh(cached[1]) and verified_msrp(cached[2], SOURCE):
            continue
        result.append({"manufacturer": row["manufacturer"], "oem": row["oem"]})
        if len(result) == 100:
            break
    return result


class RetailResult(BaseModel):
    manufacturer: str
    oem: str
    supplier_sku: str
    retail_usd: float = Field(strict=True)
    currency: str
    source: str
    source_url: str
    checked_at: str


def register_routes(app, core, signature_guard):
    @app.get("/internal/retail-sync/next")
    def next_retail(x_dp_sync_ts: str | None = Header(default=None),
                    x_dp_sync_sig: str | None = Header(default=None)):
        signature_guard("/internal/retail-sync/next", x_dp_sync_ts, x_dp_sync_sig)
        return {"candidates": candidates(core.ORDERS_DB_FILE)}

    @app.post("/internal/retail-sync/result")
    def finish_retail(payload: RetailResult,
                      x_dp_sync_ts: str | None = Header(default=None),
                      x_dp_sync_sig: str | None = Header(default=None)):
        signature_guard("/internal/retail-sync/result", x_dp_sync_ts, x_dp_sync_sig)
        key = identity(payload.manufacturer, payload.oem)
        value = verified_msrp(payload.retail_usd, payload.source)
        if (not key or value is None or payload.currency != "USD"
                or payload.supplier_sku != key[2] or payload.source_url != DETAIL_URL + key[2]
                or not _fresh(payload.checked_at, hours=5 / 60)):
            raise HTTPException(status_code=400, detail={"code": "invalid_retail_evidence"})
        # Eligibility is checked again before any write. Fresh catalog entries
        # also reject replay, stale submissions and races with a DCP MSRP result.
        with _WRITE_LOCK:
            if {"manufacturer": key[0], "oem": key[1]} not in candidates(core.ORDERS_DB_FILE):
                raise HTTPException(status_code=409, detail={"code": "retail_not_pending"})
            with sqlite3.connect(Path(core.ORDERS_DB_FILE).resolve().as_uri() + "?mode=ro", uri=True) as c:
                previous = c.execute("SELECT name,catalog FROM oem_catalog_cache "
                                     "WHERE manufacturer=? AND current_oem=?", key[:2]).fetchone()
            ok = core.upsert_oem_catalog_cache(
                {"status": "FOUND", "manufacturer": key[0], "item_sku": key[1],
                 "query_oem": key[1], "name": previous[0] if previous else None,
                 "catalog": previous[1] if previous else "Parts"}, value,
                source_kind=SOURCE, source_ref=payload.source_url, verified_at=payload.checked_at)
        if not ok:
            raise HTTPException(status_code=500, detail={"code": "retail_cache_write_failed"})
        return {"ok": True}
