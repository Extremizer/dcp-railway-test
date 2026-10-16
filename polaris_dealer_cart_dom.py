"""Pure HTML Layer A for observed Polaris authenticated dealer-cart rows.

No browser, network, cart mutation or price cache. Caller must supply trusted
session and server category evidence; row HTML never proves either.
"""
from __future__ import annotations
from bs4 import BeautifulSoup
from decimal import Decimal, InvalidOperation
import re
from polaris_dealer_cart_contract import LOGICAL_SOURCE, validate

CONTROL = re.compile(r"^/cart/(decrease|increase|remove)/([A-Za-z0-9]+)$")
SKU = re.compile(r"\bitem:\s*([A-Za-z0-9-]+)", re.I)
OLD = re.compile(r"\bOld Sku:\s*([A-Za-z0-9-]+)", re.I)

def _amount(value):
    value = str(value or "").strip().replace("$", "").replace(",", "")
    if not re.fullmatch(r"\d+(?:\.\d{1,2})?", value):
        return None
    try: return Decimal(value)
    except InvalidOperation: return None

def extract(html, *, requested_oem, dealer_authenticated=False,
            category_server_verified=False, observed_at=None, source_provenance=None,
            replacement_chain_verified=False, replacement_provenance=None):
    def reject(reason):
        return {"status":"DEALER_PRICE_UNVERIFIED","reason":reason,"dealer_price_usd":None}
    if not dealer_authenticated or not category_server_verified:
        return reject("trusted_context_missing")
    if not isinstance(html,str) or not isinstance(requested_oem,str):
        return reject("invalid_input")
    soup=BeautifulSoup(html,"html.parser")
    if "Prices shown are USD" not in soup.get_text(" ",strip=True):
        return reject("page_usd_evidence_missing")
    rows=soup.select(".cartitem.container_16")
    matches=[]
    for row in rows:
        controls=[]
        for a in row.select('a[href]'):
            m=CONTROL.fullmatch(a.get("href",""))
            if m: controls.append((m.group(1),m.group(2),a))
        ids={a.get("data-itemid") for _,_,a in controls}
        sku_text=" ".join(x.get_text(" ",strip=True) for x in row.select(".grid_4.desc .sku"))
        vis=SKU.search(sku_text)
        if requested_oem in ids or vis and vis.group(1)==requested_oem:
            matches.append((row,controls,ids,vis))
    if len(matches)!=1:
        return reject("sku_match_ambiguous")
    row,controls,ids,vis=matches[0]
    if not controls or ids!={requested_oem} or not vis or vis.group(1)!=requested_oem:
        return reject("sku_conflict")
    tokens={token for _,token,_ in controls}
    actions=[act for act,_,_ in controls]
    if len(tokens)!=1 or len(actions)!=len(set(actions)) or set(actions)!={"decrease","increase","remove"}:
        return reject("cart_token_ambiguous")
    token=next(iter(tokens))
    if sum(1 for r in rows if any(CONTROL.fullmatch(a.get("href","")) and CONTROL.fullmatch(a.get("href","")).group(2)==token for a in r.select('a[href]')))!=1:
        return reject("token_reused")
    attrs=("data-brand","data-currency","data-itemname","data-sellprice","data-origprice","data-qty")
    for k in attrs:
        if len({a.get(k) for _,_,a in controls})!=1:
            return reject("control_attribute_conflict")
    ref=controls[0][2]
    if ref.get("data-brand")!="Polaris" or ref.get("data-currency")!="USD":
        return reject("manufacturer_or_currency_conflict")
    desc=row.select_one(".grid_4.desc .title")
    name=desc.get_text(" ",strip=True) if desc else ""
    if not name or ref.get("data-itemname")!=name:
        return reject("product_name_conflict")
    unit=row.select_one(".grid_2.itemprice p.price")
    qty=row.select_one(".grid_2.modify p.qty")
    total=row.select_one(".grid_2.ext p.price")
    if unit is None or qty is None or total is None:
        return reject("missing_price_qty_total")
    price=_amount(unit.get_text(" ",strip=True))
    subtotal=_amount(total.get_text(" ",strip=True))
    sell=_amount(ref.get("data-sellprice"))
    if price is None or subtotal is None or sell is None or price!=sell:
        return reject("unit_price_conflict")
    qty_text=qty.get_text(" ",strip=True)
    if not re.fullmatch(r"[1-9][0-9]*",qty_text) or qty_text!=ref.get("data-qty"):
        return reject("qty_conflict")
    old=" ".join(x.get_text(" ",strip=True) for x in row.select(".grid_4.opt"))
    previous=OLD.search(old)
    candidate={
        "dealer_authenticated":True,"context":"dealer_cart","manufacturer":"Polaris",
        "item_category":"Parts","category_server_verified":True,
        "requested_oem":requested_oem,"matched_sku":requested_oem,"current_oem":requested_oem,
        "cart_line_unique":True,"cart_line_stable":True,"cart_line_id":token,
        "quantity":int(qty_text),"currency":"USD","source":LOGICAL_SOURCE,
        "source_provenance":source_provenance,"price_kind":"DEALER_CART_UNIT",
        "unit_price_usd":str(price),"unit_price_separately_identified":True,
        "subtotal_usd":str(subtotal),"observed_at":observed_at,
    }
    if previous: candidate["observed_previous_oem"]=previous.group(1)
    return candidate

def extract_and_validate(html, *, now=None, **kwargs):
    candidate=extract(html,**kwargs)
    if candidate.get("status")=="DEALER_PRICE_UNVERIFIED":
        return candidate
    return validate(candidate,now=now)
