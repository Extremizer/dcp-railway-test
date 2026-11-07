"""GAP01-04: isolated synthetic WEB-cart-to-Telegram authorization contract.

NO runtime wiring, persistence, real identity provider, Telegram API, PRO
subscription or channel-membership dependency. No checkout or Finance grant.
Authoritative session/cart/consent/atomic-claim proof MUST be supplied by
trusted server integrations. This module alone cannot close GAP-01.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Awaitable, Callable, Sequence


@dataclass(frozen=True)
class WebSession:
    session_id: str = field(repr=False)
    customer_id: str
    authenticated_at: datetime
    expires_at: datetime


@dataclass(frozen=True)
class ServerCart:
    cart_id: str
    owner_customer_id: str
    revision: str
    items_digest: str


@dataclass(frozen=True)
class TelegramActor:
    telegram_user_id: int
    verified_at: datetime


@dataclass(frozen=True)
class HandoffAuthorization:
    authorization_id: str = field(repr=False)
    customer_id: str
    cart_id: str
    cart_revision: str
    items_digest: str = field(repr=False)
    telegram_user_id: int
    expires_at: datetime
    consumed_at: datetime | None = None


@dataclass(frozen=True)
class ClaimEvidence:
    authorization_id: str = field(repr=False)
    transaction_id: str
    claim_id: str
    customer_id: str
    cart_id: str
    cart_revision: str
    telegram_user_id: int
    committed_at: datetime


class Status(str, Enum):
    DENY = "DENY"
    STOP_RECONCILE = "STOP_RECONCILE"
    VERIFIED_NEXT_GATE = "VERIFIED_NEXT_GATE"


@dataclass(frozen=True)
class Result:
    status: Status
    reason: str
    value: ServerCart | HandoffAuthorization | ClaimEvidence | None = field(
        default=None, repr=False
    )

    @property
    def checkout_authorized(self) -> bool:
        return False


# All proofs originate in trusted injected server dependencies, NEVER in
# client-supplied flags, cart JSON or unauthenticated Telegram IDs.
VerifySession = Callable[[WebSession], Awaitable[bool]]
VerifyActor = Callable[[TelegramActor], Awaitable[bool]]
VerifyCartOwnership = Callable[[WebSession, ServerCart], Awaitable[bool]]
VerifyTargetConsent = Callable[[WebSession, ServerCart, TelegramActor], Awaitable[bool]]

# Creates a canonical, durable server-side cart with owner, revision, digest.
CreateCartBackend = Callable[
    [WebSession, Sequence[object]], Awaitable[ServerCart | None]
]
# MUST repeat ALL authority checks in authoritative storage and issue one
# owner/cart/revision/digest/actor-bound, revocable, expiring grant atomically.
IssueAuthorizationBackend = Callable[
    [WebSession, ServerCart, TelegramActor],
    Awaitable[HandoffAuthorization | None],
]
# MUST atomically verify trusted actor, original TTL, unused/nonrevoked grant,
# current cart owner/revision/digest and consent; only one successful claim.
AtomicClaimBackend = Callable[
    [str, TelegramActor], Awaitable[ClaimEvidence | None]
]
# Independent proof by reading authoritative committed receipt/owner records.
VerifyClaimEvidence = Callable[
    [str, TelegramActor, ClaimEvidence], Awaitable[bool]
]


class ServerOwnedWebCartAuthorization:
    """Synthetic fail-closed coordinator; NEVER a source of real authority.

    VERIFIED_NEXT_GATE means this layer accepted injected evidence only.
    It is NOT authorization for commerce, checkout, payment or production.
    """

    def __init__(
        self, *,
        verify_session: VerifySession,
        verify_actor: VerifyActor,
        verify_cart_ownership: VerifyCartOwnership,
        verify_target_consent: VerifyTargetConsent,
        create_cart_backend: CreateCartBackend,
        issue_authorization_backend: IssueAuthorizationBackend,
        atomic_claim_backend: AtomicClaimBackend,
        verify_claim_evidence: VerifyClaimEvidence,
    ) -> None:
        dependencies = (
            verify_session, verify_actor, verify_cart_ownership,
            verify_target_consent, create_cart_backend,
            issue_authorization_backend, atomic_claim_backend,
            verify_claim_evidence,
        )
        if not all(callable(dependency) for dependency in dependencies):
            raise TypeError("all trusted dependencies are mandatory")
        (
            self._verify_session, self._verify_actor,
            self._verify_cart_ownership, self._verify_target_consent,
            self._create_cart, self._issue_authorization,
            self._atomic_claim, self._verify_claim_evidence,
        ) = dependencies

    @staticmethod
    def _time(value: object) -> bool:
        return (
            isinstance(value, datetime)
            and value.tzinfo is not None
            and value.utcoffset() is not None
        )

    @classmethod
    def _session(cls, session: object) -> bool:
        return (
            isinstance(session, WebSession)
            and isinstance(session.session_id, str)
            and bool(session.session_id.strip())
            and isinstance(session.customer_id, str)
            and bool(session.customer_id.strip())
            and cls._time(session.authenticated_at)
            and cls._time(session.expires_at)
            and session.authenticated_at <= datetime.now(timezone.utc)
            < session.expires_at
        )

    @classmethod
    def _actor(cls, actor: object) -> bool:
        return (
            isinstance(actor, TelegramActor)
            and type(actor.telegram_user_id) is int
            and actor.telegram_user_id > 0
            and cls._time(actor.verified_at)
            and actor.verified_at <= datetime.now(timezone.utc)
        )

    @staticmethod
    def _cart(cart: object, customer_id: str) -> bool:
        return (
            isinstance(cart, ServerCart)
            and all(
                isinstance(value, str) and bool(value.strip())
                for value in (
                    cart.cart_id, cart.owner_customer_id,
                    cart.revision, cart.items_digest,
                )
            )
            and cart.owner_customer_id == customer_id
        )

    async def create_server_cart(
        self, session: WebSession, items: Sequence[object]
    ) -> Result:
        if not self._session(session) or not isinstance(items, (tuple, list)) or not items:
            return Result(Status.DENY, "invalid_cart_request")
        try:
            if await self._verify_session(session) is not True:
                return Result(Status.DENY, "session_unverified")
            cart = await self._create_cart(session, items)
            if not self._cart(cart, session.customer_id):
                return Result(Status.STOP_RECONCILE, "cart_record_unverified")
            if await self._verify_cart_ownership(session, cart) is not True:
                return Result(Status.STOP_RECONCILE, "cart_owner_unverified")
        except Exception:
            return Result(Status.STOP_RECONCILE, "cart_creation_uncertain")
        return Result(Status.VERIFIED_NEXT_GATE, "cart_created", cart)

    async def authorize_handoff(
        self, session: WebSession, cart: ServerCart, actor: TelegramActor
    ) -> Result:
        if not (
            self._session(session)
            and self._cart(cart, session.customer_id)
            and self._actor(actor)
        ):
            return Result(Status.DENY, "invalid_authorization_request")
        try:
            if await self._verify_session(session) is not True:
                return Result(Status.DENY, "session_unverified")
            if await self._verify_actor(actor) is not True:
                return Result(Status.DENY, "actor_unverified")
            if await self._verify_cart_ownership(session, cart) is not True:
                return Result(Status.DENY, "cart_owner_unverified")
            if await self._verify_target_consent(session, cart, actor) is not True:
                return Result(Status.DENY, "target_consent_unverified")
            grant = await self._issue_authorization(session, cart, actor)
        except Exception:
            return Result(Status.STOP_RECONCILE, "grant_outcome_uncertain")
        try:
            if not (
                isinstance(grant, HandoffAuthorization)
                and isinstance(grant.authorization_id, str)
                and bool(grant.authorization_id.strip())
                and grant.customer_id == session.customer_id
                and grant.cart_id == cart.cart_id
                and grant.cart_revision == cart.revision
                and grant.items_digest == cart.items_digest
                and type(grant.telegram_user_id) is int
                and grant.telegram_user_id == actor.telegram_user_id
                and grant.consumed_at is None
                and self._time(grant.expires_at)
                and grant.expires_at > datetime.now(timezone.utc)
            ):
                return Result(Status.STOP_RECONCILE, "grant_record_mismatch")
        except Exception:
            return Result(Status.STOP_RECONCILE, "grant_record_unverifiable")
        return Result(Status.VERIFIED_NEXT_GATE, "grant_issued", grant)

    async def claim_handoff(
        self, authorization_id: str, actor: TelegramActor
    ) -> Result:
        if (
            not isinstance(authorization_id, str)
            or not authorization_id.strip()
            or not self._actor(actor)
        ):
            return Result(Status.DENY, "invalid_claim_request")
        try:
            if await self._verify_actor(actor) is not True:
                return Result(Status.DENY, "actor_unverified")
        except Exception:
            return Result(Status.DENY, "actor_check_unavailable")
        try:
            # The trusted backend must enforce replay denial, immutable binding,
            # original TTL, consent and transactionally unique ownership itself.
            evidence = await self._atomic_claim(authorization_id, actor)
        except Exception:
            # Commit may have happened: NEVER retry automatically.
            return Result(Status.STOP_RECONCILE, "claim_outcome_uncertain")
        if evidence is None:
            return Result(Status.DENY, "claim_rejected")
        try:
            if not (
                isinstance(evidence, ClaimEvidence)
                and evidence.authorization_id == authorization_id
                and type(evidence.telegram_user_id) is int
                and evidence.telegram_user_id == actor.telegram_user_id
                and all(
                    isinstance(value, str) and bool(value.strip())
                    for value in (
                        evidence.transaction_id, evidence.claim_id,
                        evidence.customer_id, evidence.cart_id,
                        evidence.cart_revision,
                    )
                )
                and self._time(evidence.committed_at)
            ):
                return Result(Status.STOP_RECONCILE, "claim_receipt_mismatch")
        except Exception:
            return Result(Status.STOP_RECONCILE, "claim_receipt_unverifiable")
        try:
            verified = await self._verify_claim_evidence(
                authorization_id, actor, evidence
            )
        except Exception:
            return Result(Status.STOP_RECONCILE, "receipt_check_unavailable")
        if verified is not True:
            return Result(Status.STOP_RECONCILE, "receipt_unverified")
        return Result(Status.VERIFIED_NEXT_GATE, "claim_evidence_verified", evidence)
