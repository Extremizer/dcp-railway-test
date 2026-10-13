#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed WEB2 adapter for the authoritative warehouse_history_v14 read model.

This module is presentation mapping only. It performs no finance math, writes no
ledger/order data and does not instantiate a FinanceEngine/OrderEngine.

Authoritative source:
    warehouse_history_v14.read_history(...)

Theme 1 remains owner of warehouse_history_v14.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


BUSINESS_STATUS_TO_KEY = {
    "Новый": "new",
    "Собран, не оплачен": "assembled-unpaid",
    "Собран, оплачен": "assembled-paid",
    "Отгружен": "shipped",
}

P2_AUTHORITATIVE_P_SOURCES = frozenset({
    "direct_payment_confirmation",
    "warehouse_balance_reservation",
})
P3_INTERNAL_PAYMENT_SOURCE = "internal_payment_snapshot"
VALID_PAYMENT_STATUSES = frozenset({"DUE", "PAID"})


class ReadModelContractError(ValueError):
    """Raised when the server read model cannot satisfy the UI contract."""


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
    date: str
    extremizer_order_no: str
    warehouse_order_no: str
    customer_amount_rub: int
    partner_amount_rub: int | None
    partner_amount_authoritative: bool
    payment_source: str | None
    payment_status: str | None
    balance_delta_rub: int | None
    customer_amount_struck: bool
    status: str
    status_key: str
    client: str
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


def _required(mapping: Mapping[str, Any], key: str) -> Any:
    if key not in mapping:
        raise ReadModelContractError(f"MISSING_REQUIRED_FIELD:{key}")
    return mapping[key]


def _required_text(mapping: Mapping[str, Any], key: str) -> str:
    value = _required(mapping, key)
    if value is None or not str(value).strip():
        raise ReadModelContractError(f"MISSING_REQUIRED_TEXT:{key}")
    return str(value)


