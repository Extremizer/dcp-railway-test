"""WOPS public Retail, using an existing browser tab without UI/cart actions.

WOPS here means World of Powersports / DealerOrders, not Western Power Sports.
Only AC/Honda prefixes and supplier identities have live read-only evidence.
Other brands are deliberately unsupported until their mapping is verified.
"""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
import re
from urllib.parse import urlsplit

SOURCE = "dealerorders_public_retail"
PREFIXES = {"Arctic Cat": "AC", "Honda": "HO"}
DETAIL_URL = "https://www.dealerorders.com/cgi-bin/dealer/detail.html?mv_arg="


def identity(manufacturer, oem):
    manufacturer = str(manufacturer or "").strip()
    oem = str(oem or "").strip().upper()
    if manufacturer not in PREFIXES or not re.fullmatch(r"[A-Z0-9-]{4,40}", oem):
        return None
    return manufacturer, oem, PREFIXES[manufacturer] + oem


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def parse_retail(html, manufacturer, oem):
    """Require an exact labeled manufacturer/OEM and exactly one USD Retail."""
    key = identity(manufacturer, oem)
    if not key or not isinstance(html, str) or len(html) > 131072:
        return None
    manufacturer, oem, sku = key
    parser = _Text()
    parser.feed(html)
    text = " ".join(" ".join(parser.parts).split())
    if any(x in text.lower() for x in ("just a moment", "cloudflare", "verify you are human",
                                       "один момент", "sign in to continue")):
        return None
    supplier = re.findall(r"Supplier\s*:\s*(.*?)\s*Part\s+Number\s*:", text, re.I)
    parts = re.findall(r"Part\s+(?:no\b|Number\s*:)\s*([A-Z0-9-]+)", text, re.I)
    retail = re.findall(r"\bRetail\s*:\s*\$\s*([0-9,]+\.[0-9]{2})(?=\s|$)", text, re.I)
    norm = lambda value: re.sub(r"[^a-z0-9]", "", value.lower())
    if (len(supplier) != 1 or norm(supplier[0]) != norm(manufacturer)
            or not parts or any(p.upper() != oem for p in parts) or len(retail) != 1):
        return None
    try:
        value = float(retail[0].replace(",", ""))
    except ValueError:
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    return {"manufacturer": manufacturer, "oem": oem, "supplier_sku": sku,
            "retail_usd": value, "currency": "USD", "source": SOURCE,
            "source_url": DETAIL_URL + sku,
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def get_public_retail(manufacturer, oem):
    """GET the observed detail route; never create, focus or navigate any tab."""
    key = identity(manufacturer, oem)
    if not key:
        return None
    import oemixibot_dcp_price as browser

    url = DETAIL_URL + key[2]
    expression = """(async()=>{
      const controller=new AbortController();
      const timer=setTimeout(()=>controller.abort(),2500);
      try {
        const r=await fetch(URL_VALUE,{method:'GET',credentials:'same-origin',
          redirect:'error',signal:controller.signal});
        if(!r.ok)return null;
        const html=await r.text();
        return html.length<=131072?html:null;
      } catch(e) {return null;} finally {clearTimeout(timer);}
    })()""".replace("URL_VALUE", json.dumps(url))
    try:
        tabs = [t for t in browser._tabs(timeout_seconds=2) if t.get("type") == "page"
                and urlsplit(str(t.get("url") or "")).hostname == "www.dealerorders.com"]
        if not tabs:
            return None
        html = browser._runtime_evaluate(tabs[0], expression, timeout_seconds=4)
    except Exception:
        # WOPS failures must not change DCP health or a successful dealer price.
        return None
    return parse_retail(html, key[0], key[1])
