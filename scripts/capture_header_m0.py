"""Capture WEB2 M0 original-assets + local Manrope evidence. No production endpoints."""
from __future__ import annotations
import argparse, hashlib, json, os, pathlib, threading, zipfile
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from playwright.sync_api import sync_playwright

SHA = {
    "wordmark.png": "46c3c4295313c0737094cc1b45a48fd64ea18cf3f971d48110640f64cd3a662f",
    "search.png": "26d942c4f05047ae12aab0de024dbc9723a8179789385c16649d13df2b89d2d8",
    "cart.png": "920a3463a55a0b0dd3ea21155b3f3ba185ed60df066cec40bb3591e15af2a28b",
    "private.png": "c40e3950728ae670d1ca7c1a02d507a1a5d5eaa7f4b7aad09c4396b4b928cb3c",
}
SOURCE_MEMBERS = {
    "wordmark.png": "Extremizer_transparent(5).png",
    "search.png": "поиск 777!!! для шапки(1).png",
    "cart.png": "корзина для шапки(1).png",
    "private.png": "личка 77 для шапки(1).png",
}

def run(bundle:pathlib.Path,out:pathlib.Path):
    root=pathlib.Path(__file__).resolve().parents[1]
    out.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        assert all(not pathlib.PurePosixPath(name).is_absolute() and ".." not in pathlib.PurePosixPath(name).parts for name in names), "unsafe asset bundle path"
        assert sorted(names) == sorted([*SOURCE_MEMBERS.values(), "ASSET_MANIFEST.json"]), "unexpected asset bundle members"
        manifest = json.loads(z.read("ASSET_MANIFEST.json").decode("utf-8"))
        files = {}
        for output_name, source_name in SOURCE_MEMBERS.items():
            raw = z.read(source_name)
            digest = hashlib.sha256(raw).hexdigest()
            assert digest == SHA[output_name], output_name
            assert manifest["files"][source_name]["sha256"] == digest, source_name
            files[output_name] = raw
    assert (root/"web2/fonts/Manrope-variable.ttf").is_file()
    os.environ.update({
        "EXTREMIZER_HEADER_WORDMARK_PATH":str((out/"wordmark.png").resolve()),
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
                    const rect = (el) => {
                        const r = el.getBoundingClientRect();
                        return {left:r.left, right:r.right, width:r.width, height:r.height};
                    };
                    const separators = [...document.querySelectorAll('.gh-separator')];
                    return {
                        viewport:innerWidth,
                        overflow:document.documentElement.scrollWidth>innerWidth,
                        manrope:document.fonts.check('500 24px Manrope'),
                        fontFamily:getComputedStyle(document.querySelector('.gh-life')).fontFamily,
                        labelSize:getComputedStyle(document.querySelector('.gh-life')).fontSize,
                        labelWeight:getComputedStyle(document.querySelector('.gh-life')).fontWeight,
                        l1Token:getComputedStyle(document.querySelector('.global-header-v11')).getPropertyValue('--gh-l1').trim(),
                        faces:[...document.fonts].filter(f=>f.family==='Manrope').map(f=>f.status),
                        elements:items.map(el=>{const r=el.getBoundingClientRect(); const img=el.querySelector('img');return {
                            name:el.className,x:r.left,right:r.right,w:r.width,h:r.height,
                            imgLoaded:img ? img.complete&&img.naturalWidth>0:null,
                            imgW:img ? img.naturalWidth:null
                        };}),
                        l1Gaps:separators.map((sep) => {
                            const previous = sep.previousElementSibling;
                            const next = sep.nextElementSibling;
                            const s = rect(sep);
                            return {
                                left: previous ? Math.round((s.left - rect(previous).right) * 100) / 100 : null,
                                right: next ? Math.round((rect(next).left - s.right) * 100) / 100 : null
                            };
                        })
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
                assert logo.evaluate("(el) => el.complete && el.naturalWidth > 0 && !el.hidden"), "approved PNG wordmark must render"
                expected_focus = ([".brand-slot",".gh-private",".gh-search",".gh-life",".gh-cart",".gh-hub"]
                                  if width <= 430 else
                                  [".brand-slot",".gh-search",".gh-life",".gh-cart",".gh-hub",".gh-private"])
                actual_focus=[]
                page.locator("body").click(position={"x":1,"y":1})
                for selector in expected_focus:
                    page.keyboard.press("Tab")
                    matches=page.evaluate("""selector => document.activeElement === document.querySelector(selector)""",selector)
                    assert matches, f"Tab focus mismatch at {width}px: expected {selector}"
                    actual_focus.append(selector)
                data["focus_order"] = actual_focus
                activation_results=[]
                for selector in expected_focus:
                    for key in (("Enter","Space") if selector==".gh-cart" else ("Enter",)):
                        probe=browser.new_page(viewport={"width":width,"height":820})
                        probe.goto(f"http://127.0.0.1:{port}/web2",wait_until="networkidle")
                        probe.locator(selector).focus()
                        assert probe.evaluate("s => document.activeElement === document.querySelector(s)",selector)
                        if selector==".gh-cart":
                            probe.keyboard.press(key)
                            passed=probe.locator("#cartPanel").get_attribute("aria-hidden")=="false"
                        elif selector==".gh-search":
                            probe.keyboard.press(key)
                            probe.wait_for_url("**/web2#oemInput",timeout=5000)
                            passed=probe.url.split("#",1)[-1]=="oemInput"
                        else:
                            target={".brand-slot":"/web2",".gh-life":"/web2/life",
                                    ".gh-hub":"/web2/hub",".gh-private":"/web2/messages"}[selector]
                            with probe.expect_navigation(url=f"**{target}",wait_until="domcontentloaded",timeout=5000):
                                probe.keyboard.press(key)
                            passed=probe.url.split("?",1)[0].split("#",1)[0].endswith(target)
                        assert passed, f"keyboard activation FAIL {width}px {selector} {key}"
                        activation_results.append({"element":selector,"key":key,"activated":True})
                        probe.close()
                data["keyboard_activation"]=activation_results

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
