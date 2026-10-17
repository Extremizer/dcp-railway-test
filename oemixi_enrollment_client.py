"""Authenticated enrollment request from authorized producer/coordinator only."""
from __future__ import annotations
import json
import urllib.request
from urllib.parse import urlparse
from oemixi_enrollment_transport import PATH, canonical_payload, make_headers

class EnrollmentClient:
    def __init__(self,base_url,secret,*,opener=None):
        u=urlparse(base_url)
        if u.scheme!="https" or not u.hostname:
            raise ValueError("https_required")
        self.url=base_url.rstrip("/")+PATH
        self.secret=secret
        self.opener=opener or urllib.request.urlopen
    def __call__(self,manufacturer,oem):
        body=canonical_payload(manufacturer,oem)
        headers=make_headers(self.secret,body)
        req=urllib.request.Request(self.url,data=body,method="POST",
                                   headers={**headers,"Content-Type":"application/json"})
        try:
            with self.opener(req,timeout=6) as resp:
                if resp.status!=200:
                    return {"status":"CATEGORY_UNVERIFIED"}
                result=json.loads(resp.read(8193))
        except Exception:
            return {"status":"CATEGORY_UNVERIFIED"}
        if not isinstance(result,dict) or result.get("status")!="CATEGORY_VERIFIED":
            return {"status":"CATEGORY_UNVERIFIED"}
        if (result.get("manufacturer")!=manufacturer or result.get("oem")!=oem
            or not isinstance(result.get("authority"),dict)):
            return {"status":"CATEGORY_UNVERIFIED"}
        return result
