"""EXTREMIZER RF37 — working-source binding boundary (ISOLATED / HOLD).

Accepts only a trusted Telegram actor issued by an injected authenticator,
a *separate* server-attested RF stock/price source, and an explicit
production V2 cart writer. Legacy user_data is untrusted input and is
NEVER treated as a V2 cart or as independent price evidence.

No import-time IO, no external imports, no direct sqlite3 or Telegram calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any, Mapping, Protocol
import hmac
import json
import re


LIVE_HOLD = True
PREFLIGHT_VERIFIED = False
TELEGRAM_WRITE_ENABLED = False
V2_WRITE_ENABLED = False
ORDERS_WRITE_ENABLED = False
CLIENT_WHITELIST_ENABLED = False


class RF37Stop(RuntimeError):
    pass


@dataclass(frozen=True)
class VerifiedTelegramActor:
    actor_id: int
    session_binding: str
    issued_by: str
    authenticated: bool


@dataclass(frozen=True)
class RF37VerifiedOffer:
    manufacturer: str
    oem: str
    warehouse_id: int
    supplier_id: str
    supplier_internal_warehouse: str
    price_rub: int
    available_quantity: int
    observed_at: str
    expires_at: str
    stock_provenance: str
    price_provenance: str
    verification_id: str
    verified_for_checkout: bool


class AuthenticatedTelegramActorPort(Protocol):
    def require_authenticated_actor(self, update: object, context: object) -> VerifiedTelegramActor: ...


class IndependentRFPriceStockPort(Protocol):
    def verify_supplier_offer(self, *, manufacturer: str, oem: str,
                              quantity: int, warehouse_id: int,
                              supplier_id: str, supplier_internal_warehouse: str) -> RF37VerifiedOffer: ...


class ApprovedExistingV2WritePort(Protocol):
    """One authorized operation, idempotent and actor-scoped.

    Must not be implemented with synthetic_actor_for_offline_qa.
    """
    def migrate_legacy_line_once(
        self, *, authenticated_actor: VerifiedTelegramActor,
        source_digest: str, manufacturer: str, oem: str,
        quantity: int, verified_offer: RF37VerifiedOffer,
    ) -> Mapping[str, Any]: ...


def _digest(data: Mapping[str, Any]) -> str:
    return sha256(json.dumps(data,sort_keys=True,separators=(",", ":"),
                             ensure_ascii=False,allow_nan=False).encode("utf-8")).hexdigest()


def _canonical_oem(raw: Any) -> str:
    if not isinstance(raw,str):
        raise RF37Stop("OEM unavailable")
    normalized=raw.strip().upper().replace(" ","")
    if not re.fullmatch(r"[A-Z0-9._/-]{2,80}",normalized):
        raise RF37Stop("OEM invalid")
    return normalized


class WorkingRFLegacyToV2Bridge:
    def __init__(self, *, actor_port: AuthenticatedTelegramActorPort,
                 offer_port: IndependentRFPriceStockPort,
                 v2_port: ApprovedExistingV2WritePort,
                 required_supplier_id: str,
                 required_warehouse_id: int,
                 required_internal_warehouse: str):
        self._actor=actor_port
        self._offers=offer_port
        self._v2=v2_port
        self._supplier=required_supplier_id
        self._warehouse=required_warehouse_id
        self._internal=required_internal_warehouse
        if not (isinstance(required_supplier_id,str) and required_supplier_id
                and type(required_warehouse_id) is int and required_warehouse_id>0
                and isinstance(required_internal_warehouse,str) and required_internal_warehouse):
            raise RF37Stop("explicit approved supplier and warehouse mapping absent")

    @staticmethod
    def _legacy_line(user_data: Mapping[str,Any]) -> tuple[str,str,int,str]:
        if not isinstance(user_data,Mapping):
            raise RF37Stop("legacy context unavailable")
        cart=user_data.get("cart")
        if not isinstance(cart,Mapping) or len(cart)!=1:
            raise RF37Stop("unsupported or empty legacy cart; one RF line only")
        key,item=next(iter(cart.items()))
        if not isinstance(key,str) or not isinstance(item,Mapping):
            raise RF37Stop("invalid legacy line")
        manufacturer=item.get("manufacturer")
        oem=item.get("oem")
        qty=item.get("qty")
        if not isinstance(manufacturer,str) or not manufacturer.strip():
            raise RF37Stop("manufacturer missing")
        normalized=_canonical_oem(oem)
        expected=manufacturer+"|"+str(oem)
        if not hmac.compare_digest(key.encode(),expected.encode()):
            raise RF37Stop("legacy line key mismatch")
        if type(qty) is not int or not 1<=qty<=99:
            raise RF37Stop("invalid quantity")
        # Legacy price, user-provided warehouse id, or preview result
        # are intentionally NOT returned as trusted evidence.
        return manufacturer,normalized,qty,key

    @staticmethod
    def _validated_offer(offer: RF37VerifiedOffer, *,
                         manufacturer: str, oem: str, quantity: int,
                         warehouse: int, supplier: str, internal: str) -> None:
        if not isinstance(offer,RF37VerifiedOffer) or offer.verified_for_checkout is not True:
            raise RF37Stop("approved server RF verification missing")
        if (offer.manufacturer!=manufacturer or offer.oem!=oem
            or offer.warehouse_id!=warehouse or offer.supplier_id!=supplier
            or offer.supplier_internal_warehouse!=internal):
            raise RF37Stop("RF stock/price identity mismatch")
        if (type(offer.price_rub) is not int or offer.price_rub<=0
            or type(offer.available_quantity) is not int
            or offer.available_quantity<quantity):
            raise RF37Stop("confirmed stock or RUB price unavailable")
        if not all(isinstance(x,str) and x.strip() for x in
                   (offer.stock_provenance,offer.price_provenance,offer.verification_id)):
            raise RF37Stop("independent price/stock provenance absent")
        try:
            obs=datetime.fromisoformat(offer.observed_at)
            exp=datetime.fromisoformat(offer.expires_at)
            now=datetime.now(timezone.utc)
        except (ValueError,TypeError):
            raise RF37Stop("freshness unavailable") from None
        if obs.tzinfo is None or exp.tzinfo is None or not (obs<=now<exp):
            raise RF37Stop("price/stock expired")

    def preview(self, *, update: object, context: object,
                user_data: Mapping[str,Any]) -> Mapping[str,Any]:
        actor=self._actor.require_authenticated_actor(update,context)
        if (not isinstance(actor,VerifiedTelegramActor)
            or type(actor.actor_id) is not int or actor.actor_id<1
            or not actor.authenticated or not actor.session_binding
            or not actor.issued_by):
            raise RF37Stop("Telegram actor binding not independently authenticated")
        mfg,oem,qty,key=self._legacy_line(user_data)
        offer=self._offers.verify_supplier_offer(
            manufacturer=mfg,oem=oem,quantity=qty,warehouse_id=self._warehouse,
            supplier_id=self._supplier,supplier_internal_warehouse=self._internal)
        self._validated_offer(offer,manufacturer=mfg,oem=oem,quantity=qty,
                              warehouse=self._warehouse,supplier=self._supplier,
                              internal=self._internal)
        source_digest=_digest({"actor":actor.actor_id,"session":actor.session_binding,
                               "legacy_key":key,"manufacturer":mfg,"oem":oem,
                               "qty":qty,"stock_check":offer.verification_id})
        return {
            "actor":actor,"manufacturer":mfg,"oem":oem,"quantity":qty,
            "offer":offer,"source_digest":source_digest,
            "checkout_allowed":False,"v2_write_allowed":False,
        }

    def migrate_into_v2(self, *, update: object, context: object,
                        user_data: Mapping[str,Any]) -> Mapping[str,Any]:
        if (LIVE_HOLD or not PREFLIGHT_VERIFIED or not TELEGRAM_WRITE_ENABLED
            or not V2_WRITE_ENABLED or not CLIENT_WHITELIST_ENABLED):
            raise RF37Stop("STOP: working V2 write not authorized")
        verified=self.preview(update=update,context=context,user_data=user_data)
        # Only a separately authorized V2 port may create a trusted
        # actor token and perform the idempotent durable V2 mutation.
        # No direct use of V2Store synthetic actor issuer.
        response=self._v2.migrate_legacy_line_once(
            authenticated_actor=verified["actor"],
            source_digest=verified["source_digest"],
            manufacturer=verified["manufacturer"],oem=verified["oem"],
            quantity=verified["quantity"],verified_offer=verified["offer"])
        if (not isinstance(response,Mapping)
            or response.get("actor_tg_id")!=verified["actor"].actor_id
            or response.get("scope")!=f"rf:{self._warehouse}"
            or type(response.get("revision")) is not int
            or not isinstance(response.get("cart_id"),str)
            or not response.get("cart_id")):
            raise RF37Stop("trusted V2 receipt incomplete")
        return response
