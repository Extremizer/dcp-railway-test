"""Read-only Polaris exact Add form proof, including explicit DCP supersession."""
import re

OEM_RE=re.compile(r"[A-Z0-9-]{4,40}")

def _oem(value):
    value=str(value or "")
    if OEM_RE.fullmatch(value) is None:
        raise ValueError("OEM_UNVERIFIED")
    return value

def validate_form(oem, forms):
    oem=_oem(oem)
    if not isinstance(forms,list):
        raise ValueError("ADD_FORM_UNVERIFIED")
    found=[f for f in forms if isinstance(f,dict) and f.get("id")=="add_"+oem]
    if len(found)!=1:
        raise ValueError("ADD_FORM_UNVERIFIED")
    form=found[0]
    expected={
        "id":"add_"+oem,
        "action":"/cart/addoemsinglepart",
        "method":"POST",
        "manf":"polaris_atv",
        "sku":oem,
        "qty":"1",
    }
    if any(str(form.get(k,""))!=v for k,v in expected.items()):
        raise ValueError("ADD_FORM_UNVERIFIED")

    old=str(form.get("old_sku") or "")
    new=str(form.get("new_sku") or "")
    if not old and not new:
        resolved=oem
        replacement=False
    else:
        if old!=oem:
            raise ValueError("REPLACEMENT_UNVERIFIED")
        resolved=_oem(new)
        if resolved==oem:
            raise ValueError("REPLACEMENT_UNVERIFIED")
        replacement=True

    return {
        "manufacturer":"Polaris",
        "item_category":"Parts",
        "requested_oem":oem,
        "submit_oem":oem,
        "resolved_oem":resolved,
        "exact_match":True,
        "replacement_proven":replacement,
    }
