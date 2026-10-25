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
    def upload_page() -> HTMLResponse:
        return HTMLResponse('''<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="robots" content="noindex,nofollow"><title>EXTREMIZER STAGE04 asset upload</title><style>body{background:#080808;color:#fff;font:16px Arial;padding:20px;max-width:780px;margin:auto}input,button{font:16px Arial;margin:8px 0;padding:10px}button{background:#BFF803;border:0;cursor:pointer}p{color:#A5A5A5}</style><h2>POWERSPORTS — загрузка утверждённого ZIP</h2><p>Только изолированный staging. Принимается исключительно побайтово идентичный EXTREMIZER_POWERSPORTS_WEB_STAGE_02_OEM_WEB2.zip. Неверный SHA-256 будет отвергнут.</p><form method="post" enctype="multipart/form-data"><label>STAGE04 upload token <input type="password" name="token" required autocomplete="off"></label><p><input type="file" name="asset" accept=".zip" required></p><button type="submit">Загрузить утверждённый ZIP</button></form></html>''', headers={"Cache-Control":"no-store"})

    @app.post("/web2/stage04/upload", include_in_schema=False)
    async def upload_asset(token: str = Form(...), asset: UploadFile = File(...)) -> HTMLResponse:
        expected_token = os.environ.get("EXTREMIZER_STAGE04_UPLOAD_TOKEN","")
        if not expected_token or not hmac.compare_digest(token,expected_token):
            raise HTTPException(403,"UPLOAD_AUTH_REQUIRED")
        data = await asset.read(ARCHIVE_SIZE+1)
        if len(data)!=ARCHIVE_SIZE or hashlib.sha256(data).hexdigest()!=ARCHIVE_SHA:
            raise HTTPException(422,"APPROVED_STAGE02_ZIP_SHA_OR_SIZE_MISMATCH")
        try:
            with ZipFile(io.BytesIO(data)) as z:
                for key,digest in EXPECTED.items():
                    if hashlib.sha256(z.read(ROOT+key)).hexdigest()!=digest:
                        raise HTTPException(422,"APPROVED_STAGE02_ASSET_SHA_MISMATCH")
        except (BadZipFile,KeyError):
            raise HTTPException(422,"APPROVED_STAGE02_ZIP_INVALID") from None
        if not STAGING_ZIP_PATH.parent.is_dir():
            raise HTTPException(503,"STAGE04_VOLUME_NOT_MOUNTED")
        temp=None
        try:
            with tempfile.NamedTemporaryFile(dir=STAGING_ZIP_PATH.parent,prefix=".stage04-",delete=False) as f:
                temp=f.name
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp,STAGING_ZIP_PATH)
        finally:
            if temp and os.path.exists(temp):
                os.unlink(temp)
        _read_archive()
        return HTMLResponse('''<!doctype html><html lang="ru"><meta name="viewport" content="width=device-width,initial-scale=1"><body style="background:#080808;color:#BFF803;font:18px Arial;padding:36px"><h2>SHA-256 PASS — исходник установлен.</h2><p><a style="color:#BFF803" href="/web2/stage04">Открыть POWERSPORTS STAGE04</a></p></body></html>''',headers={"Cache-Control":"no-store"})
