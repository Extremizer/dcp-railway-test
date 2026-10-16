"""Exact existing-session cart remove/decrement controls, never enabled by default.

Uses only the observed /cart/remove/<token> and /cart/decrease/<token> routes.
Action execution is injected: no network, browser creation, or live calls here.
"""
from decimal import Decimal
from urllib.parse import quote
from polaris_cart_snapshot import canonical

class ExactCartActions:
    def __init__(self, *, verify, snapshot, perform_existing_session_get, allow_mutation=False):
        self.verify=verify
        self.snapshot=snapshot
        self.perform=perform_existing_session_get
        self.allow_mutation=allow_mutation

    def _act(self, token, expected_itemid, expected_qty, operation):
        if not self.allow_mutation or self.verify() is not True:
            raise PermissionError("MUTATION_NOT_AUTHORIZED")
        if not isinstance(token,str) or not token or any(ch not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-' for ch in token):
            raise ValueError("INVALID_LINE_TOKEN")
        before=canonical(self.snapshot())
        rows=[x for x in before["lines"] if x["token"]==token]
        if len(rows)!=1 or rows[0]["sku"]!=expected_itemid or rows[0]["qty"]!=expected_qty:
            raise ValueError("LINE_PRECONDITION_MISMATCH")
        if operation=="decrease" and expected_qty<=1:
            raise ValueError("DECREMENT_BELOW_ONE")
        if self.verify() is not True:
            raise PermissionError("SESSION_UNVERIFIED")
        path=f"/cart/{operation}/{quote(token,safe='')}"
        self.perform(path)
        if self.verify() is not True:
            raise PermissionError("SESSION_UNVERIFIED_AFTER_ACTION")
        after=canonical(self.snapshot())
        prev={x["token"]:x for x in before["lines"]}
        nxt={x["token"]:x for x in after["lines"]}
        if operation=="remove":
            valid=token not in nxt and all(nxt.get(k)==v for k,v in prev.items() if k!=token) and len(nxt)==len(prev)-1
        else:
            old=prev[token]
            new=nxt.get(token)
            valid=(new is not None and new["sku"]==old["sku"] and new["qty"]==old["qty"]-1
                and new["unit_price"]==old["unit_price"] and new["currency"]==old["currency"]
                and Decimal(new["subtotal"])==Decimal(new["unit_price"])*new["qty"]
                and all(nxt.get(k)==v for k,v in prev.items() if k!=token)
                and len(nxt)==len(prev))
        if not valid:
            raise ValueError("CART_POSTCONDITION_MISMATCH")
        return True

    def remove_line(self,token,*,expected_itemid,expected_qty):
        return self._act(token,expected_itemid,expected_qty,"remove")

    def decrement_one(self,token,*,expected_itemid,expected_qty):
        return self._act(token,expected_itemid,expected_qty,"decrease")
