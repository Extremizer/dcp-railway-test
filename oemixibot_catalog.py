"""OEMixiBOT: canonical production identity sources + persistent confirmations."""
import os,sqlite3
from pathlib import Path
import oem_reference_service,oemixibot_dcp_price,oem_identity_service
import dealercostparts_manufacturer_finder_v6_6 as finder
ORDERS_DB=Path(os.getenv("EXTREMIZER_ORDERS_DB_FILE","").strip() or Path(__file__).with_name("extremizer_orders.db"))
def _production_candidates(oem):
    values=set()
    if ORDERS_DB.exists():
        with sqlite3.connect(ORDERS_DB) as c:
            tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            specs=(("oem_catalog_cache","current_oem"),("oem_catalog_aliases","alias_oem"),
                   ("warehouse_stock_current","oem"),("dealer_price_cache","oem"),("order_items","oem"))
            for table,col in specs:
                if table not in tables: continue
                cols={r[1] for r in c.execute("pragma table_info("+table+")")}
                if col not in cols or "manufacturer" not in cols: continue
                for r in c.execute("select distinct manufacturer from "+table+" where "+col+"=?",(oem,)):
                    v=finder.manufacturer_alias(str(r[0] or "")); 
                    if v: values.add(v)
                if table=="order_items" and "requested_oem" in cols:
                    for r in c.execute("select distinct manufacturer from order_items where requested_oem=?",(oem,)):
                        v=finder.manufacturer_alias(str(r[0] or ""));
                        if v: values.add(v)
    return sorted(values)
def resolve_identity(oem):
    oem=finder.normalize_oem(oem); ref=oem_reference_service.lookup_oem(oem) or {}
    saved=oem_identity_service.lookup(oem)
    if saved:
        return {"oem":oem,"manufacturer":saved["manufacturer"],
                "item_type":ref.get("item_type") or saved.get("item_type"),"source":"persistent_confirmed"}
    candidates=_production_candidates(oem)
    if len(candidates)==1:
        oem_identity_service.remember(oem,candidates[0],ref.get("item_type"),"production_known_source")
        return {"oem":oem,"manufacturer":candidates[0],"item_type":ref.get("item_type"),
                "source":"production_known_source"}
    return {"oem":oem,"manufacturer":None,"manufacturer_candidates":candidates,
            "item_type":ref.get("item_type"),"source":"unresolved"}
def confirm_identity(oem,manufacturer,item_type=None,source="dcp_confirmed"):
    ref=oem_reference_service.lookup_oem(oem) or {}
    return oem_identity_service.remember(oem,manufacturer,item_type or ref.get("item_type"),source)
def resolve_offer(oem,manufacturer=None):
    ident=resolve_identity(oem); ref=oem_reference_service.lookup_oem(ident["oem"]) or {}
    if manufacturer:
        canonical=finder.manufacturer_alias(manufacturer)
        if not canonical: return {**ident,"status":"IDENTITY_REQUIRED"}
        ident["manufacturer"]=canonical
    if not ident.get("manufacturer"):
        return {**ident,"status":"IDENTITY_REQUIRED","actual_weight_kg":ref.get("actual_weight_kg"),
                "volume_weight_kg":ref.get("volume_weight_kg")}
    try: dp=oemixibot_dcp_price.get_dealer_price_dict(ident["manufacturer"],ident["oem"])
    except Exception as exc:
        return {**ident,"status":"PRICE_UNAVAILABLE","actual_weight_kg":ref.get("actual_weight_kg"),
                "volume_weight_kg":ref.get("volume_weight_kg"),"price_error":type(exc).__name__}
    if dp.get("status")=="FOUND" and dp.get("dealer_price_usd") is not None:
        current=dp.get("current_oem") or ident["oem"]
        confirm_identity(ident["oem"],ident["manufacturer"],ident.get("item_type"),"dcp_dl_confirmed")
        if current != ident["oem"]:
            confirm_identity(current,ident["manufacturer"],ident.get("item_type"),"dcp_dl_confirmed")
    return {**ident,"status":dp.get("status"),"oem":dp.get("current_oem") or ident["oem"],
            "requested_oem":ident["oem"],"name":dp.get("name"),"dl_usd":dp.get("dealer_price_usd"),
            "actual_weight_kg":ref.get("actual_weight_kg"),"volume_weight_kg":ref.get("volume_weight_kg"),
            "price_source":dp.get("source")}

