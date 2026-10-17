#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Internal Order P5 isolated trusted-boundary helpers.

No production route is wired here. The module provides:
- strict pre-persistence validation for Internal line facts;
- an isolated trusted server command with no browser-supplied P argument;
- immutable P provenance verification from P4 evidence;
- read-only payment eligibility evaluation (DUE != PAYABLE).
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import sqlite3
from typing import Any, Iterable

from common_finance_contract import PaymentRoute
from internal_order_core_v01 import (
    PAYMENT_DUE,
    TERMS_FINGERPRINT_VERSION,
    _assert_terms_integrity,
    _snapshot_row,
    _strict_positive_int,
    _strict_whole_rub,
)
from internal_order_terms_v01 import (
    TermsConfirmationContext,
    confirm_terms_and_create_payment_snapshot,
)

CONFIRM_PAYMENT_PERMISSION = "CONFIRM_PAYMENT"
PERSISTENCE_FINGERPRINT_VERSION = "internal-persist-v1"


def _text(value: Any, name: str) -> str:
    if type(value) is not str:
        raise TypeError(f"{name} must be str")
    value = value.strip()
    if not value:
        raise ValueError(f"{name} required")
    return value


@dataclass(frozen=True)
class TrustedTermsCommandContext:
    """Facts a future server auth adapter must mint, never a browser."""
    actual_actor: str
    effective_subject: str
    warehouse_id: int
    authorized: bool
    permissions: frozenset[str]


@dataclass(frozen=True)
class PaymentEligibilityContext:
    actual_actor: str
    warehouse_id: int
    authorized: bool
    permissions: frozenset[str]


def validate_internal_persistence_line(
    *,
    order_id: Any,
    order_item_id: Any,
    offer_source: Any,
    warehouse_id: Any,
    oem: Any,
    quantity: Any,
    customer_unit_rub: Any,
    payment_route: Any,
) -> dict:
    """Strictly validate one future Internal persistence input before SQL."""
    order_id = _text(order_id, "order_id")
    order_item_id = _strict_positive_int(order_item_id, "order_item_id")
    if type(offer_source) is not str:
        raise TypeError("offer_source must be str")
    offer_source = offer_source.strip().lower()
    if offer_source != "warehouse":
        raise ValueError("INTERNAL_ORDER_MUST_CONTAIN_ONLY_WAREHOUSE_LINES")
    warehouse_id = _strict_positive_int(warehouse_id, "warehouse_id")
    oem = _text(oem, "oem")
    quantity = _strict_positive_int(quantity, "quantity")
    customer_unit_rub = _strict_whole_rub(customer_unit_rub, "customer_unit_rub")
    if type(payment_route) is not str:
        raise TypeError("payment_route must be str")
    try:
        route = PaymentRoute(payment_route.strip()).value
    except ValueError as exc:
        raise ValueError("internal order payment_route invalid") from exc

    canonical = {
        "customer_unit_rub": customer_unit_rub,
        "item_id": order_item_id,
        "oem": oem,
        "offer_source": offer_source,
        "order_id": order_id,
        "payment_route": route,
        "quantity": quantity,
        "warehouse_id": warehouse_id,
    }
    payload = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        **canonical,
        "fingerprint_version": PERSISTENCE_FINGERPRINT_VERSION,
        "fingerprint_sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
    }


def create_trusted_terms_payment_snapshot(
    db_file,
    *,
    confirmation_key: str,
    order_id: str,
    client_id: str,
    warehouse_order_number: str | None,
    terms_version: str,
    terms_source_type: str,
    terms_source_ref: str,
    confirmed_quantities: dict,
    context: TrustedTermsCommandContext,
    _failure_hook=None,
) -> dict:
    """Trusted command: confirmed quantities + approved terms -> P -> snapshot.

    There is intentionally no partner_amount_rub parameter.
    """
    if not isinstance(context, TrustedTermsCommandContext):
        raise TypeError("context must be TrustedTermsCommandContext")
    if not isinstance(context.permissions, frozenset):
        raise TypeError("permissions must be frozenset")
    actual_actor = _text(context.actual_actor, "actual_actor")
    effective_subject = _text(context.effective_subject, "effective_subject")
    warehouse_id = _strict_positive_int(context.warehouse_id, "warehouse_id")
    if not context.authorized:
        raise PermissionError("INTERNAL_TERMS_CALLER_NOT_AUTHORIZED")
    if CONFIRM_PAYMENT_PERMISSION not in context.permissions:
        raise PermissionError("CONFIRM_PAYMENT_PERMISSION_REQUIRED")

    return confirm_terms_and_create_payment_snapshot(
        db_file,
        confirmation_key=confirmation_key,
        order_id=order_id,
        client_id=client_id,
        warehouse_order_number=warehouse_order_number,
        terms_version=terms_version,
        terms_source_type=terms_source_type,
        terms_source_ref=terms_source_ref,
        confirmed_quantities=confirmed_quantities,
        actor_context=TermsConfirmationContext(
            actual_actor=actual_actor,
            effective_subject=effective_subject,
            warehouse_id=warehouse_id,
            authorized=True,
        ),
        _failure_hook=_failure_hook,
    )


