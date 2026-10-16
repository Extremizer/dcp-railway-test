"""Stable, non-sensitive failure codes for Polaris browser/probe orchestration."""
SAFE_CODES={
    "AUTH_REQUIRED",
    "DEALER_SESSION_UNVERIFIED",
    "DEALER_SESSION_CHANGED",
    "DEALER_SESSION_LOST_AFTER_ADD",
    "DCP_TAB_UNAVAILABLE",
    "WRONG_DCP_ORIGIN",
    "WRONG_SEARCH_CONTEXT",
    "CLOUDFLARE",
    "DCP_TRANSITION_TIMEOUT",
    "DCP_NAVIGATION_NOT_STARTED",
    "SEARCH_SUBMIT_NOT_STARTED",
    "ADD_FORM_READ_FAILED",
    "CART_SNAPSHOT_READ_FAILED",
    "CART_TOKEN_UNVERIFIED",
    "OEM_AUTHORITY_MISMATCH",
    "OEM_UNVERIFIED",
    "AMBIGUOUS_SKU",
    "PRICE_UNVERIFIED",
    "CART_CHANGED",
    "MUTATION_NOT_AUTHORIZED",
    "ADD_CLICK_NOT_STARTED",
    "CART_ACTION_NOT_STARTED",
    "UNAPPROVED_CART_ACTION",
    "RECOVERY_REQUIRED",
    "RESTORATION_FAILED",
    "BUSY",
}

class ProbeFailure(RuntimeError):
    def __init__(self,code,stage=None):
        code=str(code or "PROBE_INTERNAL_ERROR")
        self.code=code if code in SAFE_CODES else "PROBE_INTERNAL_ERROR"
        self.stage=str(stage or "")
        super().__init__(self.code)

def classify_failure(exc,default="PROBE_INTERNAL_ERROR"):
    if isinstance(exc,ProbeFailure):
        return exc.code
    text=str(exc or "").strip()
    if text.startswith("DCP_BROWSER_TRANSITION_TIMEOUT"):
        return "DCP_TRANSITION_TIMEOUT"
    if text in SAFE_CODES:
        return text
    return default
