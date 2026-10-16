"""Controlled adapter boundary for existing DCP session. Disabled by default.

The browser controller is injected by a separately authorized deployment.
No independent Chrome session, navigation or checkout endpoints are created.
"""
from polaris_cart_snapshot import canonical
class ControlledDcpCartAdapter:
    def __init__(self,*,verify_session,read_snapshot,find_oem,
                 add_exact_one=None,remove_exact_line=None,decrement_exact_one=None,
                 allow_mutation=False):
        self._verify=verify_session
        self._snapshot=read_snapshot
        self._find=find_oem
        self._add=add_exact_one
        self._remove=remove_exact_line
        self._decrement=decrement_exact_one
        self.allow_mutation=allow_mutation
    def verify(self):
        try:return self._verify() is True
        except Exception:return False
    def snapshot(self):
        if not self.verify():raise PermissionError("DCP_SESSION_UNVERIFIED")
        snap=self._snapshot()
        canonical(snap)
        return snap
    def find_exact_oem(self,oem):
        if not self.verify():raise PermissionError("DCP_SESSION_UNVERIFIED")
        return self._find(oem)
    def _write(self,fn,value):
        if not self.allow_mutation or not callable(fn) or not self.verify():
            raise PermissionError("MUTATION_NOT_AUTHORIZED")
        return fn(value)
    def add_one(self,oem):
        return self._write(self._add,oem)
    def remove_line(self,token):
        return self._write(self._remove,token)
    def decrement_one(self,token):
        return self._write(self._decrement,token)
    def price(self,oem,snapshot):
        lines=[x for x in canonical(snapshot)["lines"] if x["sku"]==oem]
        if len(lines)!=1:raise ValueError("AMBIGUOUS_SKU")
        return lines[0]["unit_price"]
