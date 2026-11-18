"""T1 one-owner LIVE TEST bridge: RAM carts ONLY, no Orders/SQLite/Finance.

The existing WEB handoff bridge secret protects server-to-server calls and a
one-time browser link. Deliberately opt-in via EXTREMIZER_T1_PILOT_ENABLED=1.
No real checkout endpoints. Test RAM data expires on service restart.
"""
from __future__ import annotations
import hashlib
import hmac
import os
import secrets
import threading
import time
from typing import Any
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

SCOPES=("usa","rf:1","rf:2","rf:3")
COOKIE="__Host-ext-t1-pilot"
_LOCK=threading.RLock()
_ROWS={s:{"revision":0,"items":[]} for s in SCOPES}
_SESSIONS={}
_USED_TICKETS=set()
_MAX_ROWS=30
_MAX_BYTES=80000
_TTL=60*60*4


def _deny(reason="PILOT_ACCESS_DENIED",code=403):
    raise HTTPException(code,{"code":reason})


def _secret():
    value=os.getenv("EXTREMIZER_WEB_HANDOFF_BRIDGE_KEY","")
    if len(value)<32:
        raise RuntimeError("PILOT_WEB_BRIDGE_SECRET_MISSING")
    return value


def _owner():
    value=os.getenv("EXTREMIZER_T1_PILOT_OWNER_ID","")
    if not value.isdecimal() or int(value)<=0:
        raise RuntimeError("PILOT_OWNER_ID_MISSING")
    return int(value)


def _server(request:Request):
    auth=request.headers.get("authorization","")
    expected="Bearer "+_secret()
    if not hmac.compare_digest(auth,expected):
        _deny("BOT_BRIDGE_AUTH_REQUIRED",401)


def _session(request:Request,*,write=False):
    if request.url.scheme!="https":
        _deny("HTTPS_REQUIRED")
    cookie=request.cookies.get(COOKIE,"")
    if not cookie or len(cookie)<32:
        _deny("LINKED_SESSION_REQUIRED",401)
    key=hashlib.sha256(cookie.encode("utf-8")).hexdigest()
    with _LOCK:
        record=_SESSIONS.get(key)
    if record is None or record["expires"]<=time.time():
        _deny("SESSION_EXPIRED",401)
    if write:
        origin=request.headers.get("origin","")
        if origin != str(request.base_url).rstrip("/"):
            _deny("SAME_ORIGIN_REQUIRED")
        token=request.headers.get("x-ext-t1-csrf","")
        if not hmac.compare_digest(token,record["csrf"]):
            _deny("CSRF_REQUIRED")
    return record


def _scope(value):
    if value not in SCOPES or type(value) is not str:
        _deny("UNKNOWN_CART_SCOPE",422)
    return value


def _number(value,lo,hi):
    if type(value) is not int or not lo<=value<=hi:
        _deny("INVALID_CART_NUMBER",422)
    return value


def _row(item:Any,scope:str,*,trusted_bot:bool,old_by_key:dict):
    if not isinstance(item,dict):
        _deny("CART_ITEM_NOT_OBJECT",422)
    key=item.get("key")
    brand=item.get("manufacturer")
    oem=item.get("oem")
    source=item.get("offer_source")
    wid=item.get("warehouse_id")
    if (not isinstance(key,str) or len(key)>400 or len(key)<4
        or not isinstance(brand,str) or len(brand)>100
        or not isinstance(oem,str) or not 2<=len(oem)<=100
        or source not in ("usa","warehouse")):
        _deny("INVALID_CART_IDENTITY",422)
    if (scope=="usa" and (source!="usa" or wid is not None)
        or scope!="usa" and (source!="warehouse" or wid!=int(scope[3:]))):
        _deny("CROSS_CART_ITEM_REJECTED",422)
    expected=f"{brand}|{oem}|{'usa' if scope=='usa' else 'warehouse:'+scope[3:]}"
    if key!=expected:
        _deny("CART_KEY_CANONICAL_REQUIRED",422)
    qty=_number(item.get("qty"),1,99)
    old=old_by_key.get(key)
    if old and (old["manufacturer"]!=brand or old["oem"]!=oem or
                old["offer_source"]!=source or old["warehouse_id"]!=wid):
        _deny("CART_IDENTITY_IMMUTABLE",422)
    if not trusted_bot and ("tg_raw" in item or "tg_key" in item):
        _deny("BROWSER_FORGED_BOT_METADATA",403)
    unit=item.get("unit_rub")
    if old and not trusted_bot:
        unit=old["unit_rub"]
    else:
        unit=_number(unit,0,100000000)
    available=item.get("available_quantity")
    if type(available) is not int or available<qty or available>100000:
        available=qty if source=="warehouse" else None
    def safe_text(k,maxlen=200):
        value=item.get(k,"")
        return value[:maxlen] if isinstance(value,str) else ""
    result={
        "key":key,"manufacturer":brand,"oem":oem,"offer_source":source,
        "warehouse_id":wid,"qty":qty,"unit_rub":unit,
        "available_quantity":available if source=="warehouse" else None,
        "requested_oem":safe_text("requested_oem",100) or oem,
        "name":safe_text("name",200),
        "source_label":safe_text("source_label",80),
        "price_status":"PILOT_PREVIEW_ONLY_NOT_CHECKOUT",
    }
    if trusted_bot:
        tk=item.get("tg_key")
        raw=item.get("tg_raw")
        if tk is not None or raw is not None:
            if (not isinstance(tk,str) or len(tk)>400 or
                not isinstance(raw,dict) or len(str(raw))>8000):
                _deny("UNSAFE_BOT_CART_METADATA",422)
            result["tg_key"]=tk
            result["tg_raw"]=raw
    elif old and "tg_raw" in old:
        result["tg_key"]=old["tg_key"]
        result["tg_raw"]=old["tg_raw"]
    return result


