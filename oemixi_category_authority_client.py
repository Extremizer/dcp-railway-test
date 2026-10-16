"""Windows-safe read-only client for Railway Category Authority."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
import time
from typing import Any, Callable
import urllib.error
import urllib.request
from urllib.parse import urlparse

PATH="/internal/oemixi/category-authority/v1"
PROTOCOL_VERSION="oemixi-category-authority-v1"
MAX_RESPONSE_BYTES=8192

_MANUFACTURERS={
    "arcticcat":"Arctic Cat","skidoo":"Ski-Doo","canam":"Can-Am",
    "seadoo":"Sea-Doo","cfmoto":"CFMoto","honda":"Honda","yamaha":"Yamaha",
    "kawasaki":"Kawasaki","suzuki":"Suzuki","polaris":"Polaris",
    "ktm":"KTM","indian":"Indian",
}

class CategoryAuthorityTransportError(RuntimeError):
    def __init__(self,code):
        self.code=str(code or "CATEGORY_AUTHORITY_UNAVAILABLE")
        super().__init__(self.code)

def derive_authority_key(root_secret):
    secret=str(root_secret or "").strip()
    if len(secret)<16:
        raise ValueError("oemixibot_authority_root_secret_required")
    return hashlib.sha256(
        b"extremizer-oemixi-category-authority-v1\0"+secret.encode("utf-8")
    ).digest()

def normalize_identity(manufacturer,oem):
    raw=re.sub(r"[\s_-]","",str(manufacturer or "")).lower()
    canonical=_MANUFACTURERS.get(raw,"")
    normalized_oem=re.sub(r"\s+","",str(oem or "")).upper()
    if not canonical or not re.fullmatch(r"[A-Z0-9-]{4,40}",normalized_oem):
        return "",""
    return canonical,normalized_oem

def canonical_body(manufacturer,oem):
    return json.dumps(
        {"manufacturer":manufacturer,"oem":oem},
        separators=(",",":"),sort_keys=True,ensure_ascii=False,
    ).encode("utf-8")

def _message(timestamp,body):
    return (
        f"{PROTOCOL_VERSION}\nPOST\n{PATH}\n{timestamp}\n"
        f"{hashlib.sha256(body).hexdigest()}"
    ).encode("utf-8")

def sign_request(root_secret,*,timestamp,body):
    return hmac.new(
        derive_authority_key(root_secret),_message(str(timestamp),body),hashlib.sha256
    ).hexdigest()

@dataclass(frozen=True)
class HTTPSCategoryAuthorityResolver:
    base_url:str
    root_secret:str
    timeout_seconds:float=5.0
    max_response_bytes:int=MAX_RESPONSE_BYTES
    opener:Callable[...,Any]=urllib.request.urlopen

    def __post_init__(self):
        parsed=urlparse(str(self.base_url or ""))
        if parsed.scheme!="https" or not parsed.netloc:
            raise ValueError("https_authority_url_required")
        if not (0<float(self.timeout_seconds)<=15):
            raise ValueError("invalid_authority_timeout")
        derive_authority_key(self.root_secret)

    @property
    def endpoint(self):
        return self.base_url.rstrip("/")+PATH

    def __call__(self,manufacturer,oem):
        manufacturer,oem=normalize_identity(manufacturer,oem)
        if not manufacturer or not oem:
            return None
        body=canonical_body(manufacturer,oem)
        ts=str(int(time.time()))
        sig=sign_request(self.root_secret,timestamp=ts,body=body)
        req=urllib.request.Request(
            self.endpoint,data=body,method="POST",
            headers={
                "Content-Type":"application/json",
                "Accept":"application/json",
                "X-OEMixi-Authority-TS":ts,
                "X-OEMixi-Authority-Sig":sig,
            },
        )
        try:
            with self.opener(req,timeout=float(self.timeout_seconds)) as response:
                raw=response.read(self.max_response_bytes+1)
                if len(raw)>self.max_response_bytes:
                    raise CategoryAuthorityTransportError(
                        "CATEGORY_AUTHORITY_RESPONSE_TOO_LARGE"
                    )
        except urllib.error.HTTPError as exc:
            if exc.code in (401,403):
                raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_AUTH_FAILED")
            if exc.code==503:
                raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_UNAVAILABLE")
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_HTTP_ERROR")
        except CategoryAuthorityTransportError:
            raise
        except Exception:
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_UNAVAILABLE")

        try:
            decoded=json.loads(raw.decode("utf-8"))
        except Exception:
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        if not isinstance(decoded,dict):
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        status=str(decoded.get("status") or "")
        if status=="CATEGORY_UNVERIFIED":
            return None
        if status!="CATEGORY_VERIFIED":
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        required=(
            "resolved_oem","item_category","category_source_kind",
            "category_source_ref","category_verified_at",
        )
        if any(not isinstance(decoded.get(k),str) or not decoded[k].strip() for k in required):
            raise CategoryAuthorityTransportError("CATEGORY_AUTHORITY_INVALID_RESPONSE")
        return {k:decoded[k] for k in required}