def _parse_confirmation_key(created_by: Any) -> str:
    marker = "terms-confirmation:"
    if type(created_by) is not str or not created_by.startswith(marker):
        raise ValueError("TRUSTED_TERMS_PROVENANCE_REQUIRED")
    key = created_by[len(marker):].strip()
    if not key:
        raise ValueError("TRUSTED_TERMS_PROVENANCE_REQUIRED")
    return key


def _strict_json_int(value: Any, name: str, *, positive: bool = False) -> int:
    if type(value) is not int:
        raise ValueError(f"TAMPERED_TERMS_EVIDENCE:{name}")
    if positive and value <= 0:
        raise ValueError(f"TAMPERED_TERMS_EVIDENCE:{name}")
    if not positive and value < 0:
        raise ValueError(f"TAMPERED_TERMS_EVIDENCE:{name}")
    return value


def verify_terms_provenance(db_file, order_id: str, *, conn=None) -> dict:
    """Verify snapshot -> confirmation audit -> immutable line evidence."""
    if type(order_id) is not str or not order_id.strip():
        raise ValueError("order_id required")
    order_id = order_id.strip()
    own = conn is None
    c = sqlite3.connect(db_file) if own else conn
    try:
        snapshot = _snapshot_row(c, order_id)
        if snapshot is None:
            raise ValueError("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
        current = _assert_terms_integrity(c, order_id)
        confirmation_key = _parse_confirmation_key(snapshot[8])
        audit = c.execute(
            """SELECT confirmation_key,order_id,warehouse_id,terms_version,
                      terms_source_type,terms_source_ref,actual_actor,
                      effective_subject,calculated_partner_amount_rub,
                      confirmed_lines_json,created_at
               FROM internal_terms_confirmation_audit_v01
               WHERE confirmation_key=?""",
            (confirmation_key,),
        ).fetchone()
        if audit is None:
            raise ValueError("TRUSTED_TERMS_PROVENANCE_REQUIRED")
        if str(audit[1]) != order_id:
            raise ValueError("TERMS_PROVENANCE_ORDER_MISMATCH")
        if int(audit[2]) != int(snapshot[1]):
            raise ValueError("TERMS_PROVENANCE_WAREHOUSE_MISMATCH")
        if int(audit[8]) != int(snapshot[5]):
            raise ValueError("TERMS_PROVENANCE_P_MISMATCH")
        try:
            evidence = json.loads(str(audit[9]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("TAMPERED_TERMS_EVIDENCE") from exc
        if not isinstance(evidence, list) or not evidence:
            raise ValueError("TAMPERED_TERMS_EVIDENCE")

        canonical_by_id = {int(item["item_id"]): item for item in current["canonical_items"]}
        seen = set()
        calculated_p = 0
        canonical_evidence = []
        for line in evidence:
            if not isinstance(line, dict):
                raise ValueError("TAMPERED_TERMS_EVIDENCE")
            item_id = _strict_json_int(line.get("order_item_id"), "order_item_id", positive=True)
            qty = _strict_json_int(line.get("confirmed_quantity"), "confirmed_quantity", positive=True)
            unit = _strict_json_int(line.get("partner_unit_rub"), "partner_unit_rub")
            total = _strict_json_int(line.get("partner_line_rub"), "partner_line_rub")
            if item_id in seen or item_id not in canonical_by_id:
                raise ValueError("TAMPERED_TERMS_EVIDENCE:item_set")
            seen.add(item_id)
            expected = canonical_by_id[item_id]
            if str(line.get("oem") or "") != str(expected["oem"]):
                raise ValueError("TAMPERED_TERMS_EVIDENCE:oem")
            if qty != int(expected["quantity"]):
                raise ValueError("TAMPERED_TERMS_EVIDENCE:quantity")
            if total != unit * qty:
                raise ValueError("TAMPERED_TERMS_EVIDENCE:line_total")
            calculated_p += total
            canonical_evidence.append({
                "order_item_id": item_id,
                "oem": str(expected["oem"]),
                "confirmed_quantity": qty,
                "partner_unit_rub": unit,
                "partner_line_rub": total,
            })

        if seen != set(canonical_by_id):
            raise ValueError("TAMPERED_TERMS_EVIDENCE:item_set")
        if calculated_p != int(snapshot[5]) or calculated_p != int(audit[8]):
            raise ValueError("TERMS_PROVENANCE_P_MISMATCH")
        fingerprint = c.execute(
            "SELECT fingerprint_sha256 FROM internal_order_terms_integrity_v01 WHERE order_id=?",
            (order_id,),
        ).fetchone()
        return {
            "order_id": order_id,
            "warehouse_id": int(snapshot[1]),
            "confirmation_key": confirmation_key,
            "terms_version": str(audit[3]),
            "terms_source_type": str(audit[4]),
            "terms_source_ref": str(audit[5]),
            "calculated_partner_amount_rub": calculated_p,
            "fingerprint_version": TERMS_FINGERPRINT_VERSION,
            "fingerprint_sha256": str(fingerprint[0]),
            "evidence": canonical_evidence,
        }
    finally:
        if own:
            c.close()


def evaluate_payment_eligibility(
    db_file,
    *,
    order_id: str,
    context: PaymentEligibilityContext,
    payment_ready_statuses: Iterable[str] | None = None,
) -> dict:
    """Read-only evaluator. Unknown/missing decision data fails closed."""
    reasons: list[str] = []
    checks: dict[str, bool] = {
        "payment_due": False,
        "trusted_fingerprint": False,
        "fingerprint_matches": False,
        "trusted_terms_provenance": False,
        "payment_route_known": False,
        "payment_ready_status": False,
        "actor_authorized": False,
        "permission_to_execute": False,
    }
    if not isinstance(context, PaymentEligibilityContext):
        raise TypeError("context must be PaymentEligibilityContext")
    if not isinstance(context.permissions, frozenset):
        raise TypeError("permissions must be frozenset")

    if context.authorized:
        checks["actor_authorized"] = True
    else:
        reasons.append("ACTOR_NOT_AUTHORIZED")
    if CONFIRM_PAYMENT_PERMISSION in context.permissions:
        checks["permission_to_execute"] = True
    else:
        reasons.append("CONFIRM_PAYMENT_PERMISSION_REQUIRED")

    try:
        actor_warehouse_id = _strict_positive_int(context.warehouse_id, "context.warehouse_id")
    except (TypeError, ValueError):
        actor_warehouse_id = None
        reasons.append("ACTOR_WAREHOUSE_SCOPE_INVALID")

    with sqlite3.connect(db_file) as c:
        snapshot = _snapshot_row(c, order_id)
        if snapshot is None:
            reasons.append("INTERNAL_PAYMENT_SNAPSHOT_REQUIRED")
            return {"order_id": order_id, "payable": False, "checks": checks, "reasons": reasons}

        if actor_warehouse_id is None or actor_warehouse_id != int(snapshot[1]):
            reasons.append("CROSS_WAREHOUSE_DENY")

        state = c.execute(
            "SELECT payment_status FROM internal_order_payment_state_v01 WHERE order_id=?",
            (order_id,),
        ).fetchone()
        if state is not None and str(state[0]) == PAYMENT_DUE:
            checks["payment_due"] = True
        else:
            reasons.append("PAYMENT_STATUS_NOT_DUE")

        proof = c.execute(
            """SELECT fingerprint_version,fingerprint_sha256,item_count
               FROM internal_order_terms_integrity_v01 WHERE order_id=?""",
            (order_id,),
        ).fetchone()
        if proof is not None:
            checks["trusted_fingerprint"] = True
            try:
                _assert_terms_integrity(c, order_id)
            except (TypeError, ValueError):
                reasons.append("FINGERPRINT_MISMATCH")
            else:
                checks["fingerprint_matches"] = True
        else:
            reasons.append("TRUSTED_FINGERPRINT_REQUIRED")

        try:
            verify_terms_provenance(db_file, order_id, conn=c)
        except (TypeError, ValueError):
            reasons.append("TRUSTED_TERMS_PROVENANCE_REQUIRED")
        else:
            checks["trusted_terms_provenance"] = True

        try:
            PaymentRoute(str(snapshot[6]))
        except ValueError:
            reasons.append("PAYMENT_ROUTE_INVALID")
        else:
            checks["payment_route_known"] = True

        order = c.execute("SELECT status FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if order is None:
            reasons.append("ORDER_STATUS_REQUIRED")
        else:
            status = str(order[0] or "")
            configured = None
            if payment_ready_statuses is not None:
                configured = {str(v).strip() for v in payment_ready_statuses if str(v).strip()}
            if not configured:
                reasons.append("PAYMENT_READY_STATUS_NOT_CONFIGURED")
            elif status in configured:
                checks["payment_ready_status"] = True
            else:
                reasons.append("ORDER_NOT_PAYMENT_READY")

    payable = all(checks.values()) and "CROSS_WAREHOUSE_DENY" not in reasons and not reasons
    return {
        "order_id": order_id,
        "warehouse_id": int(snapshot[1]),
        "payment_status": str(state[0]) if state else None,
        "payment_route": str(snapshot[6]),
        "payable": payable,
        "checks": checks,
        "reasons": reasons,
    }


__all__ = [
    "CONFIRM_PAYMENT_PERMISSION",
    "PERSISTENCE_FINGERPRINT_VERSION",
    "TrustedTermsCommandContext",
    "PaymentEligibilityContext",
    "validate_internal_persistence_line",
    "create_trusted_terms_payment_snapshot",
    "verify_terms_provenance",
    "evaluate_payment_eligibility",
]
