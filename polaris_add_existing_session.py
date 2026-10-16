"""Polaris AJAX Add over an injected, existing authorized browser evaluator."""
from polaris_add_form_contract import validate_form

def inspect_exact_form(oem, *, read_forms, verify):
    if verify() is not True:
        raise PermissionError("DEALER_SESSION_UNVERIFIED")
    result=validate_form(oem,read_forms(oem))
    if verify() is not True:
        raise PermissionError("DEALER_SESSION_CHANGED")
    return result

def add_one(oem, *, read_forms, perform_post, verify, allow_mutation=False):
    if allow_mutation is not True:
        raise PermissionError("MUTATION_DISABLED")
    proof=inspect_exact_form(oem,read_forms=read_forms,verify=verify)
    if verify() is not True:
        raise PermissionError("DEALER_SESSION_CHANGED")
    # Submit the requested OEM exactly as DCP's form requires.
    # A proven supersession affects cart-delta expectations, not the submitted SKU.
    perform_post("/cart/addoemsinglepart",
        {"manf":"polaris_atv","sku":proof["submit_oem"],"qty":"1"})
    if verify() is not True:
        raise PermissionError("DEALER_SESSION_LOST_AFTER_POST")
    return {
        "submitted":True,
        "requested_oem":proof["requested_oem"],
        "resolved_oem":proof["resolved_oem"],
        "cart_delta_verified":False,
    }
