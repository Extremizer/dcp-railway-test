"""Canonical new-line or single-quantity merge delta. Never infer ambiguous changes."""
from polaris_cart_snapshot import canonical

def prove_delta(before,after,sku):
    b=canonical(before)["lines"];a=canonical(after)["lines"]
    old={x["token"]:x for x in b};new={x["token"]:x for x in a}
    created=set(new)-set(old)
    vanished=set(old)-set(new)
    if vanished:raise ValueError("existing_line_disappeared")
    if len(created)==1:
        if any(new[k]!=v for k,v in old.items()):raise ValueError("unrelated_line_changed")
        x=new[created.pop()]
        if x["sku"]!=sku or x["qty"]!=1:raise ValueError("wrong_new_line")
        return {"kind":"new","token":x["token"],"sku":sku,"qty_before":0,"qty_after":1,"delta":1}
    if created:raise ValueError("ambiguous_new_lines")
    changed=[(old[k],new[k]) for k in old if new[k]!=old[k]]
    if len(changed)!=1:raise ValueError("unproven_quantity_delta")
    before_line,after_line=changed[0]
    if (before_line["sku"]!=sku or after_line["sku"]!=sku
        or before_line["token"]!=after_line["token"]
        or before_line["qty"]+1!=after_line["qty"]
        or before_line["unit_price"]!=after_line["unit_price"]
        or before_line["currency"]!=after_line["currency"]):
        raise ValueError("unsafe_quantity_merge")
    return {"kind":"merge","token":before_line["token"],"sku":sku,
            "qty_before":before_line["qty"],"qty_after":after_line["qty"],"delta":1}

def expected_after(before,delta):
    b=canonical(before)
    if delta["kind"]=="new":
        raise ValueError("new_line_requires_observed_proof")
    lines=[dict(x) for x in b["lines"]]
    for x in lines:
        if x["token"]==delta["token"] and x["sku"]==delta["sku"]:
            if x["qty"]!=delta["qty_before"]:raise ValueError("wrong_qty")
            x["qty"]+=1
            from decimal import Decimal
            x["subtotal"]=str(Decimal(x["unit_price"])*x["qty"])
            return canonical({"lines":lines})
    raise ValueError("missing_merge_line")
