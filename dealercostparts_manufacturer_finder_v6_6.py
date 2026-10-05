#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
dealercostparts_manufacturer_finder_v6_6.py — DIRECT PARTS SEARCH DIAGNOSTIC ONLY

Keeps v2 exact-match rules, Aftermarket Item/SKU + Dist # matching,
HUMAN_REVIEW for an exact Parts row without a public price, and no QOH.

v6.6 architecture:
  Built directly from frozen V6.5. Parts search navigation bypass only; V6.5 remains unchanged.
  Direct Parts GET uses the existing Chrome context; exact Parts semantics remain V5.1.
  Any unusable/blocked direct response falls back to the frozen V6.5 browser Parts flow.

v6.5 architecture:
  Built directly from frozen V6.4. Direct product verification only; V6.4 remains unchanged.
  Direct product GET uses the existing Chrome context; any unusable or unconfirmed response falls back to V6.4 browser verification.

v6.4 architecture:
  Built directly from frozen V6.2. Parser rules and catalog order are unchanged.
  Catalog-type searches first POST partsearch directly to the existing productsearch endpoint,
  bypassing /oemcatalogs/catalog/<slug>. FOUND is still confirmed only by the frozen V6.2
  inspect_product parser. If the direct request is blocked/ambiguous, V6.2 submit_catalog is used unchanged.

v6 architecture:
  Speed-only branch based on frozen V5.1 parser semantics.
  Removes networkidle waits, blocks image/media/font downloads, and records per-step timings.
  No parser rule, HUMAN_REVIEW rule, Previous OEM, Item/Dist #, or QOH behavior is changed.

v5 architecture:
  The customer explicitly selects the manufacturer first.
  Search is restricted to that manufacturer's catalogs only.
  There is NO cross-manufacturer fallback.
  Parsers and result semantics remain unchanged:
  FOUND / HUMAN_REVIEW / exact Item-SKU / Dist # / no QOH.

