"""EXTREMIZER / RF INTEGRATION-21. Isolated, no I/O offer -> preview.
Connects to existing RFRouter's RFBindingPort protocol and existing
legacy-cart shape, without mutating context.user_data['cart'] or V2.
Data are frozen observations from OrangeATV, NOT executable quotations.
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping
from .rf_working_integration_adapter import configure_router, RFRouterHold

LIVE_HOLD = True
PREFLIGHT_VERIFIED = False
REAL_DB_ATTACHED = False
V2_WRITE_ENABLED = False
ORDERS_WRITE_ENABLED = False
OUTBOX_WRITE_ENABLED = False
TELEGRAM_WRITE_ENABLED = False
TEST_CLIENT_ID = 7005635854
ADMIN_ID = 52637605
OEM = "417300574"
MANUFACTURER = "BRP"
SUPPLIER = "OrangeATV"
OFFERS = MappingProxyType({
    "2": ("Склад2", 10, 34990),
    "5": ("Склад5", 7, 34700),
})


class OfferPreviewHold(RuntimeError):
    pass


@dataclass(frozen=True)
class Screen:
    text: str
    buttons: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class OfferPreview:
    """No reference to mutable working cart. This is a read-only proposal."""
    legacy_cart_key: str
    legacy_cart_item: Mapping[str, Any]
    supplier: str
    supplier_internal_warehouse: str
    supplier_internal_warehouse_code: str
    available_on_website: int
    quantity: int
    price_rub: int
    price_status: str
    checkout_allowed: bool

    def copy_for_legacy_cart_interface(self) -> dict:
        """Detached legacy-compatible candidate, NEVER inserted into cart."""
        result = {"manufacturer": MANUFACTURER, "oem": OEM,
                  "requested_oem": OEM, "qty": self.quantity,
                  "name": "BRP OEM " + OEM, "price": None,
                  "catalog": None, "_rf_supplier": self.supplier,
                  "_rf_supplier_internal_warehouse": self.supplier_internal_warehouse,
                  "_rf_supplier_internal_warehouse_code": self.supplier_internal_warehouse_code,
                  "_rf_price_rub_preview": self.price_rub,
                  "_rf_price_status": self.price_status,
                  "_rf_checkout_allowed": False}
        return {"legacy_key": self.legacy_cart_key,
                "legacy_item": result, "write_allowed": False}


class OrangeATVPreviewBinding:
    """Existing RFBindingPort interface: search and async callback, no writes."""

    def __init__(self):
        self._selection: dict[int, str] = {}  # isolated memory ONLY

    def search(self, user_id: int, user_data: dict,
               manufacturer: str, oem: str) -> Screen:
        if user_id != TEST_CLIENT_ID or manufacturer.strip().upper() != MANUFACTURER or oem.strip() != OEM:
            raise OfferPreviewHold("unapproved client/manufacturer/OEM")
        return self.show_offers(user_id)

    def show_offers(self, user_id: int) -> Screen:
        if user_id != TEST_CLIENT_ID:
            raise OfferPreviewHold("client not authorized")
        buttons = tuple((f"OrangeATV / {name} — {qty} шт. / {price} RUB",
                         f"rf:offer:orangeatv:{code}")
                        for code, (name, qty, price) in OFFERS.items())
        return Screen("BRP OEM 417300574 — предложения OrangeATV. "
                      "Цены предварительные; оформление заблокировано.", buttons)

    def select_offer(self, user_id: int, code: str) -> OfferPreview:
        if type(user_id) is not int or user_id != TEST_CLIENT_ID or code not in OFFERS:
            raise OfferPreviewHold("client or internal warehouse not authorized")
        name, quantity_available, price = OFFERS[code]
        self._selection[user_id] = code
        return OfferPreview(
            legacy_cart_key=f"{MANUFACTURER}|{OEM}",
            legacy_cart_item=MappingProxyType({"manufacturer": MANUFACTURER,
                        "oem": OEM, "qty": 1}),
            supplier=SUPPLIER,
            supplier_internal_warehouse=name,
            supplier_internal_warehouse_code=code,
            available_on_website=quantity_available,
            quantity=1,
            price_rub=price,
            price_status="SITE_OBSERVED_UNVERIFIED",
            checkout_allowed=False,
        )

    def cart_preview(self, user_id: int) -> OfferPreview:
        if user_id != TEST_CLIENT_ID or user_id not in self._selection:
            raise OfferPreviewHold("no selected offer")
        code = self._selection[user_id]
        name, available, price = OFFERS[code]
        return OfferPreview(
            legacy_cart_key=f"{MANUFACTURER}|{OEM}",
            legacy_cart_item=MappingProxyType({"manufacturer": MANUFACTURER,
                        "oem": OEM, "qty": 1}),
            supplier=SUPPLIER,
            supplier_internal_warehouse=name,
            supplier_internal_warehouse_code=code,
            available_on_website=available,
            quantity=1,
            price_rub=price,
            price_status="SITE_OBSERVED_UNVERIFIED",
            checkout_allowed=False,
        )

    async def callback(self, user_id: int, user_data: dict, value: str) -> Screen:
        if user_id != TEST_CLIENT_ID:
            raise OfferPreviewHold("client not authorized")
        if value == "rf:offer:show":
            return self.show_offers(user_id)
        if not isinstance(value, str) or not value.startswith("rf:offer:orangeatv:"):
            raise OfferPreviewHold("unsupported callback / checkout HOLD")
        code = value.rsplit(":", 1)[-1]
        item = self.select_offer(user_id, code)
        return Screen(f"Предварительная корзина: {item.supplier} / "
                      f"{item.supplier_internal_warehouse}, {MANUFACTURER} {OEM}, "
                      f"{item.quantity} шт., {item.price_rub} RUB. "
                      "Данные сайта, цена не подтверждена; checkout HOLD.",
                      (("Изменить предложение", "rf:offer:show"),))

    def text(self, user_id: int, user_data: dict, value: str):
        raise OfferPreviewHold("no checkout or address collection in this pilot")


def create_isolated_router():
    binding = OrangeATVPreviewBinding()
    router = configure_router(binding=binding, existing_cart=lambda _actor: None)
    return router, binding


def attach_to_working_bot(*_args, **_kwargs):
    raise OfferPreviewHold("STOP: no live bot/Telegram/DB/cart/V2 attachment")
