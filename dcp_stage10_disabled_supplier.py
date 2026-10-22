"""Stage 10 fixture-only supplier identity boundary. Never opens Chromium.

Matches PR64 logout and single account identity semantics. Does NOT authorize
Dealer FOUND, MSRP interpretation, network access or a Polaris cart path.
"""
import hmac
from urllib.parse import urljoin, urlsplit

class AdapterDisabled(RuntimeError):
    pass

def fixture_identity(document, expected_email, *, enabled=False,
                     origin="https://www.dealercostparts.com"):
    if enabled:
        raise AdapterDisabled("live_supplier_transport_not_implemented")
    if not isinstance(document,str) or not isinstance(expected_email,str) or not expected_email.strip():
        return "AUTH_REQUIRED"
    from bs4 import BeautifulSoup
    doc=BeautifulSoup(document,"html.parser")
    if any(marker in doc.get_text(" ",strip=True).casefold() for marker in
           ("cloudflare","verify you are human","just a moment")):
        return "CLOUDFLARE"
    root=doc.select_one("#persistant-nav")
    if root is None:
        return "AUTH_REQUIRED"
    actual=root.select(".displayname")
    if len(actual)!=1:
        return "AUTH_REQUIRED"
    account=actual[0].get_text(strip=True)
    if not hmac.compare_digest(account.casefold().encode(),expected_email.strip().casefold().encode()):
        return "AUTH_REQUIRED"
    logout=False
    for anchor in root.select("a[href]"):
        href=anchor.get("href")
        if not href:continue
        target=urlsplit(urljoin(origin,href))
        reference=urlsplit(origin)
        if (target.scheme,target.netloc,target.path,target.query,target.fragment)==(
            reference.scheme,reference.netloc,"/account/logoff","",""):
            logout=True
    if not logout:
        return "AUTH_REQUIRED"
    return "FIXTURE_AUTHENTICATED_NOT_PRICE_VERIFIED"

def fixture_price(*args, **kwargs):
    return {"status":"TECHNICAL_ERROR","error_code":"SUPPLIER_DISABLED"}

def prohibited_live(*args, **kwargs):
    raise AdapterDisabled("no_live_chromium_or_supplier_requests")