No production files are imported or modified.
"""
import argparse, asyncio, json, re, sys, time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import quote
from playwright.async_api import async_playwright, Page

BASE="https://www.dealercostparts.com"
CDP_URL="http://127.0.0.1:9222"
CONTROL_OEMS=["417224332","422280652","0627-084","3221115","18-95074","0627-033"]
HUMAN_REVIEW_MESSAGE="По запрашиваемому номеру нет актуальной информации о цене. Нужна помощь человека."
CF_MARKERS=("just a moment","verify you are human","checking your browser",
            "performing security verification","подтвердите, что вы человек")

@dataclass(frozen=True)
class Catalog:
    manufacturer:str; label:str; kind:str; slug:str
    @property
    def landing_url(self): return f"{BASE}/oemcatalogs/catalog/{self.slug}"
    @property
    def search_url(self):
        return (f"{BASE}/oemparts/partsearch/{self.slug}" if self.kind=="parts"
                else f"{BASE}/oemcatalogs/productsearch/{self.slug}")

ROWS=[
("Arctic Cat","Parts","parts","arctic_cat"),("Can-Am","Parts","parts","can_am"),
("CFMoto","Parts","parts","cfmoto"),("Honda","Parts","parts","honda"),
("Indian","Parts","parts","indian_motorcycle"),("Kawasaki","Parts","parts","kawasaki"),
("KTM","Parts","parts","ktm"),("Polaris","Parts","parts","polaris"),
("Sea-Doo","Parts","parts","sea_doo"),("Ski-Doo","Parts","parts","ski_doo"),
("Suzuki","Parts","parts","suzuki"),("Yamaha","Parts","parts","yamaha"),
("Arctic Cat","Accessories","catalog","arctic_cat_snowmobile_accessories"),
("Can-Am","Accessories / ATV","catalog","can_am_atv_accessories"),
("Can-Am","Accessories / SxS","catalog","can_am_sxs_accessories"),
("CFMoto","Accessories","catalog","cfmoto_off_road_accessories"),
("Honda","Accessories","catalog","honda_accessories"),("Indian","Accessories","catalog","indian_accessories"),
("Kawasaki","Accessories","catalog","kawasaki_accessories"),
("Polaris","Accessories / ATV","catalog","polaris_atv_accessories"),
("Polaris","Accessories / SxS","catalog","polaris_sxs_accessories"),
("Polaris","Accessories / Snowmobile","catalog","polaris_snowmobile_accessories"),
("Polaris","Accessories / Slingshot","catalog","polaris_slingshot_accessories"),
("Sea-Doo","Accessories","catalog","sea_doo_accessories"),("Ski-Doo","Accessories","catalog","ski_doo_accessories"),
("Suzuki","Accessories","catalog","suzuki_accessories"),("Yamaha","Accessories","catalog","yamaha_accessories"),
("Can-Am","Oil / Shop & Maintenance","catalog","can_am_shop_maintenance"),
("CFMoto","Oil / Shop & Maintenance","catalog","cfmoto_oil_lubricants"),
("Indian","Oil / Shop & Maintenance","catalog","indian_shop_maintenance"),
("Kawasaki","Oil / Shop & Maintenance","catalog","kawasaki_shop_maintenance"),
("KTM","Oil / Shop & Maintenance","catalog","ktm_shop_maintenance"),
("Polaris","Oil / Shop & Maintenance","catalog","polaris_shop_maintenance"),
("Sea-Doo","Oil / Shop & Maintenance","catalog","sea_doo_shop_maintenance"),
("Ski-Doo","Oil / Shop & Maintenance","catalog","ski_doo_shop_maintenance"),
("Suzuki","Oil / Shop & Maintenance","catalog","suzuki_shop_maintenance"),
("Yamaha","Oil / Shop & Maintenance","catalog","yamaha_shop_maintenance"),
("Arctic Cat","Apparel & Gear","catalog","arctic_cat_snowmobile_apparel_gear"),
("Can-Am","Apparel & Gear","catalog","can_am_riding_gear"),("CFMoto","Apparel & Gear","catalog","cfmoto_apparel_gear"),
("Indian","Apparel & Gear","catalog","indian_apparel_gear"),("Kawasaki","Apparel & Gear","catalog","kawasaki_apparel_gear"),
("Polaris","Apparel & Gear / Offroad","catalog","polaris_offroad_apparel_gear"),
("Polaris","Apparel & Gear / Snowmobile","catalog","polaris_snowmobile_apparel_gear"),
("Sea-Doo","Apparel & Gear","catalog","sea_doo_riding_gear"),("Ski-Doo","Apparel & Gear","catalog","ski_doo_riding_gear"),
("Yamaha","Apparel & Gear","catalog","yamaha_apparel"),
("Aftermarket","ATV","catalog","aftermarket_atv"),
("Aftermarket","Apparel & Gear","catalog","aftermarket_apparel_gear"),("Aftermarket","Off-Road","catalog","aftermarket_off_road"),
("Aftermarket","Snowmobile","catalog","aftermarket_snowmobile"),("Aftermarket","Street & Cruiser","catalog","aftermarket_street"),
("Aftermarket","V-Twin","catalog","aftermarket_v_twin"),("Aftermarket","Shop Oils & Tools","catalog","aftermarket_shop_maintenance"),
("Aftermarket","Watercraft","catalog","aftermarket_watercraft")]
CATALOGS=[Catalog(*x) for x in ROWS]

def clean(v): return re.sub(r"\s+"," ",str(v or "")).strip()
def norm(v): return clean(v).lower()
def normalize_oem(v):
    v=re.sub(r"\s+","",v.strip())
    if not re.fullmatch(r"[A-Za-z0-9-]{4,40}",v): raise ValueError("Invalid catalog number.")
    return v
def money(v)->Optional[float]:
    m=re.search(r"\$?\s*([0-9][0-9,]*\.[0-9]{2})",str(v or ""))
    return float(m.group(1).replace(",","")) if m else None

async def cf_present(p):
    try: t=(await p.title()).lower()
    except: t=""
    try: b=(await p.locator("body").inner_text(timeout=1500)).lower()
    except: b=""
    return any(x in t+"\n"+b[:5000] for x in CF_MARKERS)

async def settle(p):
    # V6 speed-only change: do NOT wait for networkidle. Modern catalog pages keep
    # background requests alive; V5.1 could therefore spend ~5 s here per page.
    # We only wait while a real Cloudflare challenge is visible.
    announced=False; deadline=time.monotonic()+120
    while time.monotonic()<deadline and await cf_present(p):
        if not announced:
            print("Cloudflare verification detected. Complete it manually in Chrome; waiting...")
            announced=True
        await asyncio.sleep(.5)
    await asyncio.sleep(.05)

async def goto(p,u):
    t=time.monotonic()
    await p.goto(u,wait_until="domcontentloaded",timeout=90000)
    nav=time.monotonic()-t
    t=time.monotonic(); await settle(p); st=time.monotonic()-t
    return {"navigation_seconds":round(nav,3),"settle_seconds":round(st,3)}

async def install_speed_routes(p):
    # V6 speed-only: HTML/JS/XHR stay untouched. Only binary resources that the
    # parsers never read are blocked. Image URLs remain present in the HTML DOM.
    async def handler(route):
        if route.request.resource_type in {"image","media","font"}:
            await route.abort()
        else:
            await route.continue_()
    await p.route("**/*",handler)

async def parts_result(p,cat,oem):
    """
    V5.1: Parts-only parser patch.

    Routing is unchanged.
    Product/Accessories/Oil/Apparel/Aftermarket parsers are unchanged.
    QOH is not read.

    Fixes:
      1) For the exact current OEM form, MSRP is read from verified form metadata
         (data-retail) as well as the visible price cell.
      2) replacement_oem == query_oem is never treated as a real replacement.
      3) An exact current row with no usable public price remains HUMAN_REVIEW.
    """
    u=f"{cat.search_url}?partsearch={quote(oem)}"
    await goto(p,u)

    # --- Primary verified Parts path: exact current add-to-cart form ---
    form=p.locator(f'form#add_{oem}')
    if await form.count():
        form=form.first
        sku=clean(await form.locator('input[name="sku"]').get_attribute("value"))

        cur=form.locator(".itemnum")
        current=clean(await cur.first.inner_text()) if await cur.count() else ""

        if norm(sku)==norm(oem) and norm(current)==norm(oem):
            n=form.locator(".c1a .ellipsis_text")
            name=clean(await n.first.inner_text()) if await n.count() else None
            if not name:
                n=form.locator(".c1a")
                if await n.count():
                    name=(clean(await n.first.get_attribute("threedots"))
                          or clean(await n.first.inner_text()))

            # MSRP/public retail price: prefer the exact form's own metadata.
            # Dealer/private price and QOH are intentionally not read.
            price_candidates=[]

            for attr in ("data-retail","data-price"):
                v=clean(await form.get_attribute(attr))
                if v:
                    price_candidates.append(v)

            for sel in (
                'input[name="retail"]',
                'input[name="msrp"]',
                '[data-retail]',
                '.c2 .dbl',
                '.c2',
            ):
                loc=form.locator(sel)
                if await loc.count():
                    el=loc.first
                    v=clean(await el.get_attribute("value"))
                    if not v:
                        v=clean(await el.get_attribute("data-retail"))
                    if not v:
                        try:
                            v=clean(await el.inner_text())
                        except Exception:
                            v=""
                    if v:
                        price_candidates.append(v)

            price=None
            price_source=None
            for candidate in price_candidates:
                pval=money(candidate)
                if pval is not None:
                    price=pval
                    price_source=candidate
                    break

            prev=await p.evaluate("""(o)=>{
              const a=[];
              for(const r of document.querySelectorAll('#searchresults .partlistrow')){
                const x=r.querySelector('.itemnumstrike'),y=r.querySelector('.itemnumnew');
                if(!x||!y) continue;
                const xv=(x.textContent||'').trim(), yv=(y.textContent||'').trim();
                if(yv.toLowerCase()===o.toLowerCase() &&
                   xv && xv.toLowerCase()!==o.toLowerCase() && !a.includes(xv)) a.push(xv);
              }
              return a;
            }""",oem)

            base={
                "manufacturer":cat.manufacturer,"catalog":cat.label,
                "query_oem":oem,"oem":current,"name":name,"price":price,
                "currency":"USD","previous_oems":prev,"image":None,
                "product_url":None,"search_url":u,"variants":[]
            }

            if price is not None:
                return {
                    **base,"found":True,"status":"FOUND",
                    "_diagnostic":{
                        "match_rule":"exact_current_parts_row_v5_1",
                        "price_source":price_source,
                        "qoh_extracted":False
                    }
                },u

            return {
                **base,"found":False,"status":"HUMAN_REVIEW","human_review":True,
                "message":HUMAN_REVIEW_MESSAGE,"ask":"Запросить?",
                "_diagnostic":{
                    "match_rule":"exact_current_parts_row_without_price_v5_1",
                    "price_candidates":price_candidates,
                    "qoh_extracted":False
                }
            },u

    # --- Fallback Parts row path ---
    row=await p.evaluate("""(o)=>{
      o=o.toLowerCase();
      for(const r of document.querySelectorAll('#searchresults .partlistrow')){
        const vals=[...r.querySelectorAll('.itemnum,.itemnumstrike,.itemnumnew,[data-sku],input[name="sku"]')]
          .map(e=>(e.value||e.getAttribute('data-sku')||e.textContent||'').trim())
          .filter(Boolean);
        if(vals.some(v=>v.toLowerCase()===o)){
          const old=r.querySelector('.itemnumstrike'),
                nw=r.querySelector('.itemnumnew'),
                n=r.querySelector('.c1a .ellipsis_text')||r.querySelector('.c1a'),
                pr=r.querySelector('.c2 .dbl'),
                f=r.querySelector('form');
          return {
            matched:true,
            vals,
            old:(old?.textContent||'').trim()||null,
            replacement:(nw?.textContent||'').trim()||null,
            name:(n?.getAttribute('threedots')||n?.textContent||'').replace(/\\s+/g,' ').trim()||null,
            price:(pr?.textContent||'').replace(/\\s+/g,' ').trim()||null,
            data_retail:(f?.getAttribute('data-retail')||r.getAttribute('data-retail')||'').trim()||null
          };
        }
      }
      return {matched:false};
    }""",oem)

    if row.get("matched"):
        # Never call the query itself a replacement.
        replacement=row.get("replacement")
        if replacement and norm(replacement)==norm(oem):
            replacement=None

        old=row.get("old")
        if old and norm(old)==norm(oem):
            old=None

        # Public MSRP can be visible or carried by the exact row/form metadata.
        pr=money(row.get("data_retail"))
        price_source=row.get("data_retail")
        if pr is None:
            pr=money(row.get("price"))
            price_source=row.get("price")

        # If this is not an old/struck-through row and it has a price, it is FOUND.
        if pr is not None and not old:
            return {
                "found":True,"status":"FOUND",
                "manufacturer":cat.manufacturer,"catalog":cat.label,
                "query_oem":oem,"oem":oem,"name":row.get("name"),
                "price":pr,"currency":"USD","previous_oems":[],
                "image":None,"product_url":None,"search_url":u,"variants":[],
                "_diagnostic":{
                    "match_rule":"exact_real_parts_result_row_v5_1",
                    "price_source":price_source,
                    "qoh_extracted":False
                }
            },u

        # Exact number exists but no usable current public price -> human review.
        return {
            "found":False,"status":"HUMAN_REVIEW","human_review":True,
            "manufacturer":cat.manufacturer,"catalog":cat.label,
            "query_oem":oem,"oem":oem,"name":row.get("name"),
            "price":None,"currency":"USD",
            "replacement_oem":replacement,
            "search_url":u,
            "message":HUMAN_REVIEW_MESSAGE,"ask":"Запросить?",
            "_diagnostic":{
                "match_rule":"exact_parts_result_row_without_usable_price_v5_1",
                "replacement_equals_query_ignored": row.get("replacement") is not None and replacement is None,
                "qoh_extracted":False
            }
        },u

    return None,u

async def direct_parts_result(p,cat,oem):
    """V6.6 experiment: direct GET Parts search, preserving V5.1 result semantics."""
    u=f"{cat.search_url}?partsearch={quote(oem)}"
    timing={"strategy":"direct_parts_get_v6_6","browser_parts_fallback_used":False}
    t=time.monotonic()
    try:
        resp=await p.context.request.get(u,timeout=30000,fail_on_status_code=False)
        timing["direct_parts_request_seconds"]=round(time.monotonic()-t,3)
        timing["direct_parts_http_status"]=resp.status
        body=await resp.text(); timing["direct_parts_response_bytes"]=len(body.encode("utf-8",errors="ignore"))
        challenged=any(x in body.lower() for x in CF_MARKERS); timing["cloudflare_in_parts_response"]=challenged
        if challenged or resp.status < 200 or resp.status >= 400:
            raise RuntimeError(f"direct Parts response unusable: HTTP {resp.status}, cloudflare={challenged}")
        timing["direct_parts_parse_attempted"]=True
        d=await p.evaluate(r'''([html,o])=>{const doc=new DOMParser().parseFromString(html,'text/html'),n=s=>(s||'').replace(/\s+/g,' ').trim(),lo=s=>n(s).toLowerCase();
          const form=doc.getElementById('add_'+o); let primary=null;
          if(form){const sku=n(form.querySelector('input[name="sku"]')?.value),cur=n(form.querySelector('.itemnum')?.textContent);
            if(lo(sku)===lo(o)&&lo(cur)===lo(o)){let ne=form.querySelector('.c1a .ellipsis_text'),name=n(ne?.textContent); if(!name){ne=form.querySelector('.c1a');name=n(ne?.getAttribute('threedots')||ne?.textContent)}
              const pc=[]; for(const a of ['data-retail','data-price']){const v=n(form.getAttribute(a));if(v)pc.push(v)}
              for(const sel of ['input[name="retail"]','input[name="msrp"]','[data-retail]','.c2 .dbl','.c2']){const el=form.querySelector(sel);if(el){const v=n(el.value||el.getAttribute('data-retail')||el.textContent);if(v)pc.push(v)}}
              const prev=[];for(const r of doc.querySelectorAll('#searchresults .partlistrow')){const x=r.querySelector('.itemnumstrike'),y=r.querySelector('.itemnumnew');if(!x||!y)continue;const xv=n(x.textContent),yv=n(y.textContent);if(lo(yv)===lo(o)&&xv&&lo(xv)!==lo(o)&&!prev.includes(xv))prev.push(xv)}
              primary={sku,current:cur,name,price_candidates:pc,previous_oems:prev};}}
          let row={matched:false}; if(!primary){for(const r of doc.querySelectorAll('#searchresults .partlistrow')){const vals=[...r.querySelectorAll('.itemnum,.itemnumstrike,.itemnumnew,[data-sku],input[name="sku"]')].map(e=>n(e.value||e.getAttribute('data-sku')||e.textContent)).filter(Boolean);if(vals.some(v=>lo(v)===lo(o))){const old=r.querySelector('.itemnumstrike'),nw=r.querySelector('.itemnumnew'),ne=r.querySelector('.c1a .ellipsis_text')||r.querySelector('.c1a'),pr=r.querySelector('.c2 .dbl'),f=r.querySelector('form');row={matched:true,vals,old:n(old?.textContent)||null,replacement:n(nw?.textContent)||null,name:n(ne?.getAttribute('threedots')||ne?.textContent)||null,price:n(pr?.textContent)||null,data_retail:n(f?.getAttribute('data-retail')||r.getAttribute('data-retail'))||null};break}}}
          return {primary,row};}''',[body,oem])
        primary=d.get("primary")
        if primary:
            price=None; price_source=None
            for candidate in primary.get("price_candidates",[]):
                pval=money(candidate)
                if pval is not None: price=pval; price_source=candidate; break
            base={"manufacturer":cat.manufacturer,"catalog":cat.label,"query_oem":oem,"oem":primary.get("current"),"name":primary.get("name"),"price":price,"currency":"USD","previous_oems":primary.get("previous_oems",[]),"image":None,"product_url":None,"search_url":u,"variants":[]}
            timing["direct_parts_exact_match"]=True
            if price is not None:
                return {**base,"found":True,"status":"FOUND","_diagnostic":{"match_rule":"exact_current_parts_row_v5_1","price_source":price_source,"qoh_extracted":False}},u,timing
            return {**base,"found":False,"status":"HUMAN_REVIEW","human_review":True,"message":HUMAN_REVIEW_MESSAGE,"ask":"Запросить?","_diagnostic":{"match_rule":"exact_current_parts_row_without_price_v5_1","price_candidates":primary.get("price_candidates",[]),"qoh_extracted":False}},u,timing
        row=d.get("row") or {}
        if row.get("matched"):
            replacement=row.get("replacement")
            if replacement and norm(replacement)==norm(oem): replacement=None
            old=row.get("old")
            if old and norm(old)==norm(oem): old=None
            pr=money(row.get("data_retail")); price_source=row.get("data_retail")
            if pr is None: pr=money(row.get("price")); price_source=row.get("price")
            timing["direct_parts_exact_match"]=True
            if pr is not None and not old:
                return {"found":True,"status":"FOUND","manufacturer":cat.manufacturer,"catalog":cat.label,"query_oem":oem,"oem":oem,"name":row.get("name"),"price":pr,"currency":"USD","previous_oems":[],"image":None,"product_url":None,"search_url":u,"variants":[],"_diagnostic":{"match_rule":"exact_real_parts_result_row_v5_1","price_source":price_source,"qoh_extracted":False}},u,timing
            return {"found":False,"status":"HUMAN_REVIEW","human_review":True,"manufacturer":cat.manufacturer,"catalog":cat.label,"query_oem":oem,"oem":oem,"name":row.get("name"),"price":None,"currency":"USD","replacement_oem":replacement,"search_url":u,"message":HUMAN_REVIEW_MESSAGE,"ask":"Запросить?","_diagnostic":{"match_rule":"exact_parts_result_row_without_usable_price_v5_1","replacement_equals_query_ignored":row.get("replacement") is not None and replacement is None,"qoh_extracted":False}},u,timing
        timing["direct_parts_exact_match"]=False
        return None,u,timing
    except Exception as e:
        timing["direct_parts_error"]=f"{type(e).__name__}: {e}"; timing["direct_parts_exact_match"]=False; timing["browser_parts_fallback_used"]=True
        t2=time.monotonic(); result,u=await parts_result(p,cat,oem); timing["browser_parts_fallback_seconds"]=round(time.monotonic()-t2,3); return result,u,timing

async def submit_catalog(p,cat,oem):
    timing={}
    t=time.monotonic(); timing["landing"]=await goto(p,cat.landing_url); timing["landing_total_seconds"]=round(time.monotonic()-t,3)
    t=time.monotonic()
    f=p.locator("form").filter(has=p.locator("input[name='partsearch']")).first
    if not await f.count():
        timing["form_submit_seconds"]=round(time.monotonic()-t,3)
        return [],"partsearch form not found",timing
    field=f.locator("input[name='partsearch']").first; await field.fill(oem)
    b=f.locator("input[type='submit'],button[type='submit'],button").first
    if await b.count(): await b.click()
    else: await field.press("Enter")
    try: await p.wait_for_load_state("domcontentloaded",timeout=30000)
    except: pass
    await settle(p)
    timing["form_submit_seconds"]=round(time.monotonic()-t,3)
    t=time.monotonic()
    links=await p.evaluate("""()=>{const a=[],s=new Set();for(const x of document.querySelectorAll('a[href*="/oemcatalogs/p/"]'))
      if(!s.has(x.href)){s.add(x.href);a.push(x.href)}return a}""")
    timing["collect_links_seconds"]=round(time.monotonic()-t,3)
    timing["links_found"]=len(links)
    return links,None,timing

async def inspect_product(p,cat,oem,url):
    await goto(p,url)
    d=await p.evaluate("""()=>{const n=s=>(s||'').replace(/\\s+/g,' ').trim(),abs=v=>{if(!v)return null;try{return new URL(v,location.href).href}catch{return v}};
      const h=document.querySelector('h1')||document.querySelector('[itemprop="name"]'),br=document.querySelector('[itemprop="brand"]'),
      im=document.querySelector('#productimages img.lrgimg')||document.querySelector('img.lrgimg')||document.querySelector('[itemprop="image"]')||
      [...document.images].find(x=>(x.src||'').includes('/cdn/catalogs/')),vs=[];
      for(const x of document.querySelectorAll('div.productitem[data-sku]')){const f=x.querySelector('form'),
       sku=n(x.getAttribute('data-sku')||x.querySelector('[itemprop="sku"]')?.textContent||f?.querySelector('input[name="item"]')?.value||''),
       line=n(x.querySelector('.sku')?.textContent||''),m=line.match(/Dist\\s*#\\s*:\\s*([^|\\s]+)/i),ds=[...x.querySelectorAll('.desc,.itemdesc')].map(y=>n(y.textContent)).filter(Boolean);
       let color=f?.querySelector('input[name="color"]')?.value||null,size=f?.querySelector('input[name="size"]')?.value||null;
       for(const z of ds){let q=z.match(/(?:Marketing\\s+|Market\\s+|Primary\\s+)?Color\\s*:\\s*(.+)/i);if(q&&!color)color=n(q[1]);
        q=z.match(/Size\\s*:\\s*(.+)/i);if(q&&!size)size=n(q[1])}
       const pm=x.querySelector('[itemprop="price"]');vs.push({sku,dist_number:m?n(m[1]):null,
       name:n(x.querySelector('.name')?.textContent||h?.textContent),price_text:n(x.querySelector('.price')?.textContent||pm?.getAttribute('content')),
       color,size,descriptions:ds,form_id:f?.id||null})}
      return {url:location.href,heading:n(h?.textContent),brand:n(br?.textContent),image:abs(im?.currentSrc||im?.src||null),variants:vs}}""")
    for v in d["variants"]: v["price"]=money(v.pop("price_text",None))
    ex=next((v for v in d["variants"] if norm(v.get("sku"))==norm(oem) or norm(v.get("dist_number"))==norm(oem)),None)
    if not ex:return None
    fld="sku" if norm(ex.get("sku"))==norm(oem) else "dist_number"
    base={"manufacturer":cat.manufacturer,"catalog":cat.label,"query_oem":oem,"oem":ex.get("sku") or oem,
          "item_sku":ex.get("sku"),"dist_number":ex.get("dist_number"),"name":ex.get("name") or d["heading"],
          "brand":d.get("brand") or None,"price":ex.get("price"),"currency":"USD","color":ex.get("color"),
          "size":ex.get("size"),"image":d["image"],"product_url":d["url"],"search_url":cat.search_url,"variants":d["variants"]}
    if ex.get("price") is None:
        return {**base,"found":False,"status":"HUMAN_REVIEW","human_review":True,"message":HUMAN_REVIEW_MESSAGE,
                "ask":"Запросить?","_diagnostic":{"match_rule":f"exact_product_{fld}_without_price","qoh_extracted":False}}
    return {**base,"found":True,"status":"FOUND","_diagnostic":{"match_rule":f"exact_product_{fld}","qoh_extracted":False}}

async def direct_catalog_links(p,cat,oem):
    # V6.4-only experiment: bypass catalog landing and POST directly to productsearch.
    # This does candidate discovery only; exact FOUND confirmation remains the
    # frozen V6.2 inspect_product() parser.
    timing={"strategy":"direct_productsearch_post_v6_4","landing_bypassed":True}
    t=time.monotonic()
    try:
        resp=await p.context.request.post(
            cat.search_url,
            form={"partsearch":oem},
            timeout=30000,
            fail_on_status_code=False,
        )
        timing["direct_request_seconds"]=round(time.monotonic()-t,3)
        timing["http_status"]=resp.status
        body=await resp.text()
        timing["response_bytes"]=len(body.encode("utf-8",errors="ignore"))

        low=body.lower()
        challenged=any(x in low for x in CF_MARKERS)
        timing["cloudflare_in_direct_response"]=challenged
        if challenged or resp.status < 200 or resp.status >= 400:
            raise RuntimeError(f"direct response unusable: HTTP {resp.status}, cloudflare={challenged}")

        raw=re.findall(r"href\s*=\s*['\"]([^'\"]*/oemcatalogs/p/[^'\"]+)['\"]",body,re.I)
        links=[]; seen=set()
        from urllib.parse import urljoin
        for href in raw:
            href=href.replace("&amp;","&")
            u=urljoin(BASE,href)
            if u not in seen:
                seen.add(u); links.append(u)
        timing["direct_links_found"]=len(links)
        timing["fallback_used"]=False
        return links,None,timing
    except Exception as e:
        timing["direct_error"]=f"{type(e).__name__}: {e}"
        timing["fallback_used"]=True
        t2=time.monotonic()
        links,err,fb=await submit_catalog(p,cat,oem)
        timing["v6_2_fallback_seconds"]=round(time.monotonic()-t2,3)
        timing["v6_2_fallback"]=fb
        return links,err,timing

async def direct_product_verify(p,cat,oem,url):
    timing={"product_verification_strategy":"direct_product_request_v6_5","browser_product_fallback_used":False}
    t=time.monotonic()
    try:
        resp=await p.context.request.get(url,timeout=30000,fail_on_status_code=False)
        timing["direct_product_request_seconds"]=round(time.monotonic()-t,3)
        timing["direct_product_http_status"]=resp.status
        body=await resp.text(); timing["direct_product_response_bytes"]=len(body.encode("utf-8",errors="ignore"))
        challenged=any(x in body.lower() for x in CF_MARKERS); timing["cloudflare_in_product_response"]=challenged
        if challenged or resp.status < 200 or resp.status >= 400: raise RuntimeError(f"direct product response unusable: HTTP {resp.status}, cloudflare={challenged}")
        timing["direct_product_parse_attempted"]=True
        d=await p.evaluate(r"""([html,productUrl])=>{const doc=new DOMParser().parseFromString(html,'text/html'),n=s=>(s||'').replace(/\s+/g,' ').trim(),abs=v=>{if(!v)return null;try{return new URL(v,productUrl).href}catch{return v}};
          const h=doc.querySelector('h1')||doc.querySelector('[itemprop="name"]'),br=doc.querySelector('[itemprop="brand"]'),im=doc.querySelector('#productimages img.lrgimg')||doc.querySelector('img.lrgimg')||doc.querySelector('[itemprop="image"]')||[...doc.images].find(x=>(x.getAttribute('src')||'').includes('/cdn/catalogs/')),vs=[];
          for(const x of doc.querySelectorAll('div.productitem[data-sku]')){const f=x.querySelector('form'),sku=n(x.getAttribute('data-sku')||x.querySelector('[itemprop="sku"]')?.textContent||f?.querySelector('input[name="item"]')?.value||''),line=n(x.querySelector('.sku')?.textContent||''),m=line.match(/Dist\s*#\s*:\s*([^|\s]+)/i),ds=[...x.querySelectorAll('.desc,.itemdesc')].map(y=>n(y.textContent)).filter(Boolean);let color=f?.querySelector('input[name="color"]')?.value||null,size=f?.querySelector('input[name="size"]')?.value||null;for(const z of ds){let q=z.match(/(?:Marketing\s+|Market\s+|Primary\s+)?Color\s*:\s*(.+)/i);if(q&&!color)color=n(q[1]);q=z.match(/Size\s*:\s*(.+)/i);if(q&&!size)size=n(q[1])}const pm=x.querySelector('[itemprop="price"]');vs.push({sku,dist_number:m?n(m[1]):null,name:n(x.querySelector('.name')?.textContent||h?.textContent),price_text:n(x.querySelector('.price')?.textContent||pm?.getAttribute('content')),color,size,descriptions:ds,form_id:f?.id||null})}return {url:productUrl,heading:n(h?.textContent),brand:n(br?.textContent),image:abs(im?.getAttribute('src')||null),variants:vs}}""",[body,url])
        for v in d["variants"]: v["price"]=money(v.pop("price_text",None))
        ex=next((v for v in d["variants"] if norm(v.get("sku"))==norm(oem) or norm(v.get("dist_number"))==norm(oem)),None); timing["direct_product_exact_match"]=bool(ex)
        if not ex: raise RuntimeError("direct product parser did not confirm exact SKU/Dist #")
        fld="sku" if norm(ex.get("sku"))==norm(oem) else "dist_number"
        base={"manufacturer":cat.manufacturer,"catalog":cat.label,"query_oem":oem,"oem":ex.get("sku") or oem,"item_sku":ex.get("sku"),"dist_number":ex.get("dist_number"),"name":ex.get("name") or d["heading"],"brand":d.get("brand") or None,"price":ex.get("price"),"currency":"USD","color":ex.get("color"),"size":ex.get("size"),"image":d["image"],"product_url":d["url"],"search_url":cat.search_url,"variants":d["variants"]}
        if ex.get("price") is None:return {**base,"found":False,"status":"HUMAN_REVIEW","human_review":True,"message":HUMAN_REVIEW_MESSAGE,"ask":"Запросить?","_diagnostic":{"match_rule":f"exact_product_{fld}_without_price","qoh_extracted":False}},timing
        return {**base,"found":True,"status":"FOUND","_diagnostic":{"match_rule":f"exact_product_{fld}","qoh_extracted":False}},timing
    except Exception as e:
        timing["direct_product_error"]=f"{type(e).__name__}: {e}"; timing["direct_product_exact_match"]=False; timing["browser_product_fallback_used"]=True
        t2=time.monotonic(); x=await inspect_product(p,cat,oem,url); timing["browser_product_fallback_seconds"]=round(time.monotonic()-t2,3); return x,timing

async def catalog_match(p,cat,oem):
    total=time.monotonic(); links,err,timing=await direct_catalog_links(p,cat,oem)
    if err: timing["catalog_total_seconds"]=round(time.monotonic()-total,3); return None,cat.search_url,err,timing
    product_attempts=[]
    for link in links[:12]:
        t=time.monotonic()
        try:
            x,pt=await direct_product_verify(p,cat,oem,link); attempt={"url":link,"seconds":round(time.monotonic()-t,3),"exact_match":bool(x)}; attempt.update(pt); product_attempts.append(attempt)
            if x: timing["product_attempts"]=product_attempts; timing["catalog_total_seconds"]=round(time.monotonic()-total,3); return x,cat.search_url,None,timing
        except Exception as e: product_attempts.append({"url":link,"seconds":round(time.monotonic()-t,3),"exact_match":False,"error":f"{type(e).__name__}: {e}"})
    timing["product_attempts"]=product_attempts; timing["catalog_total_seconds"]=round(time.monotonic()-total,3); return None,cat.search_url,None,timing

def manufacturer_alias(value):
    key=norm(value).replace("_","-").replace(" ","-")
    aliases={
        "ski-doo":"Ski-Doo","skidoo":"Ski-Doo",
        "can-am":"Can-Am","canam":"Can-Am",
        "sea-doo":"Sea-Doo","seadoo":"Sea-Doo",
        "arctic-cat":"Arctic Cat","arcticcat":"Arctic Cat",
        "polaris":"Polaris","honda":"Honda","kawasaki":"Kawasaki",
        "ktm":"KTM","suzuki":"Suzuki","yamaha":"Yamaha",
        "indian":"Indian","cfmoto":"CFMoto","cf-moto":"CFMoto",
        "aftermarket":"Aftermarket",
    }
    return aliases.get(key)

def catalogs_for_manufacturer(manufacturer):
    # V5: the customer chooses the manufacturer. Never search other manufacturers.
    return [c for c in CATALOGS if c.manufacturer == manufacturer]

async def find_oem(p,manufacturer,oem):
    order=catalogs_for_manufacturer(manufacturer)
    attempts=[]; started=time.monotonic()

    print(f"Selected manufacturer: {manufacturer}")
    print(f"Catalogs to check: {len(order)}")
    print("Cross-manufacturer fallback: DISABLED")
    print("V6.6 speed mode: V6.5 preserved; direct Parts search ENABLED")

    if not order:
        return {"found":False,"status":"CONFIG_ERROR","manufacturer":manufacturer,
                "query_oem":oem,"message":"No catalogs configured for selected manufacturer.",
                "_attempts":[]}

    for i,cat in enumerate(order,1):
        print(f"[{i}/{len(order)}] {cat.manufacturer} -> {cat.label} ...")
        attempt_started=time.monotonic()
        try:
            if cat.kind=="parts":
                result,url,timing=await direct_parts_result(p,cat,oem); err=None
                timing["catalog_total_seconds"]=round(time.monotonic()-attempt_started,3)
            else:
                result,url,err,timing=await catalog_match(p,cat,oem)

            attempt={"manufacturer":cat.manufacturer,"catalog":cat.label,"search_url":url,
                     "status":result.get("status") if result else "NO_EXACT_MATCH",
                     "exact_match":bool(result),"error":err,
                     "timing":timing}
            attempts.append(attempt)
            print(f"    step time: {timing.get('catalog_total_seconds')} sec")
            if result:
                result["_attempts"]=attempts
                result["_optimization"]={
                    "strategy":"customer_selected_manufacturer_v6_6_direct_parts_search",
                    "base_parser":"V5.1 frozen",
                    "networkidle_wait":False,
                    "blocked_resource_types":["image","media","font"],
                    "selected_manufacturer":manufacturer,
                    "cross_manufacturer_fallback":False,
                    "attempts_used":len(attempts),
                    "catalogs_total":len(order),
                    "elapsed_seconds":round(time.monotonic()-started,2)}
                return result
        except Exception as e:
            elapsed=round(time.monotonic()-attempt_started,3)
            attempts.append({"manufacturer":cat.manufacturer,"catalog":cat.label,
                             "search_url":cat.search_url,"status":"ERROR",
                             "exact_match":False,"error":f"{type(e).__name__}: {e}",
                             "timing":{"catalog_total_seconds":elapsed}})
            print(f"    step time: {elapsed} sec (ERROR)")

    return {"found":False,"status":"NOT_FOUND","manufacturer":manufacturer,"query_oem":oem,
            "message":f"По указанному номеру ничего не найдено в каталоге {manufacturer}.",
            "actions":["Ввести другой номер","Сменить производителя"],
            "_attempts":attempts,
            "_optimization":{"strategy":"customer_selected_manufacturer_v6_6_direct_parts_search",
              "base_parser":"V5.1 frozen","networkidle_wait":False,
              "blocked_resource_types":["image","media","font"],
              "selected_manufacturer":manufacturer,"cross_manufacturer_fallback":False,
              "attempts_used":len(attempts),"catalogs_total":len(order),
              "elapsed_seconds":round(time.monotonic()-started,2)}}

def show(r):
    s=r.get("status");print("\n"+"="*100+"\n"+str(s)+"\n"+"="*100)
    print("Query:",r.get("query_oem"))
    if s in ("FOUND","HUMAN_REVIEW"):
        print("Manufacturer:",r.get("manufacturer"));print("Catalog:",r.get("catalog"))
        print("Item/SKU:",r.get("item_sku") or r.get("oem") or "—")
        if r.get("dist_number"):print("Dist #:",r["dist_number"])
        print("Name:",r.get("name") or "—");print("Price:",f"${r['price']:.2f}" if isinstance(r.get("price"),(int,float)) else "—")
        if r.get("previous_oems"):
            print("Previous OEMs:");[print(" ",x) for x in r["previous_oems"]]
        if r.get("image"):print("Image:",r["image"])
        if r.get("product_url"):print("Product URL:",r["product_url"])
    if s=="HUMAN_REVIEW":print("\n"+HUMAN_REVIEW_MESSAGE+"\nЗапросить?")
    if s=="NOT_FOUND":print(r.get("message"))
    o=r.get("_optimization",{});print(f"\nAttempts used: {o.get('attempts_used')} / {o.get('catalogs_total')}")
    print("Elapsed:",o.get("elapsed_seconds"),"sec")
    print("\nJSON:\n"+json.dumps(r,ensure_ascii=False,indent=2))

CONTROL_TESTS=[
    ("Ski-Doo","417224332"),
    ("Can-Am","422280652"),
    ("Sea-Doo","276000389"),
    ("Arctic Cat","0627-084"),
    ("Polaris","3221115"),
    ("Honda","17212-HP5-600"),
    ("Kawasaki","59011-0019"),
    ("KTM","77230001144"),
    ("Suzuki","12771-28H00"),
    ("Yamaha","8MY-F610A-10-00"),
    ("Indian","7557051"),
    ("CFMoto","0GRB-111008"),
    ("Aftermarket","18-95074"),
    ("Arctic Cat","0627-033"),
]

async def run(tests):
    async with async_playwright() as pw:
        print("Connecting to existing Chrome:",CDP_URL)
        b=await pw.chromium.connect_over_cdp(CDP_URL)
        ctx=b.contexts[0]
        p=ctx.pages[0] if ctx.pages else await ctx.new_page()
        print("ATTACHED TO EXISTING CHROME")
        print("DIAGNOSTIC V6.6 DIRECT PARTS SEARCH ONLY — clean V6.5 base; parser/routing behavior is frozen; production files are NOT modified.")
        print("QOH / availability extraction: DISABLED")
        print("Automatic manufacturer detection: DISABLED")
        await install_speed_routes(p)
        print("Speed optimization: image/media/font downloads BLOCKED")
        print("Speed optimization: networkidle wait DISABLED")
        rs=[]
        for i,(manufacturer,oem) in enumerate(tests,1):
            print(f"\nCONTROL TEST {i}/{len(tests)}: {manufacturer} / {oem}")
            r=await find_oem(p,manufacturer,oem)
            show(r); rs.append(r)
        return 0 if all(r.get("status") in ("FOUND","HUMAN_REVIEW") for r in rs) else 2

def list_manufacturers():
    seen=[]
    for c in CATALOGS:
        if c.manufacturer not in seen:
            seen.append(c.manufacturer)
    print("Available manufacturers:")
    for x in seen: print(" -",x)

def main():
    a=argparse.ArgumentParser(
        description="Diagnostic V6.6: V6.5 + direct Parts search only")
    a.add_argument("manufacturer",nargs="?",help='e.g. "Ski-Doo", Polaris, Honda, Aftermarket')
    a.add_argument("oem",nargs="?",help="catalog / part number")
    a.add_argument("--all-tests",action="store_true",help="run built-in manufacturer-specific control tests")
    a.add_argument("--list-manufacturers",action="store_true")
    x=a.parse_args()

    if x.list_manufacturers:
        list_manufacturers(); return 0

    if x.all_tests or (not x.manufacturer and not x.oem):
        tests=CONTROL_TESTS
    else:
        if not x.manufacturer or not x.oem:
            a.error("Specify both manufacturer and OEM, or use --all-tests")
        manufacturer=manufacturer_alias(x.manufacturer)
        if not manufacturer:
            print(f"Unknown manufacturer: {x.manufacturer}",file=sys.stderr)
            list_manufacturers(); return 2
        tests=[(manufacturer,normalize_oem(x.oem))]

    try:return asyncio.run(run(tests))
    except KeyboardInterrupt:print("\nStopped by user.");return 130
    except Exception as e:print(f"\nERROR: {type(e).__name__}: {e}",file=sys.stderr);return 1

if __name__=="__main__":raise SystemExit(main())
