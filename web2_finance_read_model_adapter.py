#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict WEB2 presentation adapter for authoritative Internal Order P3 reads.

Consumes internal_order_read_v01 contract v0.1 only. This module performs
validation/presentation mapping only: no Finance/Core writes, money arithmetic,
authentication, ledger mutation, payment mutation, or order mutation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

P3_CONTRACT_VERSION = "internal-order-read-v0.1"
VALID_PAYMENT_STATUSES = frozenset({"DUE", "PAID"})
AUTHORITATIVE_P_SOURCES = frozenset({
    "internal_payment_snapshot",
    "direct_payment_confirmation",
    "warehouse_balance_reservation",
})

BUSINESS_STATUS_TO_KEY = {
    "Новый": "new",
    "Собран, не оплачен": "assembled-unpaid",
    "Собран, оплачен": "assembled-paid",
    "Отгружен": "shipped",
}

# P4 proof maps only technical shared orders.status values evidenced by the
# authoritative P3/Core regressions. Do not guess payment-ready/shipped states.
SERVER_STATUS_TO_UI = {
    "new": "Новый",
    "assembled_unpaid": "Собран, не оплачен",
}


class ReadModelContractError(ValueError):
    pass


@dataclass(frozen=True)
class WarehouseUiSummary:
    warehouse_id: int
    posted_rub: int
    reserved_rub: int
    available_rub: int
    full_history_balance_rub: int


@dataclass(frozen=True)
class WarehouseUiRow:
    warehouse_id: int
    date: str | None
    extremizer_order_no: str
    warehouse_order_no: str | None
    customer_amount_rub: int | None
    partner_amount_rub: int | None
    partner_amount_authoritative: bool
    payment_source: str | None
    payment_status: str | None
    payment_route: str | None
    balance_delta_rub: int | None
    customer_amount_struck: bool
    status: str | None
    status_key: str | None
    canonical_client_id: str | None
    client_display: str | None
    oems: tuple[str, ...]


@dataclass(frozen=True)
class WarehouseSystemError:
    code: str
    message: str
    extremizer_order_no: str | None = None
    warehouse_order_no: str | None = None


@dataclass(frozen=True)
class WarehouseUiReadModel:
    demo_mode: bool
    summary: WarehouseUiSummary
    rows: tuple[WarehouseUiRow, ...]
    system_errors: tuple[WarehouseSystemError, ...] = ()


def _required(m: Mapping[str, Any], key: str) -> Any:
    if key not in m:
        raise ReadModelContractError(f"MISSING_REQUIRED_FIELD:{key}")
    return m[key]


def _text_required(m: Mapping[str, Any], key: str) -> str:
    value = _required(m, key)
    if not isinstance(value, str) or not value.strip():
        raise ReadModelContractError(f"MISSING_REQUIRED_TEXT:{key}")
    return value


def _optional_text(m: Mapping[str, Any], key: str) -> str | None:
    value = m.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReadModelContractError(f"INVALID_TEXT:{key}")
    return value if value else None


def _exact_int(value: Any, key: str) -> int:
    # JSON contract says integer. Never coerce bool, float, Decimal or strings:
    # int(1.9), int("1"), NaN/Infinity-like values must not cross this boundary.
    if type(value) is not int:
        raise ReadModelContractError(f"INVALID_INTEGER:{key}")
    return value


def _int_required(
    m: Mapping[str, Any],
    key: str,
    *,
    nonnegative: bool = False,
    positive: bool = False,
) -> int:
    value = _exact_int(_required(m, key), key)
    if nonnegative and value < 0:
        raise ReadModelContractError(f"NEGATIVE_NOT_ALLOWED:{key}")
    if positive and value <= 0:
        raise ReadModelContractError(f"NONPOSITIVE_NOT_ALLOWED:{key}")
    return value


def _optional_int(
    m: Mapping[str, Any],
    key: str,
    *,
    nonnegative: bool = False,
) -> int | None:
    value = m.get(key)
    if value is None:
        return None
    value = _exact_int(value, key)
    if nonnegative and value < 0:
        raise ReadModelContractError(f"NEGATIVE_NOT_ALLOWED:{key}")
    return value


