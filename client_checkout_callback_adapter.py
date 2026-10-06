#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Isolated adapter between Telegram checkout preflight and atomic persistence.

The live callback is NOT wired to this module yet.

Phase boundary:
- before_transaction: callback validations and pure preparation only;
- inside_transaction: persist_client_checkout_atomic() only;
- after_commit: external/durable side effects in controlled order;
- finalize_session: clear live cart/session only after critical post-commit work.

A post-commit failure never rolls back or reruns the already committed core.
The raised CheckoutPostCommitError carries core_committed=True so a future
callback can distinguish recovery from a safe pre-commit retry.
"""

from __future__ import annotations

from dataclasses import dataclass
import inspect
from typing import Any, Awaitable, Callable

from client_checkout_orchestrator import persist_client_checkout_atomic
from client_finance_adapter import ClientFinanceAdapter


CHECKOUT_PHASES = {
    "before_transaction": (
        "cart_nonempty",
        "warehouse_recipient_complete",
        "warehouse_offers_revalidated",
        "delivery_choices_complete",
        "manager_chat_configured",
        "derive_origin_and_web_handoff",
        "generate_order_id",
    ),
    "inside_transaction": (
        "save_order_to_history",
        "load_persisted_line_finance_snapshots",
        "post_client_order_charge",
        "commit",
    ),
    "after_commit_critical": (
        "manager_notification",
        "supplier_orders",
        "warehouse_reservation",
        "auto_quote_promotion",
    ),
    "after_commit_best_effort": (
        "web_handoff_link",
    ),
    "finalize_session": (
        "remember_last_order",
        "clear_cart_and_checkout_state",
    ),
}


@dataclass(frozen=True)
class CheckoutAdapterResult:
    order_id: str
    client_id: str
    finance_event_id: int | None
    completed_actions: tuple[str, ...]
    warnings: tuple[str, ...]
    auto_quote: Any = None


class CheckoutPostCommitError(RuntimeError):
    """Failure after the durable order+finance transaction already committed."""

    def __init__(
        self,
        *,
        action: str,
        core_result: dict,
        completed_actions: tuple[str, ...],
        cause: BaseException,
    ):
        self.action = action
        self.core_result = dict(core_result)
        self.completed_actions = tuple(completed_actions)
        self.cause = cause
        self.core_committed = True
        super().__init__(
            f"post-commit action failed: {action}: "
            f"{type(cause).__name__}: {cause}"
        )


async def _call_hook(hook: Callable[[], Any] | None):
    if hook is None:
        return None
    value = hook()
    if inspect.isawaitable(value):
        return await value
    return value


async def execute_prepared_checkout(
    *,
    db_path,
    save_order_fn,
    finance_adapter: ClientFinanceAdapter,
    order_id: str,
    user,
    cart: dict,
    delivery_preference: str | None = None,
    origin: str = "telegram",
    actor: str = "checkout",
    manager_notify: Callable[[], Any] | None,
    supplier_orders: Callable[[], Any] | None = None,
    warehouse_reservation: Callable[[], Any] | None = None,
    web_handoff_link: Callable[[], Any] | None = None,
    auto_quote_promotion: Callable[[], Any] | None = None,
    finalize_session: Callable[[], Any] | None = None,
):
    """Run the prepared checkout without embedding Telegram-specific logic.

    Preconditions such as cart validation, warehouse revalidation, delivery
    selection, manager-chat configuration and order-id generation must already
    have succeeded in the callback before this function is called.
    """

    if manager_notify is None or not callable(manager_notify):
        raise ValueError("manager_notify required")

    core_result = persist_client_checkout_atomic(
        db_path=db_path,
        save_order_fn=save_order_fn,
        finance_adapter=finance_adapter,
        order_id=order_id,
        user=user,
        cart=cart,
        delivery_preference=delivery_preference,
        origin=origin,
        actor=actor,
    )

    completed: list[str] = []
    warnings: list[str] = []

    async def critical(action: str, hook: Callable[[], Any] | None):
        if hook is None:
            return None
        try:
            value = await _call_hook(hook)
        except Exception as exc:
            raise CheckoutPostCommitError(
                action=action,
                core_result=core_result,
                completed_actions=tuple(completed),
                cause=exc,
            ) from exc
        completed.append(action)
        return value

    await critical("manager_notification", manager_notify)
    await critical("supplier_orders", supplier_orders)

    reservation_result = await critical(
        "warehouse_reservation",
        warehouse_reservation,
    )
    if (
        reservation_result is not None
        and isinstance(reservation_result, dict)
        and not reservation_result.get("ok")
    ):
        if completed and completed[-1] == "warehouse_reservation":
            completed.pop()
        exc = RuntimeError(
            "warehouse reservation failed: "
            + str(reservation_result.get("reason") or "unknown")
        )
        raise CheckoutPostCommitError(
            action="warehouse_reservation",
            core_result=core_result,
            completed_actions=tuple(completed),
            cause=exc,
        )

    if web_handoff_link is not None:
        try:
            linked = await _call_hook(web_handoff_link)
            if linked is False:
                warnings.append("web_handoff_link_failed")
            else:
                completed.append("web_handoff_link")
        except Exception:
            warnings.append("web_handoff_link_failed")

    auto_quote = await critical(
        "auto_quote_promotion",
        auto_quote_promotion,
    )
    await critical("finalize_session", finalize_session)

    return CheckoutAdapterResult(
        order_id=str(core_result["order_id"]),
        client_id=str(core_result["client_id"]),
        finance_event_id=core_result.get("finance_event_id"),
        completed_actions=tuple(completed),
        warnings=tuple(warnings),
        auto_quote=auto_quote,
    )


__all__ = [
    "CHECKOUT_PHASES",
    "CheckoutAdapterResult",
    "CheckoutPostCommitError",
    "execute_prepared_checkout",
]
