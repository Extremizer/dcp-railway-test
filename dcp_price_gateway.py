"""Isolated common-core price gateway and opt-in dealer adapter; no browser IO."""
from contextlib import closing
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


def _read_cache(db_file, manufacturer, oem, max_age_hours):
    # Stage 2 HOLD: legacy cache has no accepted independent Dealer semantics.
    # Never promote it to positive Dealer FOUND, regardless of numeric price.
    return None
    if not Path(db_file).exists():
        return None
    with closing(sqlite3.connect(Path(db_file).resolve().as_uri()+"?mode=ro",uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='dealer_price_cache'").fetchone():
            return None
        row = conn.execute("""SELECT dealer_price_usd,source,last_verified_at
            FROM dealer_price_cache WHERE manufacturer=? AND oem=?""",(manufacturer,oem)).fetchone()
    if row is None:
        return None
    result = {"status":"FOUND","manufacturer":manufacturer,"query_oem":oem,"current_oem":oem,
              "dealer_price_usd":row[0],"source":row[1],"last_verified_at":row[2],"currency":"USD",
              "verified_cache_hit":True}
    return result if _verified_price(result,manufacturer,oem,max_age_hours) else None


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
        if not _verified_price(result,manufacturer,oem,self.max_age_hours):
            return {"status":"NO_PRICE","error_code":"invalid_gateway_dealer_price"}
        return result
