"""Isolated PRO authorization core for @Extremizer_bot.

DOCUMENTARY CONTRACT:
EXT-T12-PROCENKA-SUBSCRIBER-ACCESS-CONSOLIDATED-TZ-20261104-V01

This module has no Telegram SDK imports, network calls, file/DB access,
runtime registration, or side effects. Integration is NOT enabled.
The membership provider must be injected by a separately authorized caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable


class Membership(str, Enum):
    MEMBER = "MEMBER"
    NOT_MEMBER = "NOT_MEMBER"
    UNKNOWN = "UNKNOWN"


class Operation(str, Enum):
    PUBLIC_NAVIGATION = "PUBLIC_NAVIGATION"
    NEW_QUOTE = "NEW_QUOTE"
    NEW_BOT_CART_READ = "NEW_BOT_CART_READ"
    NEW_BOT_CART_MUTATE = "NEW_BOT_CART_MUTATE"
    WEB_HANDOFF_RESTORE = "WEB_HANDOFF_RESTORE"
    NEW_CHECKOUT_PREFLIGHT = "NEW_CHECKOUT_PREFLIGHT"
    NEW_CHECKOUT_COMMIT = "NEW_CHECKOUT_COMMIT"
    EXISTING_ORDER_READ = "EXISTING_ORDER_READ"
    EXISTING_ORDER_SERVICE = "EXISTING_ORDER_SERVICE"
    EXISTING_ORDER_DECISION = "EXISTING_ORDER_DECISION"
    ADMIN_DEALER_SYSTEM = "ADMIN_DEALER_SYSTEM"


# Provider returns a raw Bot API membership status, NOT a business permission.
# The integration layer must verify the numeric official channel_id, the
# authenticated Telegram user_id, and the bot's ability to check membership.
MembershipProvider = Callable[[int, int], Awaitable[str | None]]

_NEW_COMMERCE = frozenset({
    Operation.NEW_QUOTE,
    Operation.NEW_BOT_CART_READ,
    Operation.NEW_BOT_CART_MUTATE,
    Operation.WEB_HANDOFF_RESTORE,
    Operation.NEW_CHECKOUT_PREFLIGHT,
    Operation.NEW_CHECKOUT_COMMIT,
})
_EXISTING_ORDER = frozenset({
    Operation.EXISTING_ORDER_READ,
    Operation.EXISTING_ORDER_SERVICE,
    Operation.EXISTING_ORDER_DECISION,
})


@dataclass(frozen=True)
class MembershipResult:
    state: Membership
    reason: str


@dataclass(frozen=True)
class AccessDecision:
    allowed: bool
    reason: str
    membership: Membership | None = None


class SubscriptionGate:
    """Fail-closed, one-shot membership classification; never caches grants."""

    def __init__(self, provider: MembershipProvider):
        if not callable(provider):
            raise TypeError("membership provider must be callable")
        self._provider = provider

    async def check(self, *, telegram_user_id: int, channel_id: int) -> MembershipResult:
        if (
            type(telegram_user_id) is not int or telegram_user_id <= 0
            or type(channel_id) is not int or channel_id == 0
        ):
            return MembershipResult(Membership.UNKNOWN, "invalid_verified_identity")
        try:
            raw = await self._provider(telegram_user_id, channel_id)
        except Exception:
            # Avoid logging provider errors or identifiers in the authorization core.
            return MembershipResult(Membership.UNKNOWN, "membership_check_failed")
        if not isinstance(raw, str):
            return MembershipResult(Membership.UNKNOWN, "unrecognized_membership")
        status = raw.strip().lower()
        if status in {"creator", "administrator", "member"}:
            return MembershipResult(Membership.MEMBER, "verified_member")
        if status in {"left", "kicked"}:
            return MembershipResult(Membership.NOT_MEMBER, "verified_nonmember")
        # restricted, missing, malformed and future statuses never grant access.
        return MembershipResult(Membership.UNKNOWN, "unrecognized_membership")


class AccessPolicy:
    """Pure policy. No grant bypasses order ownership, Finance or cart checks."""

    @staticmethod
    def authorize(
        *,
        operation: Operation,
        membership: Membership | None = None,
        order_owner_verified: bool = False,
        order_action_authorized: bool = False,
        external_role_authorized: bool = False,
    ) -> AccessDecision:
        if not isinstance(operation, Operation):
            return AccessDecision(False, "unknown_operation")
        if operation is Operation.PUBLIC_NAVIGATION:
            return AccessDecision(True, "public_navigation_only")
        if operation in _NEW_COMMERCE:
            if membership is Membership.MEMBER:
                return AccessDecision(
                    True, "membership_only_other_guards_required", membership
                )
            if membership is Membership.NOT_MEMBER:
                return AccessDecision(False, "subscription_required", membership)
            return AccessDecision(False, "subscription_unverified", Membership.UNKNOWN)
        if operation in _EXISTING_ORDER:
            if order_owner_verified is not True:
                return AccessDecision(False, "order_ownership_unverified", membership)
            if order_action_authorized is not True:
                return AccessDecision(False, "order_action_unverified", membership)
            return AccessDecision(
                True, "existing_order_only_other_guards_required", membership
            )
        if operation is Operation.ADMIN_DEALER_SYSTEM:
            return AccessDecision(
                external_role_authorized is True,
                "external_role_verified" if external_role_authorized is True
                else "external_role_unverified",
            )
        return AccessDecision(False, "unknown_operation")