def _validate_envelope(
    server_history: Mapping[str, Any],
) -> tuple[int, Mapping[str, Any], Sequence[Any]]:
    version = _text_required(server_history, "contract_version")
    if version != P3_CONTRACT_VERSION:
        raise ReadModelContractError(f"UNSUPPORTED_CONTRACT_VERSION:{version}")

    scope = _required(server_history, "scope")
    summary = _required(server_history, "summary")
    rows = _required(server_history, "rows")
    if not isinstance(scope, Mapping):
        raise ReadModelContractError("INVALID_SCOPE")
    if not isinstance(summary, Mapping):
        raise ReadModelContractError("INVALID_SUMMARY")
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        raise ReadModelContractError("INVALID_ROWS")

    scope_warehouse = _int_required(scope, "warehouse_id", positive=True)
    summary_warehouse = _int_required(summary, "warehouse_id", positive=True)
    if scope_warehouse != summary_warehouse:
        raise ReadModelContractError("SUMMARY_SCOPE_MISMATCH")
    return scope_warehouse, summary, rows


def _status(raw_status: Any, order_id: str) -> tuple[str | None, str | None]:
    if raw_status is None:
        return None, None
    if not isinstance(raw_status, str):
        raise ReadModelContractError(f"INVALID_ORDER_STATUS:{order_id}")
    ui = SERVER_STATUS_TO_UI.get(raw_status)
    if ui is None:
        raise ReadModelContractError(
            f"UNSUPPORTED_ORDER_STATUS:{order_id}:{raw_status}"
        )
    return ui, BUSINESS_STATUS_TO_KEY[ui]


def _payment_status(raw: Any) -> str | None:
    if raw is None:
        return None
    if not isinstance(raw, str) or raw not in VALID_PAYMENT_STATUSES:
        raise ReadModelContractError(f"INVALID_PAYMENT_STATUS:{raw}")
    return raw


def _partner(
    raw: Mapping[str, Any],
) -> tuple[int | None, str | None, bool]:
    amount = _optional_int(raw, "partner_amount_rub", nonnegative=True)
    source = _optional_text(raw, "partner_amount_source")
    balance = _optional_int(raw, "balance_rub")  # signed ledger effect is valid

    if source is None:
        if amount is not None:
            raise ReadModelContractError("PARTNER_AMOUNT_WITHOUT_SOURCE")
        if balance is not None and balance != 0:
            raise ReadModelContractError(
                "POSTED_EFFECT_WITHOUT_AUTHORITATIVE_P"
            )
        return None, None, False

    if source not in AUTHORITATIVE_P_SOURCES:
        raise ReadModelContractError(
            f"UNAPPROVED_PARTNER_AMOUNT_SOURCE:{source}"
        )
    if amount is None:
        raise ReadModelContractError("PARTNER_AMOUNT_SOURCE_WITHOUT_P")
    return amount, source, True


def _trusted_display_name(
    client_display_by_id: Mapping[str, str] | None,
    canonical_client_id: str | None,
) -> str | None:
    if canonical_client_id is None or client_display_by_id is None:
        return None
    if canonical_client_id not in client_display_by_id:
        return None
    value = client_display_by_id[canonical_client_id]
    if not isinstance(value, str):
        raise ReadModelContractError("INVALID_TRUSTED_CLIENT_DISPLAY")
    value = value.strip()
    return value or None


def _strict_order_metadata(
    strict_orders_by_id: Mapping[str, Mapping[str, Any]],
    *,
    order_id: str,
    expected_warehouse_id: int,
    history_row: Mapping[str, Any],
) -> tuple[str | None, tuple[str, ...]]:
    strict = strict_orders_by_id.get(order_id)
    if not isinstance(strict, Mapping):
        raise ReadModelContractError(f"STRICT_ORDER_REQUIRED:{order_id}")
    version = _text_required(strict, "contract_version")
    if version != P3_CONTRACT_VERSION:
        raise ReadModelContractError(
            f"STRICT_ORDER_VERSION_MISMATCH:{order_id}"
        )
    scope = _required(strict, "scope")
    order = _required(strict, "order")
    items = _required(strict, "items")
    if not isinstance(scope, Mapping) or not isinstance(order, Mapping):
        raise ReadModelContractError(f"INVALID_STRICT_ORDER:{order_id}")
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes)):
        raise ReadModelContractError(f"INVALID_STRICT_ITEMS:{order_id}")
    if _int_required(scope, "warehouse_id", positive=True) != expected_warehouse_id:
        raise ReadModelContractError(
            f"STRICT_ORDER_SCOPE_MISMATCH:{order_id}"
        )
    if _int_required(order, "warehouse_id", positive=True) != expected_warehouse_id:
        raise ReadModelContractError(
            f"STRICT_ORDER_SCOPE_MISMATCH:{order_id}"
        )
    if _text_required(order, "extremizer_order_number") != order_id:
        raise ReadModelContractError(f"STRICT_ORDER_ID_MISMATCH:{order_id}")

    # Validate money types/ranges on the strict payload too; Python equality
    # would otherwise accept 16575.0 == 16575.
    strict_c = _int_required(order, "customer_amount_rub", nonnegative=True)
    strict_p = _int_required(order, "partner_amount_rub", nonnegative=True)
    strict_balance = _optional_int(order, "balance_rub")
    history_c = _optional_int(
        history_row, "customer_amount_rub", nonnegative=True
    )
    history_p = _optional_int(
        history_row, "partner_amount_rub", nonnegative=True
    )
    history_balance = _optional_int(history_row, "balance_rub")
    if strict_c != history_c:
        raise ReadModelContractError(
            f"STRICT_ORDER_HISTORY_MISMATCH:{order_id}:customer_amount_rub"
        )
    if strict_p != history_p:
        raise ReadModelContractError(
            f"STRICT_ORDER_HISTORY_MISMATCH:{order_id}:partner_amount_rub"
        )
    if strict_balance != history_balance:
        raise ReadModelContractError(
            f"STRICT_ORDER_HISTORY_MISMATCH:{order_id}:balance_rub"
        )

    for field in (
        "warehouse_order_number",
        "partner_amount_source",
        "payment_status",
        "payment_route",
    ):
        if order.get(field) != history_row.get(field):
            raise ReadModelContractError(
                f"STRICT_ORDER_HISTORY_MISMATCH:{order_id}:{field}"
            )

    canonical_client_id = _optional_text(order, "client_id")
    oems: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            raise ReadModelContractError(f"INVALID_STRICT_ITEM:{order_id}")
        candidate = item.get("oem") or item.get("requested_oem")
        if candidate is not None:
            if not isinstance(candidate, str):
                raise ReadModelContractError(
                    f"INVALID_STRICT_ITEM_OEM:{order_id}"
                )
            if candidate:
                oems.append(candidate)
    return canonical_client_id, tuple(oems)


