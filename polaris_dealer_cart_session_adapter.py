"""Layer C: read-only snapshot adapter for an EXISTING trusted DCP session.

The injected snapshot reader is owned by the existing preflight/session boundary.
This module performs no HTTP, CDP, Playwright, navigation, or mutation.
"""
from __future__ import annotations
from urllib.parse import urlsplit
from polaris_dealer_cart_dom import extract_and_validate

ORIGIN = "https://www.dealercostparts.com"
REJECT = "DEALER_PRICE_UNVERIFIED"

def read_existing_cart(snapshot_reader, *, requested_oem, category_evidence,
                       session_evidence, observed_at, source_provenance, now=None):
    def stop(reason):
        return {"status": REJECT, "reason": reason, "dealer_price_usd": None}
    if not callable(snapshot_reader):
        return stop("no_trusted_snapshot_reader")
    if not isinstance(session_evidence, dict):
        return stop("session_evidence_missing")
    if (session_evidence.get("trusted_preflight") is not True
        or session_evidence.get("dealer_authenticated") is not True):
        return stop("AUTH_REQUIRED")
    if session_evidence.get("cloudflare") is True or session_evidence.get("challenged") is True:
        return stop("CLOUDFLARE")
    if (not isinstance(category_evidence, dict)
        or category_evidence.get("manufacturer") != "Polaris"
        or category_evidence.get("oem") != requested_oem
        or category_evidence.get("item_category") != "Parts"
        or not all(category_evidence.get(k) for k in
            ("category_source_kind","category_source_ref","category_verified_at"))):
        return stop("CATEGORY_UNVERIFIED")
    try:
        # Exactly one passive snapshot from a separately authorized read-only interface.
        doc = snapshot_reader()
    except Exception:
        return stop("document_read_failed")
    if not isinstance(doc, dict):
        return stop("no_dcp_page")
    if doc.get("cloudflare") is True or doc.get("challenged") is True:
        return stop("CLOUDFLARE")
    if doc.get("auth_required") is True:
        return stop("AUTH_REQUIRED")
    if doc.get("context_changed") is True:
        return stop("dom_context_changed")
    before, after = doc.get("url_before"), doc.get("url_after")
    if not isinstance(before,str) or before != after:
        return stop("dom_context_changed")
    parsed = urlsplit(before)
    if (parsed.scheme != "https" or parsed.netloc != "www.dealercostparts.com"
        or not parsed.path.rstrip("/").lower() == "/cart"
        or parsed.query or parsed.fragment):
        return stop("wrong_cart_origin_or_path")
    if doc.get("trusted_dealer_session") is not True:
        return stop("AUTH_REQUIRED")
    if not isinstance(doc.get("html"),str) or not doc["html"]:
        return stop("cart_document_missing")
    result = extract_and_validate(
        doc["html"], requested_oem=requested_oem,
        dealer_authenticated=True, category_server_verified=True,
        observed_at=observed_at,source_provenance=source_provenance,now=now)
    if result.get("status") != "VALIDATED_DEALER_CART_CANDIDATE":
        return stop("cart_contract_rejected:"+str(result.get("reason") or "unknown"))
    # Never export a production dealer price field.
    return result
