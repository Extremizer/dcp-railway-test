"""Capture WEB2 M0 original-assets + local Manrope evidence. No production endpoints."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, threading, zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

SHA = {
    "wordmark.svg":"0651e2d482c496ac135497910506b1ae7d0672d55d6e47f9cf7f87f87b3c5afa",
    "search.png":"26d942c4f05047ae12aab0de024dbc9723a8179789385c16649d13df2b89d2d8",
    "cart.png":"920a3463a55a0b0dd3ea21155b3f3ba185ed60df066cec40bb3591e15af2a28b",
    "private.png":"c40e3950728ae670d1ca7c1a02d507a1a5d5eaa7f4b7aad09c4396b4b928cb3c",
}

def run(bundle:pathlib.Path,out:pathlib.Path):
    root=pathlib.Path(__file__).resolve().parents[1]
    out.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(bundle) as z:
        assert sorted(z.namelist()) == sorted([*SHA,"manifest.json"]), "unexpected asset bundle members"
        files={k:z.read(k) for k in SHA}
        for name,raw in files.items():
            assert hashlib.sha256(raw).hexdigest() == SHA[name], name
    assert (root/"web2/fonts/Manrope-variable.ttf").is_file()
    os.environ.update({
        "EXTREMIZER_HEADER_WORDMARK_PATH":str((out/"wordmark.svg").resolve()),
        "EXTREMIZER_HEADER_SEARCH_PATH":str((out/"search.png").resolve()),
        "EXTREMIZER_HEADER_CART_PATH":str((out/"cart.png").resolve()),
        "EXTREMIZER_HEADER_PRIVATE_PATH":str((out/"private.png").resolve()),
    })
    for name,raw in files.items(): (out/name).write_bytes(raw)
    import sys
    sys.path.insert(0,str(root))
    from fastapi import FastAPI
    from web2_ui_routes import attach_web2_ui
    import uvicorn
    app=FastAPI()
    attach_web2_ui(app)
    import socket
    s=socket.socket();s.bind(("127.0.0.1",0));port=s.getsockname()[1];s.close()
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="warning"))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    import time
    for _ in range(80):
        if server.started:break
        time.sleep(.1)
    assert server.started
    try:
        with sync_playwright() as p:
            browser=p.chromium.launch(headless=True)
            for width in (1280,360,390,430):
                page=browser.new_page(viewport={"width":width,"height":820}, device_scale_factor=1)
                responses={}
                page.on("response",lambda r:responses.setdefault(r.url.split("/")[-1],r.status))
                page.goto(f"http://127.0.0.1:{port}/web2",wait_until="networkidle")
                page.evaluate("document.fonts.ready")
                data=page.evaluate("""() => {
                    const nav=document.querySelector('.global-header-nav');
                    const items=[...nav.children];
                    return {
                        viewport:innerWidth,
                        overflow:document.documentElement.scrollWidth>innerWidth,
                        manrope:document.fonts.check('500 24px Manrope'),
                        fontFamily:getComputedStyle(document.querySelector('.gh-life')).fontFamily,
                        labelSize:getComputedStyle(document.querySelector('.gh-life')).fontSize,
                        labelWeight:getComputedStyle(document.querySelector('.gh-life')).fontWeight,
                        faces:[...document.fonts].filter(f=>f.family==='Manrope').map(f=>f.status),
                        elements:items.map(el=>{const r=el.getBoundingClientRect(); const img=el.querySelector('img');return {
                            name:el.className,x:r.left,right:r.right,w:r.width,h:r.height,
                            imgLoaded:img ? img.complete&&img.naturalWidth>0:null,
                            imgW:img ? img.naturalWidth:null
                        };})
                    };
                }""")
                assert data["manrope"] and "loaded" in data["faces"]
                assert not data["overflow"]
                assert "Manrope" in data["fontFamily"] and data["labelWeight"]=="500"
                if width==1280:
                    assert data["labelSize"]=="24px", "desktop approved Manrope size"
                actions=[e for e in data["elements"] if "gh-action" in e["name"]]
                assert len(actions)==3 and all(e["w"]>=44 and e["h"]>=44 for e in actions), "44x44 touch target"
                assert all(responses.get(name)==200 for name in ("wordmark","search","cart","private")), responses
                assert sum(x["imgLoaded"] is True for x in data["elements"])==3, "all three navigation icons must render"
                logo=page.locator("header.global-header-v11 .brand-logo")
                assert logo.evaluate("(el) => el.complete && el.naturalWidth > 0 && !el.hidden"), "approved SVG wordmark must render"
                page.locator("header.global-header-v11").screenshot(path=str(out/f"header_{width}.png"))
                (out/f"header_{width}_metrics.json").write_text(json.dumps(data,ensure_ascii=False,indent=2))
                page.close()
            browser.close()
    finally:
        server.should_exit=True
    print("M0 four viewports, Manrope, assets 4/4: PASS")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("bundle",type=pathlib.Path)
    parser.add_argument("--out",type=pathlib.Path,default=pathlib.Path("m0-browser-evidence"))
    args=parser.parse_args()
    run(args.bundle,args.out)
