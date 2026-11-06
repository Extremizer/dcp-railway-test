"""Isolated Telegram entry adapter for the EXTREMIZER PRO access core.

GO-PRO-09A: no runtime wiring, Telegram SDK, network, database or Finance.
Only a separately authorized caller may supply a trusted Telegram actor ID,
verified numeric official channel ID, and membership provider. Neither ID nor
the provider is verified by this isolated module. Do not use this adapter as
final checkout authorization.
"""
from __future__ import annotations

from dataclasses import dataclass

from procenka_pro_access_core import (
    AccessDecision,
    AccessPolicy,
    Membership,
    MembershipProvider,
    MembershipResult,
    Operation,
    SubscriptionGate,
)


@dataclass(frozen=True)
class EntryAssessment:
    """Admission assessment, not permission to execute downstream side effects."""

    operation: Operation
    membership: MembershipResult
    decision: AccessDecision

    @property
    def may_enter_other_guards(self) -> bool:
        """True permits ONLY the next independently authorized gate."""
        return self.decision.allowed is True

    @property
    def final_commerce_authorized(self) -> bool:
        """No final order, checkout, payment or handoff authorization exists here."""
        return False


class TelegramEntryAdapter:
    """No API calls except the explicitly injected membership provider.

    The integration layer MUST authenticate the Telegram update, verify that
    actor_user_id comes from update.effective_user.id, verify the official
    numeric channel ID and channel permissions, and separately enforce cart,
    WEB ownership, idempotency, order action and Finance requirements.
    """

    def __init__(self, provider: MembershipProvider):
        self._subscription = SubscriptionGate(provider)

    async def assess(
        self,
        *,
        actor_user_id: int,
        channel_id: int,
        operation: Operation,
        order_owner_verified: bool = False,
        order_action_authorized: bool = False,
        external_role_authorized: bool = False,
    ) -> EntryAssessment:
        # Do not trust bools, user-controlled IDs, or unknown operation names.
        identity_ok = (
            type(actor_user_id) is int
            and actor_user_id > 0
            and type(channel_id) is int
            and channel_id != 0
        )
        if not identity_ok or not isinstance(operation, Operation):
            return EntryAssessment(
                operation=operation,
                membership=MembershipResult(
                    Membership.UNKNOWN, "invalid_verified_identity_or_operation"
                ),
                decision=AccessDecision(
                    False, "invalid_verified_identity_or_operation", Membership.UNKNOWN
                ),
            )

        # Existing-order rights and privileged role decisions are independent
        # of PRO membership. Do not invoke the membership provider for them.
        if operation in {
            Operation.EXISTING_ORDER_READ,
            Operation.EXISTING_ORDER_SERVICE,
            Operation.EXISTING_ORDER_DECISION,
            Operation.ADMIN_DEALER_SYSTEM,
            Operation.PUBLIC_NAVIGATION,
        }:
            decision = AccessPolicy.authorize(
                operation=operation,
                order_owner_verified=order_owner_verified,
                order_action_authorized=order_action_authorized,
                external_role_authorized=external_role_authorized,
            )
            return EntryAssessment(
                operation,
                MembershipResult(Membership.UNKNOWN, "not_required_for_operation"),
                decision,
            )

        membership = await self._subscription.check(
            telegram_user_id=actor_user_id, channel_id=channel_id
        )
        decision = AccessPolicy.authorize(
            operation=operation, membership=membership.state
        )
        return EntryAssessment(operation, membership, decision)
