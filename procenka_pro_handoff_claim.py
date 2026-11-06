"""Isolated WEB-to-Telegram claim coordinator (GO-PRO-09B).

NO production wiring, persistence, DB schema, Telegram SDK, network or Finance.
The injected atomic backend MUST implement a durable, transactional compare-
and-swap. This module does not implement that backend: no production claim
can be made until its semantics and independent QA are authorized.

Trust boundaries:
- actor verifier authenticates the Telegram actor, not merely an integer;
- channel verifier identifies the official numerical channel and bot rights;
- WEB ownership verifier proves cart-to-actor binding independently of MEMBER;
- SubscriptionGate obtains current membership using an injected provider;
- atomic backend RECHECKS all identity, cart revision, TTL, existing claim,
  and owner binding invariants inside ONE storage transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable

from procenka_pro_access_core import Membership, MembershipProvider, SubscriptionGate


@dataclass(frozen=True)
class ClaimIntent:
    """Opaque claim request; token is never included in the result."""
    token: str
    telegram_user_id: int
    channel_id: int
    cart_id: str
    cart_revision: str
    expires_at: datetime


@dataclass(frozen=True)
class ClaimReceipt:
    """Receipt emitted ONLY after durable atomic claim, not a cart grant."""
    claim_id: str
    telegram_user_id: int
    cart_id: str
    cart_revision: str


class ClaimStatus(str, Enum):
    CLAIMED = "CLAIMED"
    DENIED = "DENIED"
    UNKNOWN = "UNKNOWN"
    STOP_RECONCILE = "STOP_RECONCILE"


@dataclass(frozen=True)
class ClaimOutcome:
    status: ClaimStatus
    reason: str
    receipt: ClaimReceipt | None = None

    @property
    def final_commerce_authorized(self) -> bool:
        return False


# All three verifiers must be trusted integrations, never client-supplied flags.
ActorVerifier = Callable[[int], Awaitable[bool]]
ChannelVerifier = Callable[[int], Awaitable[bool]]
WebOwnershipVerifier = Callable[[ClaimIntent], Awaitable[bool]]

# REQUIRED contract for injected backend:
# - one atomic transaction/CAS, shared by ALL processes/instances;
# - match token, authenticated actor, verified WEB owner, cart ID/revision;
# - check not previously claimed, original TTL with storage-side current time;
# - assign owner and used/claimed state atomically, never reassign owner;
# - persist a unique claim receipt; return None on definite rejected claim;
# - on uncertain commit outcome, raise (coordinator returns STOP_RECONCILE).
# Rechecking against authoritative records is mandatory; external preflight
# booleans alone do NOT authorize the backend to consume a token.
AtomicClaimBackend = Callable[[ClaimIntent], Awaitable[ClaimReceipt | None]]


class WebTelegramClaimCoordinator:
    """Fail-closed coordinator with mandatory injected atomic backend."""

    def __init__(
        self,
        *,
        verify_actor: ActorVerifier,
        verify_channel: ChannelVerifier,
        verify_web_ownership: WebOwnershipVerifier,
        membership_provider: MembershipProvider,
        atomic_claim: AtomicClaimBackend,
    ) -> None:
        for fn in (
            verify_actor, verify_channel, verify_web_ownership,
            membership_provider, atomic_claim,
        ):
            if not callable(fn):
                raise TypeError("trusted claim dependency must be callable")
        self._verify_actor = verify_actor
        self._verify_channel = verify_channel
        self._verify_web_ownership = verify_web_ownership
        self._subscription = SubscriptionGate(membership_provider)
        self._atomic_claim = atomic_claim

    async def claim(self, intent: ClaimIntent) -> ClaimOutcome:
        if not isinstance(intent, ClaimIntent):
            return ClaimOutcome(ClaimStatus.DENIED, "invalid_claim_intent")
        if (
            type(intent.telegram_user_id) is not int
            or intent.telegram_user_id <= 0
            or type(intent.channel_id) is not int
            or intent.channel_id == 0
            or not isinstance(intent.token, str)
            or not intent.token.strip()
            or not isinstance(intent.cart_id, str)
            or not intent.cart_id.strip()
            or not isinstance(intent.cart_revision, str)
            or not intent.cart_revision.strip()
            or not isinstance(intent.expires_at, datetime)
            or intent.expires_at.tzinfo is None
            or intent.expires_at.utcoffset() is None
        ):
            return ClaimOutcome(ClaimStatus.DENIED, "invalid_claim_intent")
        if intent.expires_at <= datetime.now(timezone.utc):
            return ClaimOutcome(ClaimStatus.DENIED, "expired_claim_intent")

        try:
            actor_ok = await self._verify_actor(intent.telegram_user_id)
            channel_ok = await self._verify_channel(intent.channel_id)
            if actor_ok is not True or channel_ok is not True:
                return ClaimOutcome(ClaimStatus.UNKNOWN, "identity_not_verified")
            owner_ok = await self._verify_web_ownership(intent)
            if owner_ok is not True:
                return ClaimOutcome(ClaimStatus.DENIED, "web_ownership_not_verified")
        except Exception:
            return ClaimOutcome(ClaimStatus.UNKNOWN, "verification_unavailable")

        member = await self._subscription.check(
            telegram_user_id=intent.telegram_user_id,
            channel_id=intent.channel_id,
        )
        if member.state is Membership.NOT_MEMBER:
            return ClaimOutcome(ClaimStatus.DENIED, "subscription_required")
        if member.state is not Membership.MEMBER:
            return ClaimOutcome(ClaimStatus.UNKNOWN, "subscription_unverified")
        if intent.expires_at <= datetime.now(timezone.utc):
            return ClaimOutcome(ClaimStatus.DENIED, "expired_claim_intent")

        try:
            # Only the trusted storage backend can do an atomic owner-bound CAS.
            receipt = await self._atomic_claim(intent)
        except Exception:
            # Commit may already have happened. Never retry or reassign here.
            return ClaimOutcome(ClaimStatus.STOP_RECONCILE, "claim_outcome_uncertain")
        if receipt is None:
            return ClaimOutcome(ClaimStatus.DENIED, "claim_rejected")
        if (
            not isinstance(receipt, ClaimReceipt)
            or not isinstance(receipt.claim_id, str)
            or not receipt.claim_id.strip()
            or receipt.telegram_user_id != intent.telegram_user_id
            or receipt.cart_id != intent.cart_id
            or receipt.cart_revision != intent.cart_revision
        ):
            return ClaimOutcome(ClaimStatus.STOP_RECONCILE, "receipt_mismatch")
        return ClaimOutcome(ClaimStatus.CLAIMED, "atomic_claim_committed", receipt)
