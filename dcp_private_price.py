#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only DCP dealer-price lookup through an already authorized Chrome session."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, asdict
from typing import Any

import websocket

import dealercostparts_manufacturer_finder_v6_6 as finder

CDP_HTTP = "http://127.0.0.1:9222"
DCP_HOST = "www.dealercostparts.com"

@dataclass(frozen=True)
class DealerPriceResult:
    status: str
    manufacturer: str
    query_oem: str
    matched_sku: str | None = None
    current_oem: str | None = None
    name: str | None = None
    dealer_price_usd: float | None = None
    source: str | None = None
    http_status: int | None = None
    authorized: bool | None = None
    message: str | None = None

def _parts_slug(manufacturer: str) -> tuple[str, str]:
    canonical = finder.manufacturer_alias(manufacturer)
    if not canonical:
        raise ValueError(f"Unknown manufacturer: {manufacturer}")
    for catalog in finder.CATALOGS:
        if catalog.manufacturer == canonical and catalog.kind == "parts":
            return canonical, catalog.slug
    raise ValueError(f"No OEM Parts catalog configured for {canonical}")

def _normalize_oem(oem: str) -> str:
    return finder.normalize_oem(oem)

def _tabs() -> list[dict[str, Any]]:
    with urllib.request.urlopen(CDP_HTTP + "/json", timeout=5) as response:
        return json.load(response)

def _pick_authorized_dcp_tab() -> dict[str, Any]:
    pages = [
        x for x in _tabs()
        if x.get("type") == "page" and DCP_HOST in str(x.get("url") or "")
    ]
    usable = [
        x for x in pages
        if "challenges.cloudflare.com" not in str(x.get("url") or "")
        and "один момент" not in str(x.get("title") or "").lower()
        and "just a moment" not in str(x.get("title") or "").lower()
    ]
    if not usable:
        raise RuntimeError("No usable DCP tab found in Chrome.")
    usable.sort(key=lambda x: ("partsearch" not in str(x.get("url") or ""),))
    return usable[0]

def _runtime_evaluate(tab: dict[str, Any], expression: str) -> Any:
    ws = websocket.create_connection(
        str(tab["webSocketDebuggerUrl"]),
        timeout=20,
        suppress_origin=True,
    )
    try:
        ws.send(json.dumps({
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": expression,
                "awaitPromise": True,
                "returnByValue": True,
            },
        }))
        while True:
            message = json.loads(ws.recv())
            if message.get("id") != 1:
                continue
            result = message.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise RuntimeError(result.get("description") or "Runtime.evaluate failed")
            if "value" not in result:
                raise RuntimeError("Runtime.evaluate returned no value")
            return result["value"]
    finally:
        ws.close()

def _money(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"-?\d[\d,]*(?:\.\d{1,2})?", value.replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace(",", ""))
    except ValueError:
        return None

