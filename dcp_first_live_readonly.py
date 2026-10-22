"""Polaris 2205250 single-run PRE-LIVE gate. No supplier networking or cart mutation.

Real browser/CDP and signed producer admission require a separate owner GO and
verified Linux source. This module is deliberately incapable of LIVE lookup.
"""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

OEM="2205250"
MANUFACTURER="Polaris"

def verify(env):
    errors=[]
    if sys.platform!="linux":errors.append("LINUX_REQUIRED")
    if env.get("EXTREMIZER_DCP_LIVE_OWNER_GO")!="1":
        errors.append("OWNER_LIVE_GO_MISSING")
    if env.get("EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT_PRESENT")!="1":
        errors.append("DEALER_IDENTITY_NOT_ATTESTED")
    if env.get("EXTREMIZER_DCP_BROWSER_SOURCE_VERIFIED")!="1":
        errors.append("CHROMIUM_SOURCE_NOT_VERIFIED")
    if env.get("EXTREMIZER_DCP_OWNERSHIP_FENCED")!="1":
        errors.append("OWNERSHIP_ADMISSION_NOT_VERIFIED")
    if env.get("EXTREMIZER_DCP_SIGNED_BROKER_VERIFIED")!="1":
        errors.append("SIGNED_BROKER_NOT_VERIFIED")
    if env.get("EXTREMIZER_DCP_OEM")!=OEM or env.get("EXTREMIZER_DCP_MANUFACTURER")!=MANUFACTURER:
        errors.append("EXACT_OEM_MANUFACTURER_MISMATCH")
    base=env.get("EXTREMIZER_DCP_STATE_DIR","")
    if not base or not Path(base).is_absolute() or not Path(base).is_dir():
        errors.append("PERSISTENT_JOURNAL_NOT_VERIFIED")
    # Polaris verified dealer cart is the accepted price authority. Never
    # execute a cart mutation automatically, even if every readiness input passes.
    errors.append("POLARIS_READ_ONLY_PRICE_SOURCE_UNPROVEN_CART_MUTATION_HOLD")
    return {"status":"BLOCKED","manufacturer":MANUFACTURER,"oem":OEM,
            "dealer_price_usd":None,"reason_codes":errors,
            "browser_requests":0,"cart_mutations":0,"broker_completions":0}

if __name__=="__main__":
    print(json.dumps(verify(os.environ),sort_keys=True))
    sys.exit(2)
