"""Read-only responsive diagnostic for isolated approved POWERSPORTS stage."""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

MOBILE_DIAGNOSTIC_HTML = r"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow">
<title>EXTREMIZER POWERSPORTS — Mobile QA only</title>
<style>
*{box-sizing:border-box}body{background:#080808;color:#dedede;font-family:Arial,sans-serif;margin:0;padding:18px}
h1{font-size:21px;margin:0 0 10px;color:#BFF803}p{line-height:1.4;max-width:1000px}
.qa{display:flex;align-items:flex-start;gap:20px;flex-wrap:wrap}
.case{flex:0 0 auto;background:#121414;border:1px solid #626662;padding:9px;border-radius:8px}
.case h2{font-size:16px;margin:0 0 8px}.case iframe{display:block;border:1px solid #626662;background:#080808}
.case pre{font-size:12px;line-height:1.5;color:#ddd;white-space:pre-wrap;max-width:430px;overflow-wrap:anywhere}
button{padding:12px 18px;font-size:15px;border:0;background:#BFF803;color:#080808;font-weight:700;cursor:pointer}
.pass{color:#BFF803}.fail{color:#ff9898}
</style></head><body>
<h1>POWERSPORTS — Responsive QA (не утверждённый дизайн)</h1>
<p>Три реальных CSS viewport внутри same-origin iframe: 360, 390, 430 px.
Контент каждого iframe — <code>/web2/stage04</code>. Только тестовые OEM-данные; оформление отключено.</p>
<button id="run" type="button">Проверить mock OEM и корзину во всех iframe</button>
<div class="qa">
<section class="case"><h2>360 × 780</h2><iframe width="360" height="780" src="/web2/stage04" title="360px POWERSPORTS" id="f360"></iframe><pre id="r360">Загрузка…</pre></section>
<section class="case"><h2>390 × 844</h2><iframe width="390" height="844" src="/web2/stage04" title="390px POWERSPORTS" id="f390"></iframe><pre id="r390">Загрузка…</pre></section>
<section class="case"><h2>430 × 932</h2><iframe width="430" height="932" src="/web2/stage04" title="430px POWERSPORTS" id="f430"></iframe><pre id="r430">Загрузка…</pre></section>
</div>
<script>
"use strict";
function collect(w){
 const f=document.getElementById("f"+w), d=f.contentDocument,win=f.contentWindow;
 if(!d||!d.querySelector(".canvas"))return {error:"frame unavailable"};
 const box=(id)=>{const e=d.querySelector(id);if(!e)return null;const b=e.getBoundingClientRect();return {width:+b.width.toFixed(1),height:+b.height.toFixed(1),top:+b.top.toFixed(1),left:+b.left.toFixed(1)}};
 const img=d.querySelector(".approved-image");
 const c=d.querySelector(".canvas");
 return {cssViewport:win.innerWidth,docScrollWidth:d.documentElement.scrollWidth,overflow:d.documentElement.scrollWidth>win.innerWidth+1,
   canvas:box(".canvas"),approvedImageLoaded:img?.complete&&img?.naturalWidth===1536&&img?.naturalHeight===1024,
   searchInput:box("#oemInput"),searchButton:box("#searchButton"),
   searchIcon:box(".nav-search"),cartIcon:box(".nav-cart"),hubLink:box(".nav-hub"),
   searchUsable44px:box("#searchButton")?.height>=44&&box("#oemInput")?.height>=44,
   cartUsable44px:box(".nav-cart")?.width>=44&&box(".nav-cart")?.height>=44,
   productVisible:!d.querySelector("#product")?.classList.contains("hidden"),
   oem:d.querySelector("#productOem")?.textContent,
   cartTotal:d.querySelector("#cartTotal")?.textContent,
   checkoutDisabled:d.querySelector("#telegramButton")?.disabled===true,
   stageWarningVisible:!!d.querySelector("aside[style*='position:fixed']")};
}
function report(w){const val=collect(w);document.getElementById("r"+w).textContent=JSON.stringify(val,null,2);}
for(const w of [360,390,430])document.getElementById("f"+w).addEventListener("load",()=>report(w));
async function run(w){
 const f=document.getElementById("f"+w),d=f.contentDocument;
 const inp=d.querySelector("#oemInput"),form=d.querySelector("#searchForm");
 if(!inp||!form)return {error:"search controls unavailable"};
 inp.value="417300574";form.requestSubmit();
 const until=Date.now()+15000;
 while(Date.now()<until){if(!d.querySelector("#product").classList.contains("hidden"))break;await new Promise(r=>setTimeout(r,200));}
 const visible=!d.querySelector("#product").classList.contains("hidden");
 const btn=d.querySelector('[data-offer-key="warehouse:1"]');
 if(visible&&btn)btn.click();
 await new Promise(r=>setTimeout(r,400));
 const data=collect(w);data.mockSearchPass=visible && data.oem==="417300574";
 data.testWarehouseAddPass=d.querySelector("#cartPanel").classList.contains("open")
 && String(data.cartTotal).includes("34");
 data.checkoutIsDisabled=d.querySelector("#telegramButton").disabled;
 document.getElementById("r"+w).textContent=JSON.stringify(data,null,2);
 return data;
}
document.getElementById("run").addEventListener("click",()=>{for(const w of [360,390,430])run(w).catch(e=>{document.getElementById("r"+w).textContent=String(e)})});
</script></body></html>"""


def attach_mobile_qa(app: FastAPI):
    @app.get("/web2/stage04/mobile-qa",include_in_schema=False)
    def mobile_qa():
        return HTMLResponse(MOBILE_DIAGNOSTIC_HTML,headers={"Cache-Control":"no-store"})
