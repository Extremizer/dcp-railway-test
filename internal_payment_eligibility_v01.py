#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only payment eligibility evaluator for Internal Order P5.

DUE is not equivalent to payable. This service is an isolated server contract,
not a public payment endpoint and not real authorization.
"""
from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from common_finance_contract import PaymentRoute
from internal_order_core_v01 import _assert_terms_integrity
from internal_order_terms_v01 import verify_snapshot_terms_provenance


CONTRACT_VERSION="internal-payment-eligibility-v0.1"
CONFIRM_PAYMENT_PERMISSION="CONFIRM_PAYMENT"


@dataclass(frozen=True)
class PaymentEligibilityContext:
    actual_actor: str
    warehouse_id: int
    authorized: bool
    permissions: frozenset[str]
    payment_ready_statuses: tuple[str, ...]


def _reason(code,detail=None):
    out={"code":code}
    if detail is not None:
        out["detail"]=str(detail)
    return out


def evaluate_direct_payment_eligibility(
    db_file,
    *,
    order_id,
    warehouse_id,
    context: PaymentEligibilityContext,
):
    """Return a fail-closed eligibility decision without database writes."""
    reasons=[]
    if not isinstance(context,PaymentEligibilityContext):
        return {
            "contract_version":CONTRACT_VERSION,
            "eligible":False,
            "reasons":[_reason("AUTH_CONTEXT_REQUIRED")],
        }

    try:
        requested_warehouse=int(warehouse_id)
    except (TypeError,ValueError):
        requested_warehouse=-1

    if not context.authorized:
        reasons.append(_reason("ACTOR_NOT_AUTHORIZED"))
    if type(context.warehouse_id) is not int or context.warehouse_id<=0:
        reasons.append(_reason("ACTOR_WAREHOUSE_SCOPE_INVALID"))
    elif context.warehouse_id!=requested_warehouse:
        reasons.append(_reason("ACTOR_WAREHOUSE_SCOPE_MISMATCH"))
    if CONFIRM_PAYMENT_PERMISSION not in context.permissions:
        reasons.append(_reason("CONFIRM_PAYMENT_PERMISSION_REQUIRED"))
    if not context.payment_ready_statuses:
        reasons.append(_reason("PAYMENT_READY_STATUS_POLICY_REQUIRED"))

    try:
        c=sqlite3.connect(db_file)
        c.row_factory=sqlite3.Row
        c.execute("BEGIN")
        snap=c.execute(
            """SELECT order_id,warehouse_id,payment_route,order_status_snapshot
               FROM internal_order_payment_snapshots_v01 WHERE order_id=?""",
            (str(order_id),),
        ).fetchone()
        if snap is None:
            reasons.append(_reason("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED"))
        else:
            if int(snap["warehouse_id"])!=requested_warehouse:
                reasons.append(_reason("SNAPSHOT_WAREHOUSE_MISMATCH"))
            if str(snap["payment_route"])!=PaymentRoute.DIRECT_PARTNER.value:
                reasons.append(_reason("DIRECT_PARTNER_ROUTE_REQUIRED"))

        state=c.execute(
            """SELECT payment_status FROM internal_order_payment_state_v01
               WHERE order_id=?""",
            (str(order_id),),
        ).fetchone()
        if state is None:
            reasons.append(_reason("PAYMENT_STATE_REQUIRED"))
        elif str(state["payment_status"])!="DUE":
            reasons.append(_reason("PAYMENT_STATUS_NOT_DUE",state["payment_status"]))

        order=c.execute(
            "SELECT status FROM orders WHERE order_id=?",(str(order_id),)
        ).fetchone()
        if order is None:
            reasons.append(_reason("ORDER_NOT_FOUND"))
        elif context.payment_ready_statuses and str(order["status"]) not in set(
            context.payment_ready_statuses
        ):
            reasons.append(_reason("ORDER_NOT_PAYMENT_READY",order["status"]))
        elif snap is not None and str(order["status"]) != str(snap["order_status_snapshot"]):
            reasons.append(
                _reason(
                    "ORDER_STATUS_CHANGED_SINCE_SNAPSHOT",
                    f"{snap['order_status_snapshot']}->{order['status']}",
                )
            )

        if snap is not None:
            try:
                _assert_terms_integrity(c,str(order_id))
            except ValueError as exc:
                reasons.append(_reason(str(exc)))

            try:
                verify_snapshot_terms_provenance(
                    db_file,str(order_id),conn=c
                )
            except (TypeError,ValueError,KeyError) as exc:
                reasons.append(_reason(str(exc)))

        c.rollback()
    except sqlite3.Error as exc:
        reasons.append(_reason("READ_MODEL_ERROR",type(exc).__name__))
    finally:
        try:
            c.close()
        except Exception:
            pass

    # Unknown/duplicate failures still fail closed; stable unique ordering.
    unique=[]
    seen=set()
    for reason in reasons:
        key=(reason.get("code"),reason.get("detail"))
        if key not in seen:
            seen.add(key)
            unique.append(reason)
    return {
        "contract_version":CONTRACT_VERSION,
        "order_id":str(order_id),
        "warehouse_id":requested_warehouse,
        "eligible":not unique,
        "reasons":unique,
    }


__all__=[
    "CONTRACT_VERSION",
    "CONFIRM_PAYMENT_PERMISSION",
    "PaymentEligibilityContext",
    "evaluate_direct_payment_eligibility",
]
