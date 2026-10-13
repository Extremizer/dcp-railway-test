#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated Internal Order Core v0.1 payment extension.

This module extends the existing shared orders/order_items model. It does not
create a second OrderEngine and is not wired to production routes/accounts.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import sqlite3
from typing import Any

from client_finance_schema import ensure_client_finance_schema
import client_wallet_info_v14 as client_info
from common_finance_contract import (
    PaymentRoute,
    resolve_internal_client_payment_route,
)
import warehouse_balance_v14 as warehouse_balance


PAYMENT_DUE = "DUE"
PAYMENT_PAID = "PAID"
TERMS_FINGERPRINT_VERSION = "order-items-v1"


def _strict_whole_rub(value: Any, name: str, *, allow_zero: bool = True) -> int:
    """Whole-RUB money contract for Internal Order terms.

    bool, float, Decimal-like objects and strings are rejected instead of being
    coerced/truncated. Existing FinanceEngine arithmetic is not changed.
    """
    if type(value) is not int:
        raise TypeError(f"{name} must be an integer number of RUB")
    if value < 0 or (not allow_zero and value == 0):
        raise ValueError(f"{name} must be {'positive' if not allow_zero else 'non-negative'}")
    return value


def _strict_positive_int(value: Any, name: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{name} must be a positive integer")
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _text(value: Any, name: str) -> str:
    value = str(value or "").strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


@dataclass(frozen=True)
class WarehouseActorContext:
    """Trusted-server actor facts for the isolated prototype.

    This object is not authentication. Real integration must construct it only
    after warehouse-scoped server authorization succeeds.
    """

    actual_actor: str
    effective_subject: str
    warehouse_id: int
    authorized: bool


@dataclass(frozen=True)
class AdminActorContext:
    """Trusted-server admin facts for the isolated exception prototype."""

    actual_actor: str
    effective_subject: str
    authorized: bool


def init(db_file) -> None:
    """Create only the Internal extension tables required by v0.1."""
    ensure_client_finance_schema(db_file)
    warehouse_balance.init(db_file)
    with sqlite3.connect(db_file) as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS internal_order_payment_snapshots_v01(
              order_id TEXT PRIMARY KEY,
              warehouse_id INTEGER NOT NULL,
              warehouse_order_number TEXT,
              client_id TEXT NOT NULL,
              customer_amount_rub INTEGER NOT NULL CHECK(customer_amount_rub >= 0),
              partner_amount_rub INTEGER NOT NULL CHECK(partner_amount_rub >= 0),
              payment_route TEXT NOT NULL,
              order_status_snapshot TEXT NOT NULL,
              created_by TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS internal_order_terms_integrity_v01(
              order_id TEXT PRIMARY KEY,
              fingerprint_version TEXT NOT NULL,
              fingerprint_sha256 TEXT NOT NULL,
              item_count INTEGER NOT NULL CHECK(item_count > 0),
              created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS internal_order_payment_state_v01(
              order_id TEXT PRIMARY KEY,
              payment_status TEXT NOT NULL CHECK(payment_status IN ('DUE','PAID')),
              confirmation_id TEXT UNIQUE,
              confirmed_amount_rub INTEGER,
              actual_actor TEXT,
              effective_subject TEXT,
              confirmed_at TEXT
            );

            CREATE TABLE IF NOT EXISTS internal_order_audit_v01(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id TEXT NOT NULL,
              warehouse_id INTEGER NOT NULL,
              action TEXT NOT NULL,
              actual_actor TEXT NOT NULL,
              effective_subject TEXT NOT NULL,
              idempotency_key TEXT NOT NULL UNIQUE,
              details_json TEXT NOT NULL,
              created_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_internal_order_audit_order
              ON internal_order_audit_v01(order_id,id);
            """
        )


def _order_columns(c) -> set[str]:
    return {str(row[1]) for row in c.execute("PRAGMA table_info(orders)")}


def _load_shared_order(c, order_id: str):
    cols = _order_columns(c)
    if "order_id" not in cols:
        raise RuntimeError("shared orders schema missing order_id")
    select = ["order_id"]
    if "status" in cols:
        select.append("status")
    else:
        raise RuntimeError("shared orders schema missing status")
    row = c.execute(
        f"SELECT {','.join(select)} FROM orders WHERE order_id=?",
        (order_id,),
    ).fetchone()
    if row is None:
        raise KeyError("internal order not found")
    return {
        "order_id": str(row[0]),
        "status": str(row[1] or ""),
    }


def _order_item_columns(c) -> set[str]:
    return {str(row[1]) for row in c.execute("PRAGMA table_info(order_items)")}


def _canonical_internal_terms(c, order_id: str) -> dict:
    """Return strict authoritative line facts + canonical fingerprint payload."""
    required = {
        "id",
        "order_id",
        "offer_source",
        "warehouse_id",
        "oem",
        "quantity",
        "customer_unit_rub",
        "payment_route",
    }
    cols = _order_item_columns(c)
    missing = sorted(required - cols)
    if missing:
        raise RuntimeError(
            "shared order_items schema missing terms-integrity columns: "
            + ",".join(missing)
        )

    rows = c.execute(
        """SELECT id,offer_source,warehouse_id,oem,quantity,
                  customer_unit_rub,payment_route
           FROM order_items
           WHERE order_id=?
           ORDER BY id""",
        (order_id,),
    ).fetchall()
    if not rows:
        raise ValueError("internal order has no items")

    warehouse_ids = set()
    routes = set()
    customer_total = 0
    canonical_items = []

    for item_id, source, warehouse_id, oem, quantity, customer_unit_rub, route in rows:
        item_id = _strict_positive_int(item_id, "order_item_id")
        source = str(source or "").strip().lower()
        if source != "warehouse":
            raise ValueError("INTERNAL_ORDER_MUST_CONTAIN_ONLY_WAREHOUSE_LINES")
        warehouse_id = _strict_positive_int(warehouse_id, "warehouse_id")
        oem = str(oem or "").strip()
        if not oem:
            raise ValueError("internal order OEM snapshot missing")
        qty = _strict_positive_int(quantity, "quantity")
        unit_rub = _strict_whole_rub(customer_unit_rub, "customer_unit_rub")
        try:
            route_value = PaymentRoute(str(route or "").strip()).value
        except ValueError as exc:
            raise ValueError("internal order payment_route snapshot invalid") from exc

        warehouse_ids.add(warehouse_id)
        routes.add(route_value)
        customer_total += unit_rub * qty
        canonical_items.append(
            {
                "customer_unit_rub": unit_rub,
                "item_id": item_id,
                "oem": oem,
                "offer_source": source,
                "payment_route": route_value,
                "quantity": qty,
                "warehouse_id": warehouse_id,
            }
        )

    if len(warehouse_ids) != 1:
        raise ValueError("INTERNAL_ORDER_MUST_TARGET_ONE_WAREHOUSE")
    if len(routes) != 1:
        raise ValueError("INTERNAL_ORDER_PAYMENT_ROUTE_MUST_BE_UNIFORM")

    payload = json.dumps(
        canonical_items,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    fingerprint = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "warehouse_id": next(iter(warehouse_ids)),
        "customer_amount_rub": customer_total,
        "payment_route": next(iter(routes)),
        "item_count": len(canonical_items),
        "fingerprint_version": TERMS_FINGERPRINT_VERSION,
        "fingerprint_sha256": fingerprint,
        "canonical_items": canonical_items,
    }


def _load_internal_line_facts(c, order_id: str) -> dict:
    terms = _canonical_internal_terms(c, order_id)
    return {
        "warehouse_id": terms["warehouse_id"],
        "customer_amount_rub": terms["customer_amount_rub"],
        "payment_route": terms["payment_route"],
    }


def _integrity_row(c, order_id: str):
    return c.execute(
        """SELECT order_id,fingerprint_version,fingerprint_sha256,item_count,created_at
           FROM internal_order_terms_integrity_v01
           WHERE order_id=?""",
        (order_id,),
    ).fetchone()


def _assert_terms_integrity(c, order_id: str) -> dict:
    proof = _integrity_row(c, order_id)
    if proof is None:
        raise ValueError("INTERNAL_TERMS_INTEGRITY_PROOF_REQUIRED")
    current = _canonical_internal_terms(c, order_id)
    same = (
        str(proof[1]) == current["fingerprint_version"]
        and str(proof[2]) == current["fingerprint_sha256"]
        and int(proof[3]) == int(current["item_count"])
    )
    if not same:
        raise ValueError("INTERNAL_ORDER_TERMS_DRIFT")
    return current


def _snapshot_row(c, order_id: str):
    return c.execute(
        """SELECT order_id,warehouse_id,warehouse_order_number,client_id,
                  customer_amount_rub,partner_amount_rub,payment_route,
                  order_status_snapshot,created_by,created_at
           FROM internal_order_payment_snapshots_v01
           WHERE order_id=?""",
        (order_id,),
    ).fetchone()


def get_payment_snapshot(db_file, order_id: str, *, conn=None) -> dict:
    order_id = _text(order_id, "order_id")
    own = conn is None
    c = sqlite3.connect(db_file) if own else conn
    try:
        row = _snapshot_row(c, order_id)
        if row is None:
            raise KeyError("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
        names = [
            "order_id","warehouse_id","warehouse_order_number","client_id",
            "customer_amount_rub","partner_amount_rub","payment_route",
            "order_status_snapshot","created_by","created_at",
        ]
        return dict(zip(names, row))
    finally:
        if own:
            c.close()


def create_payment_snapshot(
    db_file,
    *,
    order_id: str,
    client_id: str,
    partner_amount_rub: int,
    created_by: str,
    warehouse_order_number: str | None = None,
    expected_warehouse_id: int | None = None,
    conn=None,
) -> dict:
    """Create the immutable Internal payment/terms snapshot.

    C and warehouse/payment route are derived from existing persisted order
    lines. P and client identity must come from trusted server context.
    """
    order_id = _text(order_id, "order_id")
    client_id = _text(client_id, "client_id")
    created_by = _text(created_by, "created_by")
    partner_amount_rub = _strict_whole_rub(
        partner_amount_rub, "partner_amount_rub"
    )
    warehouse_order_number = str(warehouse_order_number or "").strip() or None

    own = conn is None
    if own:
        init(db_file)
        c = sqlite3.connect(db_file, timeout=30)
        c.execute("BEGIN IMMEDIATE")
    else:
        c = conn

    ok = False
    try:
        order = _load_shared_order(c, order_id)
        line_facts = _load_internal_line_facts(c, order_id)
        warehouse_id = int(line_facts["warehouse_id"])
        if expected_warehouse_id is not None:
            expected_warehouse_id = _strict_positive_int(
                expected_warehouse_id, "expected_warehouse_id"
            )
            if expected_warehouse_id != warehouse_id:
                raise PermissionError("INTERNAL_ORDER_WAREHOUSE_SCOPE_MISMATCH")

        existing = _snapshot_row(c, order_id)
        candidate = (
            order_id,
            warehouse_id,
            warehouse_order_number,
            client_id,
            int(line_facts["customer_amount_rub"]),
            partner_amount_rub,
            str(line_facts["payment_route"]),
            str(order["status"]),
            created_by,
        )
        if existing is not None:
            same = tuple(existing[:9]) == candidate
            if not same:
                raise ValueError("internal payment snapshot conflict")
            # Compatibility: legacy P2/P3 snapshots remain readable. If they
            # predate P4 and have no proof, do not silently backfill one.
            if _integrity_row(c, order_id) is not None:
                _assert_terms_integrity(c, order_id)
            result = get_payment_snapshot(db_file, order_id, conn=c)
            ok = True
            return result

        now = _now()
        c.execute(
            """INSERT INTO internal_order_payment_snapshots_v01(
                 order_id,warehouse_id,warehouse_order_number,client_id,
                 customer_amount_rub,partner_amount_rub,payment_route,
                 order_status_snapshot,created_by,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            candidate + (now,),
        )
        terms = _canonical_internal_terms(c, order_id)
        c.execute(
            """INSERT INTO internal_order_terms_integrity_v01(
                 order_id,fingerprint_version,fingerprint_sha256,item_count,created_at)
               VALUES(?,?,?,?,?)""",
            (
                order_id,
                terms["fingerprint_version"],
                terms["fingerprint_sha256"],
                int(terms["item_count"]),
                now,
            ),
        )
        c.execute(
            """INSERT INTO internal_order_payment_state_v01(
                 order_id,payment_status)
               VALUES(?,?)""",
            (order_id, PAYMENT_DUE),
        )
        result = get_payment_snapshot(db_file, order_id, conn=c)
        ok = True
        return result
    finally:
        if own:
            try:
                c.commit() if ok else c.rollback()
            finally:
                c.close()


def _payment_state(c, order_id: str):
    return c.execute(
        """SELECT order_id,payment_status,confirmation_id,confirmed_amount_rub,
                  actual_actor,effective_subject,confirmed_at
           FROM internal_order_payment_state_v01
           WHERE order_id=?""",
        (order_id,),
    ).fetchone()


def _audit_by_key(c, key: str):
    return c.execute(
        """SELECT id,order_id,warehouse_id,action,actual_actor,effective_subject,
                  idempotency_key,details_json,created_at
           FROM internal_order_audit_v01
           WHERE idempotency_key=?""",
        (key,),
    ).fetchone()


def _insert_audit(
    c,
    *,
    order_id: str,
    warehouse_id: int,
    action: str,
    actual_actor: str,
    effective_subject: str,
    idempotency_key: str,
    details: dict,
) -> int:
    payload = json.dumps(details, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    existing = _audit_by_key(c, idempotency_key)
    if existing:
        same = (
            str(existing[1]) == order_id
            and int(existing[2]) == int(warehouse_id)
            and str(existing[3]) == action
            and str(existing[4]) == actual_actor
            and str(existing[5]) == effective_subject
            and str(existing[7]) == payload
        )
        if not same:
            raise ValueError("internal audit idempotency conflict")
        return int(existing[0])
    cur = c.execute(
        """INSERT INTO internal_order_audit_v01(
             order_id,warehouse_id,action,actual_actor,effective_subject,
             idempotency_key,details_json,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            order_id,
            int(warehouse_id),
            action,
            actual_actor,
            effective_subject,
            idempotency_key,
            payload,
            _now(),
        ),
    )
    return int(cur.lastrowid)


def confirm_internal_direct_payment(
    db_file,
    *,
    order_id: str,
    confirmation_id: str,
    actor_context: WarehouseActorContext,
    payment_confirmed: bool,
    confirmed_amount_rub: int,
    _failure_hook=None,
) -> dict:
    """Prototype one-transaction direct warehouse payment confirmation.

    The actor context is assumed to have been produced by server authorization;
    this prototype validates the supplied facts but does not authenticate users.
    """
    order_id = _text(order_id, "order_id")
    confirmation_id = _text(confirmation_id, "confirmation_id")
    if not isinstance(actor_context, WarehouseActorContext):
        raise TypeError("actor_context must be WarehouseActorContext")
    actual_actor = _text(actor_context.actual_actor, "actual_actor")
    effective_subject = _text(actor_context.effective_subject, "effective_subject")
    if not actor_context.authorized:
        raise PermissionError("WAREHOUSE_ACTOR_NOT_AUTHORIZED")
    if not payment_confirmed:
        raise ValueError("FULL_PAYMENT_CONFIRMATION_REQUIRED")
    confirmed_amount_rub = _strict_whole_rub(
        confirmed_amount_rub, "confirmed_amount_rub"
    )

    init(db_file)
    c = sqlite3.connect(db_file, timeout=30)
    try:
        c.execute("BEGIN IMMEDIATE")

        order = _load_shared_order(c, order_id)
        snapshot_row = _snapshot_row(c, order_id)
        if snapshot_row is None:
            raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
        snapshot = get_payment_snapshot(db_file, order_id, conn=c)
        current_terms = _assert_terms_integrity(c, order_id)

        if int(current_terms["warehouse_id"]) != int(snapshot["warehouse_id"]):
            raise ValueError("INTERNAL_ORDER_TERMS_DRIFT")
        if int(current_terms["customer_amount_rub"]) != int(snapshot["customer_amount_rub"]):
            raise ValueError("INTERNAL_ORDER_TERMS_DRIFT")
        if str(current_terms["payment_route"]) != str(snapshot["payment_route"]):
            raise ValueError("INTERNAL_ORDER_TERMS_DRIFT")

        actor_warehouse_id = _strict_positive_int(
            actor_context.warehouse_id, "actor_context.warehouse_id"
        )
        if actor_warehouse_id != int(snapshot["warehouse_id"]):
            raise PermissionError("CROSS_WAREHOUSE_PAYMENT_CONFIRMATION_DENIED")
        if str(snapshot["payment_route"]) != PaymentRoute.DIRECT_PARTNER.value:
            raise ValueError("INTERNAL_DIRECT_CONFIRMATION_REQUIRES_DIRECT_PARTNER")
        if str(order["status"]) != str(snapshot["order_status_snapshot"]):
            raise ValueError("INTERNAL_ORDER_STATUS_CHANGED_SINCE_PAYMENT_SNAPSHOT")
        if confirmed_amount_rub != int(snapshot["customer_amount_rub"]):
            raise ValueError("FULL_PAYMENT_AMOUNT_MISMATCH")

        state = _payment_state(c, order_id)
        if state is None:
            raise RuntimeError("internal payment state missing")

        expected_info_reference = f"internal-direct:{confirmation_id}"
        audit_key = f"INTERNAL_DIRECT_PAYMENT_CONFIRM:{confirmation_id}"
        details = {
            "confirmation_id": confirmation_id,
            "client_id": str(snapshot["client_id"]),
            "customer_amount_rub": int(snapshot["customer_amount_rub"]),
            "partner_amount_rub": int(snapshot["partner_amount_rub"]),
            "payment_route": str(snapshot["payment_route"]),
            "warehouse_id": int(snapshot["warehouse_id"]),
        }

        if str(state[1]) == PAYMENT_PAID:
            same = (
                str(state[2]) == confirmation_id
                and int(state[3]) == confirmed_amount_rub
                and str(state[4]) == actual_actor
                and str(state[5]) == effective_subject
            )
            if not same:
                raise ValueError("internal direct payment confirmation conflict")
        elif str(state[1]) == PAYMENT_DUE:
            c.execute(
                """UPDATE internal_order_payment_state_v01
                   SET payment_status=?,confirmation_id=?,confirmed_amount_rub=?,
                       actual_actor=?,effective_subject=?,confirmed_at=?
                   WHERE order_id=? AND payment_status=?""",
                (
                    PAYMENT_PAID,
                    confirmation_id,
                    confirmed_amount_rub,
                    actual_actor,
                    effective_subject,
                    _now(),
                    order_id,
                    PAYMENT_DUE,
                ),
            )
            if c.total_changes < 1:
                raise RuntimeError("internal payment state transition failed")
        else:
            raise ValueError("unsupported internal payment status")

        if _failure_hook:
            _failure_hook("payment_state")

        balance_result = warehouse_balance.confirm_direct_payment_and_accrue(
            db_file,
            confirmation_id=confirmation_id,
            warehouse_id=int(snapshot["warehouse_id"]),
            order_id=order_id,
            customer_amount_rub=int(snapshot["customer_amount_rub"]),
            partner_amount_rub=int(snapshot["partner_amount_rub"]),
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            conn=c,
        )

        if _failure_hook:
            _failure_hook("warehouse_accrual")

        info_event_id = client_info.record_direct_payment(
            db_file,
            client_id=str(snapshot["client_id"]),
            order_id=order_id,
            warehouse_id=int(snapshot["warehouse_id"]),
            external_amount_rub=int(snapshot["customer_amount_rub"]),
            reference_id=expected_info_reference,
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            description=f"Direct partner payment for {order_id}",
            conn=c,
        )

        if _failure_hook:
            _failure_hook("client_info")

        audit_id = _insert_audit(
            c,
            order_id=order_id,
            warehouse_id=int(snapshot["warehouse_id"]),
            action="CONFIRM_INTERNAL_DIRECT_PAYMENT",
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            idempotency_key=audit_key,
            details=details,
        )

        if _failure_hook:
            _failure_hook("audit")

        c.commit()
        return {
            "order_id": order_id,
            "warehouse_id": int(snapshot["warehouse_id"]),
            "client_id": str(snapshot["client_id"]),
            "payment_status": PAYMENT_PAID,
            "confirmation_id": confirmation_id,
            "customer_amount_rub": int(snapshot["customer_amount_rub"]),
            "partner_amount_rub": int(snapshot["partner_amount_rub"]),
            "difference_rub": int(balance_result["difference_rub"]),
            "client_info_event_id": int(info_event_id),
            "audit_event_id": int(audit_id),
        }
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def prepare_admin_extremizer_exception(
    db_file,
    *,
    order_id: str,
    reservation_id: str,
    admin_context: AdminActorContext,
) -> dict:
    """Isolated proof for the CLIENT -> EXTREMIZER exception.

    It reserves the full P for the order and returns the proposed route.
    It does not mutate the persisted order/payment snapshot route and does not
    charge a real client.
    """
    order_id = _text(order_id, "order_id")
    reservation_id = _text(reservation_id, "reservation_id")
    if not isinstance(admin_context, AdminActorContext):
        raise TypeError("admin_context must be AdminActorContext")
    actual_actor = _text(admin_context.actual_actor, "actual_actor")
    effective_subject = _text(admin_context.effective_subject, "effective_subject")
    if not admin_context.authorized:
        raise PermissionError("ADMIN_NOT_AUTHORIZED_FOR_INTERNAL_ROUTE_EXCEPTION")

    init(db_file)
    c = sqlite3.connect(db_file, timeout=30)
    try:
        c.execute("BEGIN IMMEDIATE")
        snapshot_row = _snapshot_row(c, order_id)
        if snapshot_row is None:
            raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
        snapshot = get_payment_snapshot(db_file, order_id, conn=c)
        current_terms = _assert_terms_integrity(c, order_id)
        if (
            int(current_terms["warehouse_id"]) != int(snapshot["warehouse_id"])
            or int(current_terms["customer_amount_rub"]) != int(snapshot["customer_amount_rub"])
            or str(current_terms["payment_route"]) != str(snapshot["payment_route"])
        ):
            raise ValueError("INTERNAL_ORDER_TERMS_DRIFT")
        reservation = warehouse_balance.reserve_full_balance(
            db_file,
            reservation_id=reservation_id,
            warehouse_id=int(snapshot["warehouse_id"]),
            order_id=order_id,
            required_amount_rub=int(snapshot["partner_amount_rub"]),
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            conn=c,
        )
        proposed = resolve_internal_client_payment_route(
            admin_assign_extremizer=True,
            admin_authorized=True,
            full_partner_amount_reserved=(
                str(reservation["status"]) == "reserved"
                and int(reservation["amount_rub"]) == int(snapshot["partner_amount_rub"])
            ),
        )
        _insert_audit(
            c,
            order_id=order_id,
            warehouse_id=int(snapshot["warehouse_id"]),
            action="PREPARE_INTERNAL_EXTREMIZER_ROUTE_EXCEPTION",
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            idempotency_key=f"INTERNAL_EXTREMIZER_ROUTE_RESERVE:{reservation_id}",
            details={
                "reservation_id": reservation_id,
                "partner_amount_rub": int(snapshot["partner_amount_rub"]),
                "proposed_payment_route": proposed.value,
            },
        )
        c.commit()
        return {
            "order_id": order_id,
            "warehouse_id": int(snapshot["warehouse_id"]),
            "partner_amount_rub": int(snapshot["partner_amount_rub"]),
            "reservation_id": reservation_id,
            "reservation_status": str(reservation["status"]),
            "proposed_payment_route": proposed.value,
            "persisted_payment_route": str(snapshot["payment_route"]),
        }
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


__all__ = [
    "WarehouseActorContext",
    "AdminActorContext",
    "init",
    "create_payment_snapshot",
    "get_payment_snapshot",
    "TERMS_FINGERPRINT_VERSION",
    "confirm_internal_direct_payment",
    "prepare_admin_extremizer_exception",
]
