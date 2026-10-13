#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""COMMON FINANCE integration contract.

This module intentionally contains NO finance math and NO database writes.

It exists only to freeze cross-interface vocabulary and policy boundaries while
the exact proven OEMixiBOT FinanceEngine is being recovered for COMMON CORE.

Do not turn this file into a second FinanceEngine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Literal


class PaymentRoute(str, Enum):
    """Where the customer pays for one order line."""

    EXTREMIZER_BALANCE = "extremizer_balance"
    DIRECT_PARTNER = "direct_partner"


class FulfillmentRoute(str, Enum):
    """How one order line is physically fulfilled."""

    THROUGH_EXTREMIZER = "through_extremizer"
    DROPSHIP = "dropship"


BuyerType = Literal["client", "dealer"]
WalletCurrency = Literal["RUB", "USD"]


@dataclass(frozen=True)
class FinancePolicy:
    buyer_type: BuyerType
    wallet_currency: WalletCurrency
    credit_enabled: bool
    due_date_enabled: bool
    aging_enabled: bool


CLIENT_POLICY = FinancePolicy(
    buyer_type="client",
    wallet_currency="RUB",
    credit_enabled=False,
    due_date_enabled=False,
    aging_enabled=False,
)

DEALER_POLICY = FinancePolicy(
    buyer_type="dealer",
    wallet_currency="USD",
    credit_enabled=False,
    due_date_enabled=False,
    aging_enabled=False,
)


@dataclass(frozen=True)
class LineFinanceSnapshot:
    """Finance-only immutable facts captured for one order line.

    amount_rub is deliberately passed in from the pricing/order layer.
    This contract MUST NOT recalculate prices.
    """

    order_id: str
    order_item_id: int
    offer_source: str
    payment_route: PaymentRoute
    amount_rub: int


def default_client_payment_route(offer_source: str) -> PaymentRoute:
    """Owner-approved default route for newly persisted client lines.

    USA remains paid through the EXTREMIZER client wallet. RF warehouse/Internal
    lines default to direct payment to the partner warehouse. Persisted snapshots
    remain authoritative for existing orders and are never rewritten by this
    policy function.
    """

    source = str(offer_source or "").strip().lower()
    if source == "usa":
        return PaymentRoute.EXTREMIZER_BALANCE
    if source == "warehouse":
        return PaymentRoute.DIRECT_PARTNER
    raise ValueError("unsupported offer_source")


def resolve_internal_client_payment_route(
    *,
    admin_assign_extremizer: bool = False,
    full_partner_amount_reserved: bool = False,
) -> PaymentRoute:
    """Resolve the future Internal checkout payment route.

    Direct partner payment is the default and the client does not choose the
    payee. EXTREMIZER may be selected only by an authorized admin flow after the
    same warehouse has atomically reserved the full partner amount P.

    This pure policy guard does not itself reserve money; the authoritative
    caller must obtain that reservation from the warehouse-balance domain.
    """

    if not admin_assign_extremizer:
        return PaymentRoute.DIRECT_PARTNER
    if not full_partner_amount_reserved:
        raise ValueError("EXTREMIZER_INTERNAL_ROUTE_REQUIRES_FULL_PARTNER_BALANCE")
    return PaymentRoute.EXTREMIZER_BALANCE


def client_usa_financially_ready(balance_rub: int | float) -> bool:
    """Fixed client rule: USA processing requires strictly positive balance."""

    return float(balance_rub) > 0.0


def requires_client_wallet_charge(route: PaymentRoute | str) -> bool:
    """Whether this line belongs in the client wallet ORDER_CHARGE total."""

    return PaymentRoute(route) is PaymentRoute.EXTREMIZER_BALANCE


__all__ = [
    "PaymentRoute",
    "FulfillmentRoute",
    "FinancePolicy",
    "LineFinanceSnapshot",
    "CLIENT_POLICY",
    "DEALER_POLICY",
    "default_client_payment_route",
    "resolve_internal_client_payment_route",
    "client_usa_financially_ready",
    "requires_client_wallet_charge",
]
