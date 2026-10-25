"""SHA-locked POWERSPORTS stage04 routes, isolated staging only."""
from __future__ import annotations
import hashlib
import hmac
import io
import os
import tempfile
from pathlib import Path
from zipfile import BadZipFile, ZipFile
from fastapi import FastAPI, HTTPException, File, Form, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response

ARCHIVE_SHA = "37da3250803462f0a846d29517da20634cd19cd0aee2e89abd8b23a8ce58d508"
ARCHIVE_SIZE = 3186352
ROOT = "EXTREMIZER_POWERSPORTS_WEB_STAGE_02_OEM_WEB2/"
EXPECTED = {
    "index.html": "7a90069e58a6b7dff42ff1177d7a4d2f8766bc558a439cbb4ea60c55994c7492",
    "styles.css": "0c0fea80e48e43c97a1ecfc20ff0447228146e21bac5db08cadebbb60d93284d",
    "assets/approved_powersports_1536x1024.png": "e4e75353fe816dc11fc75391b15de7aef60ff221b8ca10ec66d90ad08dc2e4b1",
}
STAGING_ZIP_PATH = Path("/stage04-data/EXTREMIZER_POWERSPORTS_WEB_STAGE_02_OEM_WEB2.zip")


def _read_archive() -> dict[str, bytes]:
    if not STAGING_ZIP_PATH.is_file():
        raise HTTPException(503, "STAGE02_ZIP_NOT_MOUNTED")
    if STAGING_ZIP_PATH.stat().st_size != ARCHIVE_SIZE:
        raise HTTPException(503, "STAGE02_ZIP_SIZE_MISMATCH")
    if hashlib.sha256(STAGING_ZIP_PATH.read_bytes()).hexdigest() != ARCHIVE_SHA:
        raise HTTPException(503, "STAGE02_ZIP_HASH_MISMATCH")
    try:
        with ZipFile(STAGING_ZIP_PATH) as z:
            data = {key: z.read(ROOT + key) for key in EXPECTED}
    except (BadZipFile, KeyError, OSError):
        raise HTTPException(503, "STAGE02_ZIP_UNREADABLE") from None
    if any(hashlib.sha256(data[key]).hexdigest() != val for key, val in EXPECTED.items()):
        raise HTTPException(503, "STAGE02_ASSET_SHA_MISMATCH")
    return data


def attach_stage04(app: FastAPI) -> None:
    @app.get("/web2/stage04", include_in_schema=False)
    def page() -> HTMLResponse:
        html = _read_archive()["index.html"].decode("utf-8")
        html = html.replace('href="styles.css"', 'href="/web2/stage04/styles.css"')
        html = html.replace('src="assets/approved_powersports_1536x1024.png"',
                            'src="/web2/stage04/assets/approved_powersports_1536x1024.png"')
        html = html.replace("</head>", '<meta name="robots" content="noindex,nofollow"></head>')
        html = html.replace("<body>", '<body><aside style="position:fixed;bottom:8px;left:8px;z-index:999;background:#BFF803;color:#080808;font:700 13px Arial;padding:7px 10px">ISOLATED MOCK DATA — НЕ РЕАЛЬНЫЕ ЦЕНЫ / ЗАКАЗЫ ОТКЛЮЧЕНЫ</aside>')
        html = html.replace('<script defer="defer" src="/web2-static/app.js"></script>',
            '<script defer="defer" src="/web2-static/app.js"></script>'
            '<script>document.addEventListener("DOMContentLoaded",function(){'
            'var b=document.getElementById("telegramButton");'
            'if(b){b.disabled=true;b.textContent="Оформление отключено — тестовый стенд";}});</script>')
        return HTMLResponse(html, headers={"Cache-Control":"no-store","X-Extremizer-Asset-Source":"SHA-LOCKED-ZIP"})

    @app.get("/web2/stage04/styles.css", include_in_schema=False)
    def css() -> Response:
        return Response(_read_archive()["styles.css"], media_type="text/css", headers={"Cache-Control":"no-store"})

    @app.get("/web2/stage04/assets/approved_powersports_1536x1024.png", include_in_schema=False)
    def image() -> Response:
        return Response(_read_archive()["assets/approved_powersports_1536x1024.png"], media_type="image/png",
            headers={"Cache-Control":"no-store","X-Content-SHA256":EXPECTED["assets/approved_powersports_1536x1024.png"]})

    @app.get("/web2/stage04/asset-gate", include_in_schema=False)
    def gate() -> JSONResponse:
        _read_archive()
        return JSONResponse({"status":"READY","archive_sha256":ARCHIVE_SHA,
          "image_sha256":EXPECTED["assets/approved_powersports_1536x1024.png"],
          "synthetic_oem_only":True,"production_data":False},headers={"Cache-Control":"no-store"})

    @app.get("/web2/stage04/upload", include_in_schema=False)
    def upload_page_disabled():
        raise HTTPException(410, "STAGE04_UPLOAD_CLOSED")

    @app.post("/web2/stage04/upload", include_in_schema=False)
    async def upload_asset_disabled():
        raise HTTPException(410, "STAGE04_UPLOAD_CLOSED")