def get_dealer_price(manufacturer: str, oem: str) -> DealerPriceResult:
    """Return the dealer price from the user's authorized DCP Chrome session."""
    canonical, slug = _parts_slug(manufacturer)
    normalized_oem = _normalize_oem(oem)

    # First prefer an already-open exact DCP page. This avoids an extra request
    # and keeps working when Cloudflare blocks background fetches but the user's
    # authorized browser page is already loaded.
    exact_url_marker = f"/oemparts/partsearch/{slug}?partsearch={normalized_oem}"
    for candidate in _tabs():
        candidate_url = str(candidate.get("url") or "")
        if candidate.get("type") != "page" or exact_url_marker not in candidate_url:
            continue
        live_expression = f"""(()=>{{
          const body=(document.body?.innerText||'');
          const lower=body.toLowerCase();
          const challenged=['just a moment','verify you are human',
            'checking your browser','performing security verification',
            'подтвердите, что вы человек','выполнение проверки безопасности',
            'enable javascript and cookies to continue','cloudflare']
            .some(x=>lower.includes(x));
          const authorized=/sign out/i.test(body);
          const form=document.querySelector('#add_{normalized_oem}');
          if(!form) return {{http_status:200,authorized,challenged,found:false}};
          const current=form.querySelector('.itemnumnew')?.textContent.trim()
            ||form.querySelector('.itemnum')?.textContent.trim()
            ||form.querySelector('input[name="sku"]')?.value||null;
          return {{
            http_status:200,authorized,challenged,found:true,
            matched_sku:form.querySelector('input[name="sku"]')?.value||null,
            current_oem:current,
            name:form.querySelector('.c1a')?.textContent.trim()||null,
            dealer_price_text:form.querySelector('.c2 .dbl')?.textContent.trim()||null
          }};
        }})()"""
        live_data = _runtime_evaluate(candidate, live_expression)
        if live_data.get("challenged"):
            return DealerPriceResult(
                "CLOUDFLARE", canonical, normalized_oem,
                http_status=live_data.get("http_status"),
                authorized=live_data.get("authorized"),
                message="Open DCP page is waiting for human verification.",
            )
        if live_data.get("authorized") and live_data.get("found"):
            live_price = _money(live_data.get("dealer_price_text"))
            if live_price is not None:
                return DealerPriceResult(
                    "FOUND", canonical, normalized_oem,
                    matched_sku=live_data.get("matched_sku"),
                    current_oem=live_data.get("current_oem"),
                    name=live_data.get("name"),
                    dealer_price_usd=live_price,
                    source=f"live_dom:#add_{normalized_oem} .c2 .dbl",
                    http_status=200,
                    authorized=True,
                )

    tab = _pick_authorized_dcp_tab()
    url = f"/oemparts/partsearch/{slug}?partsearch={normalized_oem}"
    expression = f"""(async()=>{{
      const response=await fetch({json.dumps(url)}, {{
        credentials:'include',
        cache:'no-store',
        method:'GET'
      }});
      const html=await response.text();
      const doc=new DOMParser().parseFromString(html,'text/html');
      const body=(doc.body?.innerText||'');
      const lower=body.toLowerCase();
      const challenged=response.status===403 || ['just a moment','verify you are human',
        'checking your browser','performing security verification',
        'подтвердите, что вы человек','выполнение проверки безопасности',
        'enable javascript and cookies to continue','cloudflare']
        .some(x=>lower.includes(x));
      const authorized=/sign out/i.test(body);
      const forms=[...doc.querySelectorAll('form[id^="add_"]')];
      const form=forms.find(f=>f.querySelector('input[name="sku"]')?.value==={json.dumps(normalized_oem)})||null;
      if(!form) return {{http_status:response.status,authorized,challenged,found:false}};
      const current=form.querySelector('.itemnumnew')?.textContent.trim()
        ||form.querySelector('.itemnum')?.textContent.trim()
        ||form.querySelector('input[name="sku"]')?.value||null;
      return {{
        http_status:response.status,
        authorized,
        challenged,
        found:true,
        matched_sku:form.querySelector('input[name="sku"]')?.value||null,
        current_oem:current,
        name:form.querySelector('.c1a')?.textContent.trim()||null,
        dealer_price_text:form.querySelector('.c2 .dbl')?.textContent.trim()||null
      }};
    }})()"""
    data = _runtime_evaluate(tab, expression)

    if data.get("challenged"):
        return DealerPriceResult(
            "CLOUDFLARE", canonical, normalized_oem,
            http_status=data.get("http_status"),
            authorized=data.get("authorized"),
            message="DCP returned a human-verification page.",
        )
    if not data.get("authorized"):
        return DealerPriceResult(
            "AUTH_REQUIRED", canonical, normalized_oem,
            http_status=data.get("http_status"),
            authorized=False,
            message="Authorized DCP session was not detected.",
        )
    if not data.get("found"):
        return DealerPriceResult(
            "NOT_FOUND", canonical, normalized_oem,
            http_status=data.get("http_status"),
            authorized=True,
            message="Exact OEM row was not found in DCP Parts search.",
        )

    price = _money(data.get("dealer_price_text"))
    if price is None:
        return DealerPriceResult(
            "NO_PRICE", canonical, normalized_oem,
            matched_sku=data.get("matched_sku"),
            current_oem=data.get("current_oem"),
            name=data.get("name"),
            http_status=data.get("http_status"),
            authorized=True,
            source=f"#add_{normalized_oem} .c2 .dbl",
            message="Exact OEM row exists, but no dealer price was parsed.",
        )

    return DealerPriceResult(
        "FOUND", canonical, normalized_oem,
        matched_sku=data.get("matched_sku"),
        current_oem=data.get("current_oem"),
        name=data.get("name"),
        dealer_price_usd=price,
        source=f"server_html:#add_{normalized_oem} .c2 .dbl",
        http_status=data.get("http_status"),
        authorized=True,
    )

def get_dealer_price_dict(manufacturer: str, oem: str) -> dict[str, Any]:
    return asdict(get_dealer_price(manufacturer, oem))

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="Read-only DCP dealer-price lookup through authorized Chrome."
    )
    parser.add_argument("manufacturer")
    parser.add_argument("oem")
    args = parser.parse_args()
    print(json.dumps(
        get_dealer_price_dict(args.manufacturer, args.oem),
        ensure_ascii=False,
        indent=2,
    ))
