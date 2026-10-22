"""Stage 10 disabled supplier adapter boundary. Fixture DOM only; no network.

No session ownership, browser bootstrap or vendor transport is present.
This adapter cannot produce a dealer FOUND. Theme 4 must separately approve
real supplier semantics and an authorized browser before any live connection.
"""
from html.parser import HTMLParser

class AdapterDisabled(RuntimeError):
    pass

class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text=[]
    def handle_data(self,data):
        self.text.append(data)

def fixture_identity(document, expected_email, *, enabled=False):
    if enabled:
        raise AdapterDisabled("live_supplier_transport_not_implemented")
    if not isinstance(document,str) or not isinstance(expected_email,str) or not expected_email:
        return "AUTH_REQUIRED"
    parser=_Text()
    parser.feed(document)
    text=" ".join(parser.text)
    if "cloudflare" in text.lower() or "verify you are human" in text.lower():
        return "CLOUDFLARE"
    if "SIGN OUT" not in text.upper():
        return "AUTH_REQUIRED"
    # Exact identity text; not a substring match against a different email.
    import re
    found=set(re.findall(r"[A-Za-z0-9_.+%-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",text))
    if expected_email not in found:
        return "AUTH_REQUIRED"
    return "FIXTURE_AUTHENTICATED_NOT_PRICE_VERIFIED"

def fixture_price(*args, **kwargs):
    # A fixture identity never proves live price/source semantics.
    return {"status":"TECHNICAL_ERROR","error_code":"SUPPLIER_DISABLED"}

def prohibited_live(*args, **kwargs):
    raise AdapterDisabled("no_live_chromium_or_supplier_requests")