def _int(mapping: Mapping[str, Any], key: str) -> int:
    value = _required(mapping, key)
    if value is None:
        raise ReadModelContractError(f"MISSING_REQUIRED_NUMBER:{key}")
    if isinstance(value, bool):
        raise ReadModelContractError(f"INVALID_NUMBER:{key}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ReadModelContractError(f"INVALID_NUMBER:{key}") from exc


def _optional_int(mapping: Mapping[str, Any], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool):
        raise ReadModelContractError(f"INVALID_NUMBER:{key}")
    try:
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ReadModelContractError(f"INVALID_NUMBER:{key}") from exc


def _metadata_for(
    order_metadata_by_id: Mapping[str, Mapping[str, Any]],
    order_id: str,
    *,
    warehouse_id: int,
) -> Mapping[str, Any]:
    meta = order_metadata_by_id.get(order_id)
    if not isinstance(meta, Mapping):
        raise ReadModelContractError(f"ORDER_METADATA_REQUIRED:{order_id}")
    meta_warehouse_id = _int(meta, "warehouse_id")
    if meta_warehouse_id != warehouse_id:
        raise ReadModelContractError(
            f"ORDER_METADATA_SCOPE_MISMATCH:{order_id}:{meta_warehouse_id}"
        )
    _required(meta, "client")
    _required(meta, "oems")
    _required(meta, "status")
    return meta


def _status(meta: Mapping[str, Any], order_id: str) -> tuple[str, str]:
    text = _required_text(meta, "status")
    if text not in BUSINESS_STATUS_TO_KEY:
        raise ReadModelContractError(f"UNSUPPORTED_ORDER_STATUS:{order_id}:{text}")
    return text, BUSINESS_STATUS_TO_KEY[text]


def _payment_status(row: Mapping[str, Any]) -> str | None:
    raw = row.get("payment_status")
    if raw is None:
        return None
    value = str(raw)
    if value not in VALID_PAYMENT_STATUSES:
        raise ReadModelContractError(f"INVALID_PAYMENT_STATUS:{value}")
    return value


def _partner_authority(
    row: Mapping[str, Any],
    *,
    allow_internal_payment_snapshot_compat: bool,
) -> tuple[int | None, str | None, bool]:
    amount = _optional_int(row, "partner_amount_rub")
    source_raw = row.get("partner_amount_source")
    source = None if source_raw is None else str(source_raw)

    # Never synthesize P. A missing P remains None and the UI renders an em dash.
    balance = _optional_int(row, "balance_rub")
    if source is not None and amount is None:
        raise ReadModelContractError("PARTNER_AMOUNT_SOURCE_WITHOUT_P")
    if balance is not None and amount is None:
        raise ReadModelContractError("POSTED_EFFECT_WITHOUT_AUTHORITATIVE_P")

    if source is None:
        return amount, None, False
    if source in P2_AUTHORITATIVE_P_SOURCES:
        return amount, source, amount is not None
    if source == P3_INTERNAL_PAYMENT_SOURCE:
        if not allow_internal_payment_snapshot_compat:
            raise ReadModelContractError(
                "INTERNAL_PAYMENT_SNAPSHOT_COMPAT_NOT_ENABLED"
            )
        return amount, source, amount is not None
    raise ReadModelContractError(f"UNAPPROVED_PARTNER_AMOUNT_SOURCE:{source}")


def adapt_warehouse_history(
    server_history: Mapping[str, Any],
    *,
    order_metadata_by_id: Mapping[str, Mapping[str, Any]],
    system_errors: Sequence[Mapping[str, Any]] = (),
    demo_mode: bool = False,
    allow_internal_payment_snapshot_compat: bool = False,
) -> WarehouseUiReadModel:
    """Map Theme 1 warehouse_history_v14.read_history() to the WEB2 UI schema.

    order_metadata_by_id is intentionally separate because warehouse_history_v14
    currently owns finance/history fields but does not expose textual business
    order status, client display, or OEM search data. The caller must provide
    those from an authoritative scoped Order Core projection. The adapter never
    infers order status from finance values.

    partner_amount_rub remains None when Theme 1 has no authoritative P.
    """
    summary_raw = _required(server_history, "summary")
    rows_raw = _required(server_history, "rows")
    if not isinstance(summary_raw, Mapping):
        raise ReadModelContractError("INVALID_SUMMARY")
    if not isinstance(rows_raw, Sequence) or isinstance(rows_raw, (str, bytes)):
        raise ReadModelContractError("INVALID_ROWS")

    summary = WarehouseUiSummary(
        warehouse_id=_int(summary_raw, "warehouse_id"),
        posted_rub=_int(summary_raw, "posted_balance_rub"),
        reserved_rub=_int(summary_raw, "reserved_balance_rub"),
        available_rub=_int(summary_raw, "available_balance_rub"),
        # Theme 1 calculates posted_balance_rub from the complete warehouse
        # ledger independently from row search/pagination. This is the approved
        # full-history total displayed under the five-column table.
        full_history_balance_rub=_int(summary_raw, "posted_balance_rub"),
    )

    mapped_rows: list[WarehouseUiRow] = []
    for raw in rows_raw:
        if not isinstance(raw, Mapping):
            raise ReadModelContractError("INVALID_ROW")
        order_id = _required_text(raw, "extremizer_order_number")
        warehouse_order_no = _required_text(raw, "warehouse_order_number")
        date = _required_text(raw, "date")
        row_warehouse_id = _int(raw, "warehouse_id")
        if row_warehouse_id != summary.warehouse_id:
            raise ReadModelContractError(
                f"WAREHOUSE_SCOPE_MISMATCH:{order_id}:{row_warehouse_id}"
            )

        customer_amount = _int(raw, "customer_amount_rub")
        partner_amount, payment_source, partner_authoritative = _partner_authority(
            raw,
            allow_internal_payment_snapshot_compat=allow_internal_payment_snapshot_compat,
        )
        payment_status = _payment_status(raw)
        balance_delta = _optional_int(raw, "balance_rub")

        meta = _metadata_for(
            order_metadata_by_id,
            order_id,
            warehouse_id=row_warehouse_id,
        )
        status_text, status_key = _status(meta, order_id)
        client = str(_required(meta, "client") or "")
        oems_raw = _required(meta, "oems")
        if isinstance(oems_raw, (str, bytes)) or not isinstance(oems_raw, Sequence):
            raise ReadModelContractError(f"INVALID_OEMS:{order_id}")
        oems = tuple(str(value) for value in oems_raw)

        mapped_rows.append(
            WarehouseUiRow(
                warehouse_id=row_warehouse_id,
                date=date,
                extremizer_order_no=order_id,
                warehouse_order_no=warehouse_order_no,
                customer_amount_rub=customer_amount,
                partner_amount_rub=partner_amount,
                partner_amount_authoritative=partner_authoritative,
                payment_source=payment_source,
                payment_status=payment_status,
                balance_delta_rub=balance_delta,
                customer_amount_struck=(
                    balance_delta is not None
                    and balance_delta < 0
                    and payment_source == "warehouse_balance_reservation"
                ),
                status=status_text,
                status_key=status_key,
                client=client,
                oems=oems,
            )
        )

    errors: list[WarehouseSystemError] = []
    for raw_error in system_errors:
        if not isinstance(raw_error, Mapping):
            raise ReadModelContractError("INVALID_SYSTEM_ERROR")
        errors.append(
            WarehouseSystemError(
                code=_required_text(raw_error, "code"),
                message=_required_text(raw_error, "message"),
                extremizer_order_no=(
                    None
                    if raw_error.get("extremizer_order_no") is None
                    else str(raw_error.get("extremizer_order_no"))
                ),
                warehouse_order_no=(
                    None
                    if raw_error.get("warehouse_order_no") is None
                    else str(raw_error.get("warehouse_order_no"))
                ),
            )
        )

    return WarehouseUiReadModel(
        demo_mode=bool(demo_mode),
        summary=summary,
        rows=tuple(mapped_rows),
        system_errors=tuple(errors),
    )


def to_fixture_dict(model: WarehouseUiReadModel) -> dict[str, Any]:
    """Serialize the typed model into the existing camelCase preview contract."""
    return {
        "meta": {
            "demoMode": model.demo_mode,
            "serverWiring": "PENDING",
            "warehouseReadModelSource": "warehouse_history_v14.py",
        },
        "warehouse": {
            "warehouseId": model.summary.warehouse_id,
            "summary": {
                "postedRub": model.summary.posted_rub,
                "reservedRub": model.summary.reserved_rub,
                "availableRub": model.summary.available_rub,
                "fullHistoryBalanceRub": model.summary.full_history_balance_rub,
            },
            "rows": [
                {
                    "warehouseId": row.warehouse_id,
                    "date": row.date,
                    "extremizerOrderNo": row.extremizer_order_no,
                    "warehouseOrderNo": row.warehouse_order_no,
                    "customerAmountRub": row.customer_amount_rub,
                    "partnerAmountRub": row.partner_amount_rub,
                    "partnerAmountAuthoritative": row.partner_amount_authoritative,
                    "paymentSource": row.payment_source,
                    "paymentStatus": row.payment_status,
                    "balanceDeltaRub": row.balance_delta_rub,
                    "customerAmountStruck": row.customer_amount_struck,
                    "status": row.status,
                    "statusKey": row.status_key,
                    "client": row.client,
                    "oems": list(row.oems),
                }
                for row in model.rows
            ],
            "systemErrors": [
                {
                    "code": error.code,
                    "message": error.message,
                    "context": {
                        "extremizerOrderNo": error.extremizer_order_no,
                        "warehouseOrderNo": error.warehouse_order_no,
                    },
                }
                for error in model.system_errors
            ],
        },
    }


__all__ = [
    "BUSINESS_STATUS_TO_KEY",
    "P2_AUTHORITATIVE_P_SOURCES",
    "P3_INTERNAL_PAYMENT_SOURCE",
    "VALID_PAYMENT_STATUSES",
    "ReadModelContractError",
    "WarehouseUiSummary",
    "WarehouseUiRow",
    "WarehouseSystemError",
    "WarehouseUiReadModel",
    "adapt_warehouse_history",
    "to_fixture_dict",
]
