"""Isolated common-core price gateway and opt-in dealer adapter; no browser IO."""
from contextlib import closing
from polaris_dealer_cart_completion_contract import SOURCE
from datetime import datetime, timezone
import math
from pathlib import Path
import sqlite3

import dp_live_bridge
from dp_result_completion import MANUFACTURERS


def _verified_price(result, manufacturer, oem, max_age_hours):
    price = result.get("dealer_price_usd")
    checked = dp_live_bridge.dp_live_health._parse_datetime(result.get("last_verified_at"))
    now = datetime.now(timezone.utc)
    return (
        result.get("manufacturer") == manufacturer and result.get("query_oem") == oem
        and result.get("currency") == "USD" and dp_live_bridge._valid_dp(price,result.get("source"))
        and checked is not None and -60 <= (now-checked).total_seconds() <= max_age_hours*3600
        and max_age_hours > 0
    )


def _read_exact_ski_doo_cache(db_file, manufacturer, oem, max_age_hours, *, now=None):
    """Fail closed: only exact, server-committed Ski-Doo Parts DCP search.

    Existing Polaris cart semantics are untouched; no MSRP/retail fallback.
    Every accepted value must have an atomic request completion matching the
    cache value, source, OEM, category authority, and verification timestamp.
    """
    if manufacturer != "Ski-Doo":
        return None
    try:
        limit=min(float(max_age_hours), 24.0)
        if not math.isfinite(limit) or limit <= 0:
            return None
        observed=now if now is not None else datetime.now(timezone.utc)
        if observed.tzinfo is None:
            return None
        source_values=(
            f"verified_local_dcp:live_dom:current_oem_{oem}",
            f"verified_local_dcp:server_html:current_oem_{oem}",
        )
        uri=Path(db_file).resolve().as_uri()+"?mode=ro"
        with closing(sqlite3.connect(uri,uri=True,timeout=5)) as conn:
            row=conn.execute("""
                SELECT c.dealer_price_usd,c.source,c.last_verified_at,
                       r.current_oem,r.request_id,r.result_manufacturer,
                       r.item_category,r.category_source_kind,
                       r.category_source_ref,r.category_verified_at
                  FROM dealer_price_cache c
                  JOIN dp_live_requests r
                    ON r.manufacturer=c.manufacturer AND r.oem=c.oem
                   AND r.dealer_price_usd=c.dealer_price_usd
                   AND r.result_source=c.source
                   AND r.cache_committed_at=c.last_verified_at
                   AND r.result_status='FOUND' AND r.status='finished'
                 WHERE c.manufacturer=? AND c.oem=?
                 ORDER BY r.cache_committed_at DESC LIMIT 1
            """,(manufacturer,oem)).fetchone()
        if not row:
            return None
        price,source,verified,matched,rid,result_mfg,category,kind,ref,cat_verified=row
        if (source not in source_values or matched!=oem or result_mfg!=manufacturer
            or category!="Parts" or kind!="explicit_manufacturer_parts_search"
            or not ref or not cat_verified or not rid
            or not dp_live_bridge._valid_dp(price,source)):
            return None
        stamp=dp_live_bridge.dp_live_health._parse_datetime(verified)
        if stamp is None:
            return None
        age=(observed-stamp).total_seconds()
        if not -60 <= age <= limit*3600:
            return None
        return {"status":"FOUND","manufacturer":manufacturer,"query_oem":oem,
                "current_oem":oem,"dealer_price_usd":float(price),
                "source":source,"currency":"USD","last_verified_at":verified,
                "server_cache_committed":True,"verified_cache_hit":True,
                "request_id":rid}
    except (OSError,sqlite3.Error,ValueError,TypeError,OverflowError):
        return None


def _read_cache(db_file, manufacturer, oem, max_age_hours, *, now=None):
    """Read strictly evidenced, current dealer prices without MSRP fallback."""
    if manufacturer == "Ski-Doo":
        return _read_exact_ski_doo_cache(
            db_file,manufacturer,oem,max_age_hours,now=now)
    if manufacturer != "Polaris":
        return None
    try:
        limit=min(float(max_age_hours),24.0)
        if not math.isfinite(limit) or limit<=0:
            return None
        observed=now if now is not None else datetime.now(timezone.utc)
        with closing(sqlite3.connect(str(db_file),timeout=5)) as conn:
            row=conn.execute("""
                SELECT c.dealer_price_usd,c.source,c.last_verified_at,
                       r.current_oem,r.request_id,e.evidence_sha256
                  FROM dealer_price_cache c
                  JOIN dp_live_requests r
                    ON r.manufacturer=c.manufacturer
                   AND r.dealer_price_usd=c.dealer_price_usd
                   AND r.result_source=c.source
                   AND r.cache_committed_at=c.last_verified_at
                   AND r.result_status='FOUND'
                   AND r.status='finished'
                   AND (r.oem=c.oem OR r.current_oem=c.oem)
                  JOIN dp_live_recovery_events e
                    ON e.request_id=r.request_id
                   AND e.claim_generation=r.claim_generation
                   AND e.event_type='polaris_cart_verified_found'
                   AND e.actor=r.claim_owner
                 WHERE c.manufacturer=? AND c.oem=? AND c.source=?
                 ORDER BY r.cache_committed_at DESC LIMIT 1
            """,(manufacturer,oem,SOURCE)).fetchone()
        if row is None or not isinstance(row[5],str) or len(row[5])!=64:
            return None
        stamp=dp_live_bridge.dp_live_health._parse_datetime(row[2])
        if stamp is None or observed.tzinfo is None:
            return None
        age=(observed-stamp).total_seconds()
        if not (0<=age<=limit*3600):
            return None
        price=float(row[0])
        if not dp_live_bridge._valid_dp(price,row[1]):
            return None
        return {"status":"FOUND","manufacturer":manufacturer,"query_oem":oem,
                "current_oem":row[3] or oem,"dealer_price_usd":price,
                "source":SOURCE,"currency":"USD","last_verified_at":row[2],
                "server_cache_committed":True,"request_id":row[4]}
    except (sqlite3.Error,ValueError,TypeError,OverflowError):
        return None


