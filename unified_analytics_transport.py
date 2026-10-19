"""Isolated signed cross-producer transport. Canonical database ONLY.

Separate per-source HMAC keys; never use Telegram tokens or Dealer Price secrets.
No runtime mount until explicit integration gate.
"""
from __future__ import annotations
import hashlib,hmac,json,time
from fastapi import APIRouter,Request,HTTPException
from unified_analytics_core import begin,complete,link_exact,SOURCES

PATH="/internal/unified-analytics/v1"
PATH_CONVERSION=PATH+"/conversion"
FIELDS=frozenset((
 "source_bot","source_kind","delivery_key","oem","manufacturer",
 "telegram_user_id","username","first_name","last_name",
 "source_chat_id","source_chat_title","telegram_message_id",
 "result_status","price_status","display_price_amount",
 "display_price_currency","stock_rf_status","stock_rf"
))
def signed_headers(secret:str,source_bot:str,body:bytes,*,timestamp=None,path=PATH):
    if len(secret)<32 or source_bot not in SOURCES:raise ValueError("untrusted_source")
    ts=int(time.time() if timestamp is None else timestamp)
    digest=hashlib.sha256(body).hexdigest()
    msg=f"v1\nPOST\n{path}\n{source_bot}\n{ts}\n{digest}".encode()
    return {"X-Analytics-Source":source_bot,"X-Analytics-Timestamp":str(ts),
            "X-Analytics-Signature":hmac.new(secret.encode(),msg,hashlib.sha256).hexdigest(),
            "Content-Type":"application/json"}

def build_router(*,canonical_db,secrets_by_source):
    if set(secrets_by_source)!=SOURCES or any(not isinstance(s,str) or len(s)<32 for s in secrets_by_source.values()):
        raise ValueError("three_independent_producer_secrets_required")
    if len(set(secrets_by_source.values()))!=3:raise ValueError("shared_secret_prohibited")
    router=APIRouter(include_in_schema=False)
    @router.post(PATH)
    async def receive(request:Request):
        body=await request.body()
        if len(body)>8192:raise HTTPException(413,"payload_too_large")
        src=request.headers.get("X-Analytics-Source","")
        if src not in SOURCES:raise HTTPException(401,"unauthorized")
        try:ts=int(request.headers.get("X-Analytics-Timestamp",""))
        except ValueError:raise HTTPException(401,"unauthorized")
        if abs(int(time.time())-ts)>60:raise HTTPException(401,"expired")
        expected=signed_headers(secrets_by_source[src],src,body,timestamp=ts)["X-Analytics-Signature"]
        supplied=request.headers.get("X-Analytics-Signature","")
        if not hmac.compare_digest(expected,supplied):raise HTTPException(401,"unauthorized")
        try:payload=json.loads(body)
        except (ValueError,UnicodeDecodeError):raise HTTPException(400,"invalid_body")
        if not isinstance(payload,dict) or set(payload)-FIELDS or payload.get("source_bot")!=src:
            raise HTTPException(400,"invalid_event")
        if not payload.get("delivery_key") or not payload.get("oem"):
            raise HTTPException(400,"missing_identity")
        try:
            rid=begin(canonical_db,source_bot=src,
                source_kind=payload.get("source_kind") or "private",
                oem=payload["oem"],delivery_key=payload["delivery_key"],
                manufacturer=payload.get("manufacturer"),
                telegram_user_id=payload.get("telegram_user_id"),
                username=payload.get("username"),first_name=payload.get("first_name"),
                last_name=payload.get("last_name"),
                source_chat_id=payload.get("source_chat_id"),
                source_chat_title=payload.get("source_chat_title"),
                telegram_message_id=payload.get("telegram_message_id"))
        except ValueError:raise HTTPException(409,"delivery_identity_conflict")
        if rid is None:raise HTTPException(503,"canonical_db_unavailable")
        if payload.get("result_status") is not None:
            ok=complete(canonical_db,rid,
                result_status=payload.get("result_status"),
                price_status=payload.get("price_status"),
                price=payload.get("display_price_amount"),
                currency=payload.get("display_price_currency"),
                stock_status=payload.get("stock_rf_status"),
                stock=payload.get("stock_rf"))
            if not ok:raise HTTPException(409,"completion_conflict")
        return {"status":"ANALYTICS_ACCEPTED","request_id":rid}
    @router.post(PATH_CONVERSION)
    async def receive_conversion(request:Request):
        body=await request.body()
        if len(body)>4096:raise HTTPException(413,"payload_too_large")
        src=request.headers.get("X-Analytics-Source","")
        if src not in ("pricing","oemixibot"):
            raise HTTPException(403,"conversion_not_permitted")
        try:ts=int(request.headers.get("X-Analytics-Timestamp",""))
        except ValueError:raise HTTPException(401,"unauthorized")
        if abs(int(time.time())-ts)>60:raise HTTPException(401,"expired")
        expected=signed_headers(secrets_by_source[src],src,body,
                         timestamp=ts,path=PATH_CONVERSION)["X-Analytics-Signature"]
        if not hmac.compare_digest(expected,request.headers.get("X-Analytics-Signature","")):
            raise HTTPException(401,"unauthorized")
        try:payload=json.loads(body)
        except (ValueError,UnicodeDecodeError):raise HTTPException(400,"invalid_body")
        if (not isinstance(payload,dict) or set(payload)-{
          "source_bot","delivery_key","oem","telegram_user_id","action","order_id"}
          or payload.get("source_bot")!=src):
            raise HTTPException(400,"invalid_conversion")
        rid=link_exact(canonical_db,source_bot=src,
            delivery_key=payload.get("delivery_key"),oem=payload.get("oem"),
            telegram_user_id=payload.get("telegram_user_id"),
            action=payload.get("action"),order_id=payload.get("order_id"))
        if rid is None:raise HTTPException(409,"exact_request_not_linkable")
        return {"status":"CONVERSION_ACCEPTED","request_id":rid}
    return router