def adapt_warehouse_history(
    server_history: Mapping[str, Any],
    *,
    strict_orders_by_id: Mapping[str, Mapping[str, Any]] | None = None,
    client_display_by_id: Mapping[str, str] | None = None,
    system_errors: Sequence[Mapping[str, Any]] = (),
    demo_mode: bool = False,
) -> WarehouseUiReadModel:
    """Adapt real internal_order_read_v01.read_warehouse_history() response.

    Snapshot-backed rows require a matching strict read_order() payload.
    Canonical client_id is never used as a display name; display names require a
    separately supplied trusted scoped client directory projection.
    """
    scope_warehouse, summary_raw, rows_raw = _validate_envelope(server_history)
    strict_orders_by_id = strict_orders_by_id or {}

    posted = _int_required(summary_raw, "posted_balance_rub")
    reserved = _int_required(
        summary_raw, "reserved_balance_rub", nonnegative=True
    )
    available = _int_required(summary_raw, "available_balance_rub")
    if available != posted - reserved:
        raise ReadModelContractError("AVAILABLE_BALANCE_INVARIANT_MISMATCH")

    summary = WarehouseUiSummary(
        warehouse_id=scope_warehouse,
        posted_rub=posted,
        reserved_rub=reserved,
        available_rub=available,  # preserve server value after validation
        full_history_balance_rub=posted,
    )

    mapped: list[WarehouseUiRow] = []
    for raw in rows_raw:
        if not isinstance(raw, Mapping):
            raise ReadModelContractError("INVALID_ROW")
        order_id = _text_required(raw, "extremizer_order_number")
        warehouse_id = _int_required(raw, "warehouse_id", positive=True)
        if warehouse_id != scope_warehouse:
            raise ReadModelContractError(
                f"WAREHOUSE_SCOPE_MISMATCH:{order_id}:{warehouse_id}"
            )

        warehouse_order_no = _optional_text(
            raw, "warehouse_order_number"
        )
        date = _optional_text(raw, "date")
        customer_amount = _optional_int(
            raw, "customer_amount_rub", nonnegative=True
        )
        partner_amount, source, partner_authoritative = _partner(raw)
        pay_status = _payment_status(raw.get("payment_status"))
        payment_route = _optional_text(raw, "payment_route")
        balance_delta = _optional_int(raw, "balance_rub")
        status_text, status_key = _status(
            raw.get("order_status"), order_id
        )

        has_snapshot_raw = raw.get("internal_payment_snapshot")
        if type(has_snapshot_raw) is not bool:
            raise ReadModelContractError(
                f"INVALID_SNAPSHOT_FLAG:{order_id}"
            )
        has_snapshot = has_snapshot_raw

        canonical_client_id: str | None = None
        oems: tuple[str, ...] = ()
        if has_snapshot:
            canonical_client_id, oems = _strict_order_metadata(
                strict_orders_by_id,
                order_id=order_id,
                expected_warehouse_id=scope_warehouse,
                history_row=raw,
            )
            if source != "internal_payment_snapshot":
                raise ReadModelContractError(
                    f"SNAPSHOT_SOURCE_MISMATCH:{order_id}"
                )
            if pay_status not in VALID_PAYMENT_STATUSES:
                raise ReadModelContractError(
                    f"SNAPSHOT_PAYMENT_STATUS_REQUIRED:{order_id}"
                )
            raw_client_id = _optional_text(raw, "client_id")
            if raw_client_id != canonical_client_id:
                raise ReadModelContractError(
                    f"STRICT_ORDER_HISTORY_MISMATCH:{order_id}:client_id"
                )
        else:
            if source == "internal_payment_snapshot":
                raise ReadModelContractError(
                    f"SNAPSHOT_FLAG_REQUIRED:{order_id}"
                )
            if raw.get("client_id") is not None:
                raise ReadModelContractError(
                    f"UNTRUSTED_CLIENT_ID_WITHOUT_SNAPSHOT:{order_id}"
                )

        client_display = _trusted_display_name(
            client_display_by_id, canonical_client_id
        )

        mapped.append(WarehouseUiRow(
            warehouse_id=warehouse_id,
            date=date,
            extremizer_order_no=order_id,
            warehouse_order_no=warehouse_order_no,
            customer_amount_rub=customer_amount,
            partner_amount_rub=partner_amount,
            partner_amount_authoritative=partner_authoritative,
            payment_source=source,
            payment_status=pay_status,
            payment_route=payment_route,
            balance_delta_rub=balance_delta,
            customer_amount_struck=(
                balance_delta is not None
                and balance_delta < 0
                and partner_amount is not None
            ),
            status=status_text,
            status_key=status_key,
            canonical_client_id=canonical_client_id,
            client_display=client_display,
            oems=oems,
        ))

    errors: list[WarehouseSystemError] = []
    for err in system_errors:
        if not isinstance(err, Mapping):
            raise ReadModelContractError("INVALID_SYSTEM_ERROR")
        errors.append(WarehouseSystemError(
            code=_text_required(err, "code"),
            message=_text_required(err, "message"),
            extremizer_order_no=_optional_text(
                err, "extremizer_order_no"
            ),
            warehouse_order_no=_optional_text(
                err, "warehouse_order_no"
            ),
        ))

    return WarehouseUiReadModel(
        demo_mode=bool(demo_mode),
        summary=summary,
        rows=tuple(mapped),
        system_errors=tuple(errors),
    )


