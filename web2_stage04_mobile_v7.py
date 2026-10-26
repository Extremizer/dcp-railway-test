"""EXTREMIZER MOBILE HEADER VARIANT 7 — original, SHA-locked icon sources.
Isolated WEB2 staging ONLY. Leaves /web2/stage04, desktop and prod unchanged.
"""
from __future__ import annotations
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import Response, HTMLResponse, JSONResponse
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile, BadZipFile
from PIL import Image
from web2_stage04_gate import _read_archive
from web2_stage04_mobile_candidate import _make_candidate
from web2_stage04_mobile_qa import MOBILE_DIAGNOSTIC_HTML
import hashlib
import hmac
import os
import tempfile

FILE = Path("/stage04-data/EXTREMIZER_MOBILE_HEADER_V7_APPROVED_ICONS_SOURCE_LOCK_20261025.zip")
ZIP_SHA = "b5966492b3a75bc44210599c855464f52930697b93618cc10ddda8c54d6a4292"
ZIP_SIZE = 563948
SOURCE = {
 "search": ("originals/поиск 777!!! для шапки(1).png",
            "26d942c4f05047ae12aab0de024dbc9723a8179789385c16649d13df2b89d2d8"),
 "cart": ("originals/корзина для шапки(1).png",
          "920a3463a55a0b0dd3ea21155b3f3ba185ed60df066cec40bb3591e15af2a28b"),
 "messages": ("originals/личка 77 для шапки(1).png",
              "c40e3950728ae670d1ca7c1a02d507a1a5d5eaa7f4b7aad09c4396b4b928cb3c"),
}


def _verified_zip(blob: bytes) -> dict[str, bytes]:
    if len(blob) != ZIP_SIZE or hashlib.sha256(blob).hexdigest() != ZIP_SHA:
        raise HTTPException(503, "V7_CANONICAL_ARCHIVE_SHA_OR_SIZE_MISMATCH")
    try:
        with ZipFile(BytesIO(blob)) as z:
            files={key:z.read(item[0]) for key,item in SOURCE.items()}
    except (BadZipFile, KeyError, OSError):
        raise HTTPException(503,"V7_CANONICAL_ARCHIVE_UNREADABLE") from None
    for key,(_,checksum) in SOURCE.items():
        if hashlib.sha256(files[key]).hexdigest()!=checksum:
            raise HTTPException(503, "V7_CANONICAL_ICON_SHA_MISMATCH:"+key)
    return files


def _icons() -> dict[str, bytes]:
    if not FILE.is_file():
        raise HTTPException(503, "V7_CANONICAL_ASSET_ZIP_NOT_UPLOADED")
    return _verified_zip(FILE.read_bytes())


def _wordmark() -> bytes:
    """Pixel crop of approved STAGE02 wordmark source, never redraw."""
    src=_read_archive()["assets/approved_powersports_1536x1024.png"]
    im=Image.open(BytesIO(src)).convert("RGB")
    if im.size != (1536,1024):
        raise HTTPException(503,"V7_WORDMARK_SOURCE_SIZE_MISMATCH")
    out=BytesIO()
    im.crop((53,25,322,59)).save(out,format="PNG")
    return out.getvalue()


def _html() -> str:
    _icons()
    html=_make_candidate()
    start=html.find('<header aria-label="Глобальная навигация EXTREMIZER"')
    end=html.find("</header>",start)+len("</header>")
    if start<0 or end<len("</header>"):
        raise HTTPException(503,"V7_HEADER_ANCHOR_MISSING")
    header='''<header class="v7-header" aria-label="EXTREMIZER — мобильная шапка Variant 7">
      <a class="v7-cell v7-life" href="/web2/life">LIFE</a>
      <a class="v7-cell v7-brand" href="/web2" aria-label="EXTREMIZER — главная"><img src="/web2/stage04/mobile-v7/asset/wordmark" alt="EXTREMIZER"/></a>
      <a class="v7-cell v7-hub" href="/web2/hub">МОЙ ХАБ</a>
      <a class="v7-cell v7-search nav-search" href="#oemInput" aria-label="Поиск OEM"><img src="/web2/stage04/mobile-v7/asset/search" alt=""/></a>
      <button class="v7-cell v7-cart nav-cart" type="button" id="cartButton" aria-label="Открыть корзину"><img src="/web2/stage04/mobile-v7/asset/cart" alt=""/><span id="cartCount"></span><span id="cartButtonTotal" hidden></span></button>
      <a class="v7-cell v7-messages nav-messages" href="/web2/messages" aria-label="Личные сообщения"><img src="/web2/stage04/mobile-v7/asset/messages" alt=""/></a>
    </header>'''
    html=html[:start]+header+html[end:]
    html=html.replace("</head>", '<link rel="stylesheet" href="/web2-static/stage04-mobile-v7.css"></head>',1)
    html=html.replace("MOBILE CANDIDATE / MOCK OEM / ЗАКАЗЫ ОТКЛЮЧЕНЫ",
                      "VARIANT 7 REVIEW / MOCK OEM / ЗАКАЗЫ ОТКЛЮЧЕНЫ")
    return html


