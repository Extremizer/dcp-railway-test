"""DCP dealer SEARCH after an authenticated SIGN OUT session, Parts only.

A publicly visible Parts Price is MSRP if SIGN IN; once SIGN OUT is confirmed
the same field is dealer price (owner-verified DCP account contract).
An old/struck-through SKU is NOT a competing item when its .itemnumnew
shows the requested current OEM. Multiple such rows must agree on price.
Polaris must never call this module; its verified cart route is separate.
"""
from __future__ import annotations

import json
from urllib.parse import parse_qs, urlsplit

def extract_parts_rows(manufacturer, query_oem, data, *, origin):
    """Pure fail-closed normalizer for actual Parts SEARCH result rows."""
    from oemixibot_dcp_price import DealerPriceResult, _money, _normalize_oem
    query = _normalize_oem(query_oem).upper()
    def error(status, detail=""):
        return DealerPriceResult(status, manufacturer, query, message=detail or status)
    if manufacturer == "Polaris" or origin not in {"live_dom", "server_html"}:
        return error("TECHNICAL_ERROR", "search_not_authorized_for_manufacturer")
    if not isinstance(data, dict):
        return error("TECHNICAL_ERROR", "invalid_search_document")
    if data.get("context_changed") or data.get("transport_error"):
        return error("TECHNICAL_ERROR", "document_context_changed")
    if data.get("challenged"):
        return error("CLOUDFLARE")
    if not data.get("authorized"):
        return error("AUTH_REQUIRED")
    if data.get("http_status") != 200:
        return error("TECHNICAL_ERROR", "unexpected_http_status")
    rows=data.get("rows")
    if not isinstance(rows, list) or len(rows)>100:
        return error("TECHNICAL_ERROR", "invalid_parts_rows")
    current=[]
    previous=[]
    for r in rows:
        if not isinstance(r,dict): return error("TECHNICAL_ERROR")
        try:
            form_sku=_normalize_oem(str(r.get("form_sku") or "")).upper()
            current_oem=_normalize_oem(str(r.get("current_oem") or "")).upper()
        except (TypeError,ValueError,AttributeError):
            continue
        if not r.get("price_selector_exact"):
            if current_oem==query or form_sku==query:
                return error("NO_PRICE", "dcp_price_cell_missing")
            continue
        price=_money(r.get("price_text"))
        if current_oem==query:
            current.append((form_sku,current_oem,price,r.get("name")))
        elif form_sku==query:
            previous.append((form_sku,current_oem,price,r.get("name")))
    matches=current if current else previous
    if not matches:
        return error("NOT_FOUND", "exact_requested_or_verified_replacement_row_not_found")
    # Identical replacement price across multiple old-SKU rows is ONE current OEM.
    if any(p is None for _,_,p,_ in matches):
        return error("NO_PRICE")
    if len({(new,round(p,2)) for _,new,p,_ in matches})!=1:
        return error("TECHNICAL_ERROR", "conflicting_supersession_or_price")
    replacement=matches[0][1]
    mode="current_oem" if current else "previous_oem"
    if not current and replacement==query:
        return error("TECHNICAL_ERROR", "invalid_previous_oem_claim")
    return DealerPriceResult(
        status="FOUND",manufacturer=manufacturer,query_oem=query,
        matched_sku=query,current_oem=replacement,
        name=str(matches[0][3] or "")[:300] or None,
        dealer_price_usd=float(matches[0][2]),
        source=f"{origin}:{mode}_{query}",
        http_status=200,authorized=True,
        message=("matched_current_with_superseded_skus" if current and any(old!=query for old,_,_,_ in current)
                 else ("matched_previous_oem" if previous and not current else "matched_exact_oem"))
    )


def parts_dom_expression(slug, query_oem, *, live):
    """One passive DOM read or a single GET; no navigation/cart/search retry."""
    target=json.dumps(query_oem.upper())
    path=f"/oemparts/partsearch/{slug}"
    endpoint=json.dumps(path+"?partsearch="+query_oem.upper())
    before="""const part=(document.body?.innerText||document.body?.textContent||'').toLowerCase();
      const cf=['just a moment','verify you are human','checking your browser',
        'performing security verification','enable javascript and cookies to continue',
        'подтвердите, что вы человек'].some(x=>part.includes(x));
      if(location.origin!=='https://www.dealercostparts.com')
        return {context_changed:true};
      if(cf)return {challenged:true};
      if(!/sign out/i.test(part))return {authorized:false};"""
    parse="""const text=doc.body?.textContent||'';
      const lower=text.toLowerCase();
      if(['just a moment','verify you are human','checking your browser',
          'performing security verification','enable javascript and cookies to continue'].some(x=>lower.includes(x)))
          return {challenged:true,http_status:status};
      if(!/sign out/i.test(text))return {authorized:false,http_status:status};
      const forms=[...doc.querySelectorAll('form[id^="add_"]')];
      const rows=forms.map(f=>{
        const sku=f.querySelector('input[name="sku"]')?.value||'';
        const current=f.querySelector('.itemnumnew')?.textContent?.trim()
          ||f.querySelector('.itemnum')?.textContent?.trim()||sku;
        const price=f.querySelector('.c2 .dbl');
        return {form_sku:sku,current_oem:current,name:f.querySelector('.c1a')?.textContent?.trim()||null,
          price_selector_exact:!!price,price_text:price?.textContent?.trim()||null};
      });
      return {http_status:status,authorized:true,challenged:false,rows};"""
    if live:
        body="const doc=document; const status=200;\n"+parse
        return "(()=>{"+before+"if(location.pathname!=="+json.dumps(path)+
               "||new URL(location.href).searchParams.get('partsearch')!=="+target+
               ")return {context_changed:true};"+body+"})()"
    body=("const response=await fetch("+endpoint+
          ",{method:'GET',credentials:'include',cache:'no-store',redirect:'manual'});"
          "const status=response.status;if(status===403)return {challenged:true,http_status:403};"
          "if(status!==200)return {transport_error:true,http_status:status};"
          "const doc=new DOMParser().parseFromString(await response.text(),'text/html');"
          +parse)
    return "(async()=>{"+before+body+"})()"
