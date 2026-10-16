"""Canonical semantic snapshots of a DCP cart; no browser or HTTP access."""
from __future__ import annotations
from decimal import Decimal, InvalidOperation
import hashlib
import json

FIELDS=("sku","token","qty","unit_price","subtotal","currency")

def money(value):
    try:
        v=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError):
        raise ValueError("invalid_money") from None
    if not v.is_finite() or v < 0 or v.as_tuple().exponent < -2:
        raise ValueError("invalid_money")
    return v

def canonical(snapshot):
    if not isinstance(snapshot,dict) or not isinstance(snapshot.get("lines"),list):
        raise ValueError("invalid_snapshot")
    lines=[]; seen=set()
    for source in snapshot["lines"]:
        if not isinstance(source,dict) or any(k not in source for k in FIELDS):
            raise ValueError("incomplete_line")
        sku=source["sku"]; token=source["token"]; qty=source["qty"]; currency=source["currency"]
        if not all(isinstance(x,str) and x.strip() for x in (sku,token,currency)):
            raise ValueError("invalid_identity")
        if token in seen or isinstance(qty,bool) or not isinstance(qty,int) or qty<1:
            raise ValueError("ambiguous_line")
        seen.add(token)
        unit=money(source["unit_price"]); subtotal=money(source["subtotal"])
        if unit<=0 or subtotal != unit*qty or currency!="USD":
            raise ValueError("invalid_line_price")
        lines.append({"sku":sku,"token":token,"qty":qty,"unit_price":str(unit),
                      "subtotal":str(subtotal),"currency":currency})
    lines.sort(key=lambda x:(x["sku"],x["token"]))
    if "item_count" in snapshot and snapshot["item_count"] != sum(x["qty"] for x in lines):
        raise ValueError("item_count_mismatch")
    if "cart_total" in snapshot and snapshot["cart_total"] is not None:
        if money(snapshot["cart_total"])!=sum((money(x["subtotal"]) for x in lines),Decimal(0)):
            raise ValueError("cart_total_mismatch")
    return {"lines":lines,"item_count":sum(x["qty"] for x in lines),
            "cart_total":str(sum((money(x["subtotal"]) for x in lines),Decimal(0)))}

def digest(snapshot):
    data=json.dumps(canonical(snapshot),sort_keys=True,separators=(",",":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()

def exact_new_line(before,after,sku):
    """Only a single NEW qty=1 line is supported; merges are unsafe, not guessed."""
    b=canonical(before); a=canonical(after)
    old={x["token"]:x for x in b["lines"]}; new={x["token"]:x for x in a["lines"]}
    created=set(new)-set(old)
    if len(created)!=1 or any(new.get(k)!=v for k,v in old.items()):
        raise ValueError("unproven_cart_delta")
    line=new[created.pop()]
    if line["sku"]!=sku or line["qty"]!=1:
        raise ValueError("unexpected_added_line")
    return line