def to_fixture_dict(model: WarehouseUiReadModel) -> dict[str, Any]:
    return {
        "meta": {
            "demoMode": model.demo_mode,
            "serverWiring": "PENDING",
            "contractVersion": P3_CONTRACT_VERSION,
        },
        "warehouse": {
            "warehouseId": model.summary.warehouse_id,
            "summary": {
                "postedRub": model.summary.posted_rub,
                "reservedRub": model.summary.reserved_rub,
                "availableRub": model.summary.available_rub,
                "fullHistoryBalanceRub": model.summary.full_history_balance_rub,
            },
            "rows": [{
                "warehouseId": r.warehouse_id,
                "date": r.date,
                "extremizerOrderNo": r.extremizer_order_no,
                "warehouseOrderNo": r.warehouse_order_no,
                "customerAmountRub": r.customer_amount_rub,
                "partnerAmountRub": r.partner_amount_rub,
                "partnerAmountAuthoritative": r.partner_amount_authoritative,
                "paymentSource": r.payment_source,
                "paymentStatus": r.payment_status,
                "paymentRoute": r.payment_route,
                "balanceDeltaRub": r.balance_delta_rub,
                "customerAmountStruck": r.customer_amount_struck,
                "status": r.status,
                "statusKey": r.status_key,
                "clientId": r.canonical_client_id,
                "client": r.client_display,
                "oems": list(r.oems),
            } for r in model.rows],
            "systemErrors": [{
                "code": e.code,
                "message": e.message,
                "context": {
                    "extremizerOrderNo": e.extremizer_order_no,
                    "warehouseOrderNo": e.warehouse_order_no,
                },
            } for e in model.system_errors],
        },
    }


__all__ = [
    "P3_CONTRACT_VERSION",
    "VALID_PAYMENT_STATUSES",
    "AUTHORITATIVE_P_SOURCES",
    "BUSINESS_STATUS_TO_KEY",
    "SERVER_STATUS_TO_UI",
    "ReadModelContractError",
    "WarehouseUiSummary",
    "WarehouseUiRow",
    "WarehouseSystemError",
    "WarehouseUiReadModel",
    "adapt_warehouse_history",
    "to_fixture_dict",
]