def _clean(scope,items,*,trusted_bot=False):
    if not isinstance(items,list) or len(items)>_MAX_ROWS:
        _deny("PILOT_TOO_MANY_CART_LINES",422)
    old={x["key"]:x for x in _ROWS[scope]["items"]}
    result=[]
    seen=set()
    for item in items:
        row=_row(item,scope,trusted_bot=trusted_bot,old_by_key=old)
        if row["key"] in seen:
            _deny("DUPLICATE_OFFER",409)
        seen.add(row["key"])
        result.append(row)
    return sorted(result,key=lambda x:x["key"])


def _view(*,private=False):
    with _LOCK:
        return {scope:{"revision":row["revision"],
            "items":[
                dict(item) if private else
                {k:v for k,v in item.items() if k not in ("tg_raw","tg_key")}
                for item in row["items"]]}
            for scope,row in _ROWS.items()}


def _update(scope,base_revision,items,*,trusted_bot):
    _scope(scope)
    _number(base_revision,0,99999999)
    if len(str(items))>_MAX_BYTES:
        _deny("CART_PAYLOAD_TOO_LARGE",422)
    with _LOCK:
        current=_ROWS[scope]
        if current["revision"]!=base_revision:
            _deny("CART_REVISION_CONFLICT",409)
        normalized=_clean(scope,items,trusted_bot=trusted_bot)
        if normalized!=current["items"]:
            current["items"]=normalized
            current["revision"]+=1
        return {"revision":current["revision"],"items":(
            [dict(x) for x in current["items"]] if trusted_bot else
            [{k:v for k,v in x.items() if k not in ("tg_raw","tg_key")}
             for x in current["items"]])}


def _body(payload:Any,required:set):
    if not isinstance(payload,dict) or set(payload)!=required:
        _deny("STRICT_ENVELOPE_REQUIRED",422)
    return payload


def attach(app:FastAPI)->None:
    if os.getenv("EXTREMIZER_T1_PILOT_ENABLED","")!="1":
        return
    secret=_secret()
    owner=_owner()
    router=APIRouter()

    @router.get("/web2/t1-cart-link")
    def link(ticket:str,request:Request):
        if request.url.scheme!="https":
            _deny("HTTPS_LINK_ONLY")
        parts=ticket.split(".")
        if len(parts)!=4:
            _deny("MALFORMED_LINK")
        user,expiry,nonce,signature=parts
        if (user!=str(owner) or not expiry.isdecimal()
            or len(nonce)!=24 or len(signature)!=64
            or not all(c in "0123456789abcdef" for c in nonce+signature)
            or int(expiry)<int(time.time())
            or int(expiry)>int(time.time())+900):
            _deny("EXPIRED_OR_FOREIGN_LINK")
        msg=f"EXT-T1-WEB-LINK-V1:{user}:{expiry}:{nonce}".encode()
        expected=hmac.new(secret.encode(),msg,hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature,expected):
            _deny("INVALID_LINK_SIGNATURE")
        with _LOCK:
            if nonce in _USED_TICKETS:
                _deny("LINK_USED")
            _USED_TICKETS.add(nonce)
            raw=secrets.token_urlsafe(32)
            _SESSIONS[hashlib.sha256(raw.encode()).hexdigest()]={
                "owner":owner,"csrf":secrets.token_urlsafe(32),
                "expires":time.time()+_TTL}
        response=RedirectResponse("/web2?tgweb=1",status_code=303)
        response.set_cookie(COOKIE,raw,max_age=_TTL,secure=True,
                            httponly=True,samesite="lax",path="/")
        return response

    @router.get("/api/t1/pilot/carts")
    def browser_read(request:Request):
        session=_session(request)
        if session["owner"]!=owner:
            _deny("FOREIGN_SESSION")
        return {"test_only":True,"checkout_allowed":False,
                "csrf":session["csrf"],"scopes":_view()}

    @router.post("/api/t1/pilot/carts/{scope}")
    async def browser_write(scope:str,request:Request):
        session=_session(request,write=True)
        if session["owner"]!=owner:
            _deny("FOREIGN_SESSION")
        body=_body(await request.json(),{"expected_revision","items"})
        return _update(scope,body["expected_revision"],body["items"],
                       trusted_bot=False)

    @router.post("/internal/t1/pilot/carts/read")
    async def bot_read(request:Request):
        _server(request)
        body=_body(await request.json(),{"telegram_user_id"})
        if type(body["telegram_user_id"]) is not int or body["telegram_user_id"]!=owner:
            _deny("FOREIGN_BOT_CLIENT")
        return {"test_only":True,"checkout_allowed":False,
                "scopes":_view(private=True)}

    @router.post("/internal/t1/pilot/carts/write")
    async def bot_write(request:Request):
        _server(request)
        body=_body(await request.json(),{
            "telegram_user_id","scope","expected_revision","items"})
        if type(body["telegram_user_id"]) is not int or body["telegram_user_id"]!=owner:
            _deny("FOREIGN_BOT_CLIENT")
        return _update(body["scope"],body["expected_revision"],body["items"],
                       trusted_bot=True)

    app.include_router(router)