def request_price(db_file, manufacturer, oem, *, max_age_hours=168.0, wait_seconds=10.0,
                  live_request=None):
    manufacturer,oem = dp_live_bridge._request_key(manufacturer,oem)
    if manufacturer not in MANUFACTURERS or not oem or not math.isfinite(max_age_hours) or max_age_hours<=0:
        return {"status":"INVALID"}
    cached = _read_cache(db_file,manufacturer,oem,max_age_hours)
    if cached:
        return cached
    result = (live_request or dp_live_bridge.request_live_dp)(db_file,manufacturer,oem,wait_seconds=wait_seconds)
    if result.get("status") != "FOUND":
        return {k:v for k,v in result.items() if k not in {"dealer_price_usd","msrp_usd","retail","rrp"}}
    if result.get("server_cache_committed") is not True:
        return {"status":"NO_PRICE","error_code":"atomic_completion_not_proven"}
    # FOUND is useful only after the server atomically committed verified DP.
    cached = _read_cache(db_file,manufacturer,oem,max_age_hours)
    if cached:
        return {**cached,"server_cache_committed":True,"cache_committed_at":result.get("cache_committed_at"),"request_id":result.get("request_id"),"current_oem":result.get("current_oem") or oem,
                "name":result.get("name")}
    return {"status":"NO_PRICE","error_code":"verified_cache_completion_missing"}


class SharedDealerPriceAdapter:
    """Explicit dependency injection only. Installed OEMixiBOT is not switched."""
    def __init__(self, gateway, *, max_age_hours):
        if type(max_age_hours) not in (int,float) or not math.isfinite(max_age_hours) or max_age_hours<=0:
            raise ValueError("explicit_valid_dealer_freshness_policy_required")
        self.gateway, self.max_age_hours = gateway, max_age_hours

    def get_dealer_price_dict(self, manufacturer, oem):
        manufacturer,oem = dp_live_bridge._request_key(manufacturer,oem)
        if manufacturer not in MANUFACTURERS or not oem:
            return {"status":"INVALID"}
        if not callable(self.gateway):
            return {"status":"TECHNICAL_ERROR","error_code":"shared_gateway_not_configured"}
        try:
            result = self.gateway(manufacturer,oem,max_age_hours=self.max_age_hours)
        except Exception:
            return {"status":"TECHNICAL_ERROR","error_code":"shared_gateway_unavailable"}
        if not isinstance(result, dict):
            return {"status":"TECHNICAL_ERROR","error_code":"invalid_gateway_response"}
        if result.get("status") != "FOUND":
            return {k:v for k,v in result.items() if k not in {"dealer_price_usd","msrp_usd","retail","rrp"}}
        # Preserve the Polaris dealer-cart-only contract unchanged.
        if (manufacturer=="Polaris" and result.get("server_cache_committed") is True
            and _verified_price(result,manufacturer,oem,min(self.max_age_hours,24.0))
            and result.get("source")==SOURCE):
            return {k:v for k,v in result.items() if k not in
                    ("msrp_usd","retail","rrp")}
        # Allow only the exact server-committed Ski-Doo Parts proof. A raw
        # FOUND, MSRP, client price or uncorrelated source cannot unlock it.
        exact_source={
            f"verified_local_dcp:live_dom:current_oem_{oem}",
            f"verified_local_dcp:server_html:current_oem_{oem}",
        }
        if (manufacturer=="Ski-Doo"
            and result.get("server_cache_committed") is True
            and result.get("verified_cache_hit") is True
            and result.get("current_oem")==oem
            and result.get("request_id")
            and result.get("source") in exact_source
            and _verified_price(result,manufacturer,oem,min(self.max_age_hours,24.0))):
            return {k:v for k,v in result.items() if k not in ("msrp_usd","retail","rrp")}
        return {"status":"DEALER_PRICE_UNVERIFIED"}