def attach_mobile_v7(app: FastAPI)->None:
    @app.get("/web2/stage04/mobile-v7",include_in_schema=False)
    def page():
        return HTMLResponse(_html(),headers={"Cache-Control":"no-store","X-Extremizer-Visual-State":"V7_OWNER_ICON_APPROVED_LAYOUT_HOLD"})

    @app.get("/web2/stage04/mobile-v7/asset/{kind}",include_in_schema=False)
    def asset(kind:str):
        if kind=="wordmark":
            blob=_wordmark()
        elif kind in SOURCE:
            blob=_icons()[kind]
        else:
            raise HTTPException(404,"V7_ASSET_UNSUPPORTED")
        return Response(blob,media_type="image/png",headers={"Cache-Control":"no-store",
            "X-Content-SHA256":hashlib.sha256(blob).hexdigest()})

    @app.get("/web2/stage04/mobile-v7/asset-gate",include_in_schema=False)
    def gate():
        _icons()
        return JSONResponse({"status":"READY","zip_sha256":ZIP_SHA,
            "icons":{k:s[1] for k,s in SOURCE.items()},
            "state":"OWNER_APPROVED_ICONS_ONLY","mock_oem":True,"orders_enabled":False},
            headers={"Cache-Control":"no-store"})

    @app.get("/web2/stage04/mobile-v7/qa",include_in_schema=False)
    def qa():
        _icons()
        html=MOBILE_DIAGNOSTIC_HTML.replace('src="/web2/stage04"','src="/web2/stage04/mobile-v7"')
        html=html.replace('Responsive QA (не утверждённый дизайн)','Variant 7 / owner-approved icons / layout review')
        return HTMLResponse(html,headers={"Cache-Control":"no-store"})

    @app.get("/web2/stage04/mobile-v7/upload",include_in_schema=False)
    def upload_page():
        if FILE.is_file():
            raise HTTPException(410,"V7_ASSET_UPLOAD_ALREADY_COMPLETE")
        return HTMLResponse('''<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1">
        <meta name="robots" content="noindex,nofollow"><body style="font:16px Arial;color:#eee;background:#080808;padding:24px">
        <h2>EXTREMIZER — Variant 7 / оригинальные иконки</h2><p>Только isolated staging, строго фиксированный ZIP / SHA-256.</p>
        <form enctype="multipart/form-data" method="post">
        <label>Ключ staging <input type="password" name="token" required></label>
        <p><input type="file" name="asset" accept=".zip" required></p>
        <button type="submit" style="padding:12px;background:#BFF803">Загрузить три канонические иконки</button>
        </form></body></html>''',headers={"Cache-Control":"no-store"})

    @app.post("/web2/stage04/mobile-v7/upload",include_in_schema=False)
    async def upload(token:str=Form(...),asset:UploadFile=File(...)):
        secret=os.environ.get("EXTREMIZER_STAGE04_UPLOAD_TOKEN","")
        if not secret or not hmac.compare_digest(secret,token):
            raise HTTPException(403,"V7_UPLOAD_AUTH_REQUIRED")
        if FILE.is_file():
            raise HTTPException(410,"V7_ASSET_UPLOAD_ALREADY_COMPLETE")
        data=await asset.read(ZIP_SIZE+1)
        _verified_zip(data)
        if not FILE.parent.is_dir():
            raise HTTPException(503,"V7_STAGING_VOLUME_NOT_MOUNTED")
        temp=None
        try:
            with tempfile.NamedTemporaryFile(dir=FILE.parent,prefix=".mobile-v7-",delete=False) as f:
                temp=f.name
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp,FILE)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        _icons()
        return HTMLResponse('<body style="background:#080808;color:#BFF803;font:20px Arial;padding:30px"><h2>SHA-256 PASS</h2><a href="/web2/stage04/mobile-v7" style="color:#BFF803">Открыть Variant 7</a></body>')
