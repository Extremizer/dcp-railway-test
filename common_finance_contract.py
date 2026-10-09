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
    credit_enabled=True,
    due_date_enabled=True,
    aging_enabled=True,
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
    """Current safe default until per-warehouse payment routing is enabled."""

    source = str(offer_source or "").strip().lower()
    if source not in {"usa", "warehouse"}:
        raise ValueError("unsupported offer_source")
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
    "client_usa_financially_ready",
    "requires_client_wallet_charge",
]
