#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only DCP dealer-price lookup through an already authorized Chrome session."""

from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any

import websocket

import dealercostparts_manufacturer_finder_v6_6 as finder
import dp_live_health

CDP_HTTP = "http://127.0.0.1:9222"
DCP_HOST = "www.dealercostparts.com"
LOCAL_HEALTH_FILE = Path(__file__).with_name("dp_sync_agent_status.json")

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

    health = dp_live_health.read_local_health(LOCAL_HEALTH_FILE)
    if not health.get("live_allowed"):
        status = str(
            health.get("effective_status")
            or health.get("status")
            or "TECHNICAL_ERROR"
        ).upper()
        return DealerPriceResult(
            status,
            canonical,
            normalized_oem,
            authorized=(False if status == "AUTH_REQUIRED" else None),
            message="DCP health guard blocked live lookup: "
            + str(health.get("detail") or status),
        )

    # First prefer an already-open exact DCP page. This avoids an extra request
    # and keeps working when Cloudflare blocks background fetches but the user's
    # authorized browser page is already loaded.
    exact_query_marker = f"partsearch={normalized_oem}"
    expected_parts_path = f"/oemparts/partsearch/{slug}"
    for candidate in _tabs():
        candidate_url = str(candidate.get("url") or "")
        if (
            candidate.get("type") != "page"
            or DCP_HOST not in candidate_url
            or expected_parts_path not in candidate_url
            or exact_query_marker not in candidate_url
        ):
            continue
        live_expression = f"""(()=>{{
          const body=(document.body?.innerText||'');
          const lower=body.toLowerCase();
          const challenged=['just a moment','verify you are human',
            'checking your browser','performing security verification',
            'подтвердите, что вы человек','выполнение проверки безопасности',
            'enable javascript and cookies to continue']
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
            dealer_price_text:form.querySelector('.c2 .dbl')?.textContent.trim()
              ||form.querySelector('.dbl')?.textContent.trim()||null
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
        'enable javascript and cookies to continue']
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
        # Background fetch may be challenged although visible Chrome already
        # passed CF. Retry as normal top-level navigation in the SAME session.
        nav_url = f"https://{DCP_HOST}{url}"
        _runtime_evaluate(tab, "location.href=" + json.dumps(nav_url) + "; true")
        import time
        deadline = time.time() + 15
        while time.time() < deadline:
            time.sleep(0.75)
            try:
                current = _pick_authorized_dcp_tab()
                probe = _runtime_evaluate(current, f"""(()=>{{
                  const body=(document.body?.innerText||'');
                  const lower=body.toLowerCase();
                  const challenged=['just a moment','verify you are human',
                    'checking your browser','performing security verification',
                    'enable javascript and cookies to continue'].some(x=>lower.includes(x));
                  const authorized=/sign out/i.test(body);
                  const form=document.querySelector('#add_{normalized_oem}');
                  if(!form) return {{authorized,challenged,found:false}};
                  return {{authorized,challenged,found:true,
                    matched_sku:form.querySelector('input[name="sku"]')?.value||null,
                    current_oem:form.querySelector('.itemnumnew')?.textContent.trim()
                      ||form.querySelector('.itemnum')?.textContent.trim()||null,
                    name:form.querySelector('.c1a')?.textContent.trim()||null,
                    dealer_price_text:form.querySelector('.c2 .dbl')?.textContent.trim()
                      ||form.querySelector('.dbl')?.textContent.trim()||null}};
                }})()""")
                if probe.get("authorized") and probe.get("found") and not probe.get("challenged"):
                    price = _money(probe.get("dealer_price_text"))
                    if price is not None:
                        return DealerPriceResult(
                            "FOUND", canonical, normalized_oem,
                            matched_sku=probe.get("matched_sku"),
                            current_oem=probe.get("current_oem"),
                            name=probe.get("name"), dealer_price_usd=price,
                            source=f"live_dom_after_navigation:#add_{normalized_oem} .c2 .dbl",
                            http_status=200, authorized=True,
                        )
            except Exception:
                continue
        return DealerPriceResult(
            "CLOUDFLARE", canonical, normalized_oem,
            http_status=data.get("http_status"),
            authorized=data.get("authorized"),
            message="DCP top-level navigation is still waiting for human verification.",
        )
    if not data.get("authorized"):
        return DealerPriceResult(
            "AUTH_REQUIRED", canonical, normalized_oem,
            http_status=data.get("http_status"),
            authorized=False,
            message="Authorized DCP session was not detected.",
        )
    # Verified two-step Ski-Doo Accessories fallback.
    if not data.get("found") and canonical == "Ski-Doo":
        accessories_expression = f"""(async()=>{{
          const norm=v=>String(v||'').replace(/[^A-Za-z0-9]/g,'').toUpperCase();
          const target=norm({json.dumps(normalized_oem)});
          const cf=t=>['just a moment','verify you are human',
            'checking your browser','performing security verification',
            'enable javascript and cookies to continue']
            .some(x=>String(t||'').toLowerCase().includes(x));

          const sr=await fetch('/oemcatalogs/productsearch/ski_doo_accessories',{{
            credentials:'include',
            cache:'no-store',
            method:'POST',
            headers:{{'Content-Type':'application/x-www-form-urlencoded;charset=UTF-8'}},
            body:new URLSearchParams({{partsearch:{json.dumps(normalized_oem)}}}).toString()
          }});

          const sh=await sr.text();
          const sd=new DOMParser().parseFromString(sh,'text/html');
          const sb=sd.body?.innerText||'';
          const sa=/sign out/i.test(sb);

          if(sr.status===403||cf(sb))
            return {{http_status:sr.status,authorized:sa,challenged:true,found:false}};

          const links=[...new Set(
            [...sd.querySelectorAll('#CategoryItems a[href*="/oemcatalogs/p/"]')]
              .map(a=>a.getAttribute('href')).filter(Boolean)
          )];

          if(links.length!==1)
            return {{http_status:sr.status,authorized:sa,challenged:false,
                     found:false,candidate_count:links.length}};

          const pr=await fetch(links[0],{{
            credentials:'include',
            cache:'no-store'
          }});

          const ph=await pr.text();
          const pd=new DOMParser().parseFromString(ph,'text/html');
          const pb=pd.body?.innerText||'';
          const pa=/sign out/i.test(pb);

          if(pr.status===403||cf(pb))
            return {{http_status:pr.status,authorized:pa,challenged:true,found:false}};

          const nodes=[...pd.querySelectorAll(
            '.productitem,form[action*="/cart/addoemtocart"]'
          )];

          const exact=nodes.find(n=>[
            n.getAttribute('data-sku'),
            n.querySelector('input[name="item"]')?.value,
            n.querySelector('[itemprop="sku"]')?.textContent,
            n.querySelector('meta[itemprop="identifier"]')?.content
          ].some(v=>norm(v)===target))||null;

          if(!exact)
            return {{http_status:pr.status,authorized:pa,challenged:false,found:false}};

          const sku=exact.getAttribute('data-sku')
            ||exact.querySelector('input[name="item"]')?.value
            ||exact.querySelector('[itemprop="sku"]')?.textContent?.trim()
            ||exact.querySelector('meta[itemprop="identifier"]')?.content
            ||null;

          const price=exact.querySelector('p.price,.price')?.textContent?.trim()
            ||exact.querySelector('meta[itemprop="price"]')?.content
            ||null;

          const name=exact.querySelector('.name')?.textContent?.trim()
            ||pd.querySelector('h1')?.textContent?.trim()
            ||null;

          return {{
            http_status:pr.status,
            authorized:pa,
            challenged:false,
            found:true,
            matched_sku:sku,
            current_oem:sku,
            name:name,
            dealer_price_text:price
          }};
        }})()"""

        accessories_data = _runtime_evaluate(tab, accessories_expression)

        if accessories_data.get("challenged"):
            return DealerPriceResult(
                "CLOUDFLARE", canonical, normalized_oem,
                http_status=accessories_data.get("http_status"),
                authorized=accessories_data.get("authorized"),
                message="DCP Accessories search is waiting for human verification.",
            )

        if accessories_data.get("authorized") and accessories_data.get("found"):
            accessories_price = _money(accessories_data.get("dealer_price_text"))

            if accessories_price is None:
                return DealerPriceResult(
                    "NO_PRICE", canonical, normalized_oem,
                    matched_sku=accessories_data.get("matched_sku"),
                    current_oem=accessories_data.get("current_oem"),
                    name=accessories_data.get("name"),
                    http_status=accessories_data.get("http_status"),
                    authorized=True,
                    source="catalog_product:exact_sku",
                )

            return DealerPriceResult(
                "FOUND", canonical, normalized_oem,
                matched_sku=accessories_data.get("matched_sku"),
                current_oem=accessories_data.get("current_oem"),
                name=accessories_data.get("name"),
                dealer_price_usd=accessories_price,
                source="catalog_product:exact_sku_price",
                http_status=accessories_data.get("http_status"),
                authorized=True,
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
