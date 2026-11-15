"""RF38: production-facing Telegram identity, RF V2 writer and independent offer ports.

No token, Telegram request or real SQLite open at import. Default LIVE HOLD.
Only inject separately approved, non-synthetic pricing/stock providers.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime,timezone
from pathlib import Path
from typing import Callable,Mapping
import hmac
import re
from .rf_live_source_binding37 import VerifiedTelegramActor,RF37VerifiedOffer,RF37Stop

LIVE_HOLD=True
PREFLIGHT_VERIFIED=False
V2_WRITE_ENABLED=False
TELEGRAM_WRITE_ENABLED=False

class RF38Stop(RuntimeError): pass

@dataclass(frozen=True)
class TelegramRuntimeAuth:
    """In-process authority must be injected by existing PTB callback.
    Never construct from client-provided JSON, callback data or user_data.
    """
    expected_private_chat_id:int
    expected_actor_id:int
    approved_issuer:str

class VerifiedPTBActorPort:
    def __init__(self, settings:TelegramRuntimeAuth):
        if (type(settings.expected_actor_id) is not int or settings.expected_actor_id<=0
            or type(settings.expected_private_chat_id) is not int
            or settings.expected_private_chat_id!=settings.expected_actor_id
            or not settings.approved_issuer):
            raise RF38Stop("approved private Telegram scope required")
        self._settings=settings

    def require_authenticated_actor(self,update,context):
        # PTB-authenticated Update only; injected by the existing Application
        # with single polling. We never accept id from user_data.
        from telegram import Update
        if type(update) is not Update:
            raise RF38Stop("trusted PTB Update required")
        user=update.effective_user
        chat=update.effective_chat
        if (user is None or chat is None or chat.type!="private"
            or user.id!=self._settings.expected_actor_id
            or chat.id!=self._settings.expected_private_chat_id):
            raise RF38Stop("private client scope mismatch")
        if update.callback_query is not None:
            caller=update.callback_query.from_user
            if caller is None or caller.id!=user.id:
                raise RF38Stop("callback actor mismatch")
        if getattr(context,"_application",None) is None and getattr(context,"application",None) is None:
            raise RF38Stop("unverified PTB context")
        return VerifiedTelegramActor(
            actor_id=user.id,
            session_binding="TGPRIVATE:"+str(chat.id),
            issued_by=self._settings.approved_issuer,
            authenticated=True)

@dataclass(frozen=True)
class AttestedQuote:
    source:str
    warehouse_id:int
    supplier_id:str
    supplier_internal_warehouse:str
    manufacturer:str
    oem:str
    price_rub:int
    observed_at:str
    expires_at:str
    provider_record_id:str
    independently_confirmed:bool

@dataclass(frozen=True)
class AttestedAvailability:
    source:str
    warehouse_id:int
    supplier_id:str
    supplier_internal_warehouse:str
    manufacturer:str
    oem:str
    available_quantity:int
    observed_at:str
    expires_at:str
    provider_record_id:str
    reservations_accounted_for:bool
    independently_confirmed:bool

class IndependentRFOfferPort:
    """Two independently configured real providers. NEVER use pilot price.
    Neither source may be named 'synthetic', 'preview' or 'html_unverified'.
    """
    def __init__(self,*,price_reader:Callable,stock_reader:Callable,
                 approved_price_sources:frozenset[str],
                 approved_stock_sources:frozenset[str]):
        if not callable(price_reader) or not callable(stock_reader):
            raise RF38Stop("two actual providers missing")
        invalid={"synthetic","test","preview","historical","parsed_unverified","unknown"}
        if (not approved_price_sources or not approved_stock_sources
            or any(v.lower() in invalid for v in approved_price_sources|approved_stock_sources)):
            raise RF38Stop("approved independent sources unavailable")
        self._p=price_reader;self._s=stock_reader
        self._ps=approved_price_sources;self._ss=approved_stock_sources

    @staticmethod
    def _fresh(a,b):
        try:
            t=datetime.now(timezone.utc)
            start=datetime.fromisoformat(a)
            end=datetime.fromisoformat(b)
            return (start.tzinfo is not None and end.tzinfo is not None
                    and start<=t<end and (t-start).total_seconds()<=300)
        except (TypeError,ValueError):
            return False

    def verify_supplier_offer(self,*,manufacturer,oem,quantity,warehouse_id,
                              supplier_id,supplier_internal_warehouse):
        if (type(quantity) is not int or quantity<1
            or type(warehouse_id) is not int or warehouse_id<1
            or not all(isinstance(x,str) and x.strip() for x in
                (manufacturer,oem,supplier_id,supplier_internal_warehouse))):
            raise RF38Stop("RF source identity incomplete")
        keys=dict(manufacturer=manufacturer,oem=oem,warehouse_id=warehouse_id,
                  supplier_id=supplier_id,
                  supplier_internal_warehouse=supplier_internal_warehouse)
        price=self._p(**keys)
        stock=self._s(**keys)
        if type(price) is not AttestedQuote or type(stock) is not AttestedAvailability:
            raise RF38Stop("independent provenance attestation missing")
        for item,allowed in ((price,self._ps),(stock,self._ss)):
            if (item.source not in allowed or not item.independently_confirmed
                or any(getattr(item,k)!=v for k,v in keys.items())
                or not item.provider_record_id or
                not self._fresh(item.observed_at,item.expires_at)):
                raise RF38Stop("independent supplier evidence invalid or stale")
        if (type(stock.available_quantity) is not int
            or stock.available_quantity<quantity
            or not stock.reservations_accounted_for
            or type(price.price_rub) is not int or price.price_rub<=0):
            raise RF38Stop("price or net available stock not authoritative")
        return RF37VerifiedOffer(
            manufacturer=manufacturer,oem=oem,warehouse_id=warehouse_id,
            supplier_id=supplier_id,
            supplier_internal_warehouse=supplier_internal_warehouse,
            price_rub=price.price_rub,
            available_quantity=stock.available_quantity,
            observed_at=max(price.observed_at,stock.observed_at),
            expires_at=min(price.expires_at,stock.expires_at),
            stock_provenance=stock.source,price_provenance=price.source,
            verification_id=price.provider_record_id+":"+stock.provider_record_id,
            verified_for_checkout=True)

class AuthorizedV2WritePort:
    """Existing V2Store select + mutate; trusted Actor issued only here.
    Requires a separately approved isolated V2 path and explicit writes GO.
    """
    def __init__(self,*,v2_store,expected_resolved_path:Path,
                 approved_client_id:int,approved_supplier_id:str,
                 approved_warehouse_id:int):
        from .tgfirst_multicart_v2 import V2Store
        if (type(v2_store) is not V2Store
            or Path(v2_store.path).resolve()!=Path(expected_resolved_path).resolve()
            or not Path(expected_resolved_path).is_file()
            or type(approved_client_id) is not int or approved_client_id<=0
            or not approved_supplier_id or
            type(approved_warehouse_id) is not int or approved_warehouse_id<1):
            raise RF38Stop("V2 real store and approved actor/scope not verified")
        self._v2=v2_store
        self._writer_capability=object()
        self._v2._bind_rf_writer_boundary(self._writer_capability)
        self._client=approved_client_id
        self._supplier=approved_supplier_id
        self._warehouse=approved_warehouse_id

    def migrate_legacy_line_once(self,*,authenticated_actor,source_digest,
                                manufacturer,oem,quantity,verified_offer):
        if LIVE_HOLD or not PREFLIGHT_VERIFIED or not V2_WRITE_ENABLED or not TELEGRAM_WRITE_ENABLED:
            raise RF38Stop("STOP: LIVE V2 write not authorized")
        if (type(authenticated_actor) is not VerifiedTelegramActor
            or authenticated_actor.actor_id!=self._client
            or not authenticated_actor.authenticated
            or authenticated_actor.session_binding!="TGPRIVATE:"+str(self._client)
            or not authenticated_actor.issued_by):
            raise RF38Stop("trusted Telegram identity missing")
        if (type(verified_offer) is not RF37VerifiedOffer
            or verified_offer.verified_for_checkout is not True
            or verified_offer.supplier_id!=self._supplier
            or verified_offer.warehouse_id!=self._warehouse
            or verified_offer.manufacturer!=manufacturer or verified_offer.oem!=oem
            or verified_offer.available_quantity<quantity
            or type(verified_offer.price_rub) is not int):
            raise RF38Stop("authoritative RF offer missing")
        if not isinstance(source_digest,str) or not re.fullmatch(r"[0-9a-f]{64}",source_digest):
            raise RF38Stop("stable legacy source idempotency missing")
        # Explicit production actor issuance takes place inside isolated
        # V2 module, not synthetic_actor_for_offline_qa.
        from .tgfirst_multicart_v2 import issue_approved_actor_for_rf38,SyntheticAuthority
        actor=issue_approved_actor_for_rf38(
            authenticated_actor,expected_client_id=self._client)
        def verify_offer(m,o,scope):
            if (m,o,scope)!=(manufacturer,oem,f"rf:{self._warehouse}"):
                raise RF38Stop("scope and offer mismatch")
            return dict(can_add=True,manufacturer=m,oem=o,
                        source="warehouse",warehouse_id=self._warehouse,
                        price_rub=verified_offer.price_rub,
                        available_quantity=verified_offer.available_quantity)
        auth=SyntheticAuthority(
            warehouses=lambda wid:wid==self._warehouse,offers=verify_offer)
        selected=self._v2.select(actor,f"rf:{self._warehouse}",auth)
        cart=selected["cart"]
        op_key="rf38-"+source_digest[:32]
        result=self._v2.mutate(
            actor,cart["cart_id"],cart["revision"],op_key,"add",
            dict(manufacturer=manufacturer,oem=oem,qty=quantity,
                 source="warehouse",warehouse_id=self._warehouse),auth,
            _writer_capability=self._writer_capability)
        state=result["cart"]
        return dict(actor_tg_id=state["actor_tg_id"],
                    cart_id=state["cart_id"],scope=state["scope"],
                    revision=state["revision"])
