#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated strict persistence boundary for future Internal shared-order writes.

This module is NOT wired to the production checkout writer. It proves the
validation contract that must run before any future Internal INSERT/UPDATE.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from common_finance_contract import PaymentRoute


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    value=value.strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


def _positive_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _whole_rub(value: Any, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer number of RUB")
    if value < 0:
        raise ValueError(f"{name} must be non-negative")
    return value


@dataclass(frozen=True)
class PreparedInternalLine:
    position: int
    manufacturer: str
    oem: str
    requested_oem: str
    name: str
    quantity: int
    customer_unit_rub: int
    offer_source: str
    warehouse_id: int
    payment_route: str


@dataclass(frozen=True)
class PreparedInternalOrder:
    order_id: str
    client_id: str
    status: str
    customer_amount_rub: int
    warehouse_id: int
    payment_route: str
    items: tuple[PreparedInternalLine, ...]


def prepare_internal_order_write(
    *,
    order_id,
    client_id,
    status,
    items,
) -> PreparedInternalOrder:
    """Validate every protected value before any SQLite write is allowed."""
    order_id=_text(order_id,"order_id")
    client_id=_text(client_id,"client_id")
    status=_text(status,"status")
    if not isinstance(items,(list,tuple)) or not items:
        raise ValueError("items must be a non-empty list")

    prepared=[]
    warehouses=set()
    routes=set()
    total=0
    seen_positions=set()

    for raw in items:
        if not isinstance(raw,dict):
            raise TypeError("order item must be a dict")
        position=_positive_int(raw.get("position"),"position")
        if position in seen_positions:
            raise ValueError("duplicate position")
        seen_positions.add(position)
        manufacturer=_text(raw.get("manufacturer"),"manufacturer")
        oem=_text(raw.get("oem"),"oem")
        requested_oem=raw.get("requested_oem")
        if requested_oem is None:
            requested_oem=oem
        requested_oem=_text(requested_oem,"requested_oem")
        name=_text(raw.get("name"),"name")
        quantity=_positive_int(raw.get("quantity"),"quantity")
        unit=_whole_rub(raw.get("customer_unit_rub"),"customer_unit_rub")
        source=_text(raw.get("offer_source"),"offer_source").lower()
        if source!="warehouse":
            raise ValueError("Internal order requires warehouse offer_source")
        warehouse_id=_positive_int(raw.get("warehouse_id"),"warehouse_id")
        route=_text(raw.get("payment_route"),"payment_route").lower()
        try:
            route=PaymentRoute(route).value
        except ValueError as exc:
            raise ValueError("invalid payment_route") from exc

        warehouses.add(warehouse_id)
        routes.add(route)
        total += unit * quantity
        prepared.append(
            PreparedInternalLine(
                position=position,
                manufacturer=manufacturer,
                oem=oem,
                requested_oem=requested_oem,
                name=name,
                quantity=quantity,
                customer_unit_rub=unit,
                offer_source=source,
                warehouse_id=warehouse_id,
                payment_route=route,
            )
        )

    if len(warehouses)!=1:
        raise ValueError("Internal order must target exactly one warehouse")
    if len(routes)!=1:
        raise ValueError("Internal order payment route must be uniform")

    prepared.sort(key=lambda x:x.position)
    return PreparedInternalOrder(
        order_id=order_id,
        client_id=client_id,
        status=status,
        customer_amount_rub=total,
        warehouse_id=next(iter(warehouses)),
        payment_route=next(iter(routes)),
        items=tuple(prepared),
    )


def insert_disposable_shared_order(conn, prepared: PreparedInternalOrder) -> None:
    """Proof helper: execute INSERTs only after a PreparedInternalOrder exists."""
    if not isinstance(prepared,PreparedInternalOrder):
        raise TypeError("PreparedInternalOrder required")
    conn.execute(
        """INSERT INTO orders(order_id,status,created_at,telegram_user_id)
           VALUES(?,?,'p5-disposable',0)""",
        (prepared.order_id,prepared.status),
    )
    for item in prepared.items:
        conn.execute(
            """INSERT INTO order_items(
                 order_id,position,manufacturer,oem,requested_oem,name,
                 offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (
                prepared.order_id,item.position,item.manufacturer,item.oem,
                item.requested_oem,item.name,item.offer_source,item.warehouse_id,
                item.quantity,item.customer_unit_rub,item.payment_route,
            ),
        )


__all__=[
    "PreparedInternalLine",
    "PreparedInternalOrder",
    "prepare_internal_order_write",
    "insert_disposable_shared_order",
]
