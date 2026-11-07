"""GO-PRO-11: isolated *synthetic-only* SQLite atomic WEB->TG claim backend.

No production DB, runtime integration, Telegram API, Finance or network access.
A private temporary SQLite database is created only when explicitly constructed.
The caller MUST inject four trusted proof checkers. This module cannot establish
real Telegram actor/channel/WEB ownership/MEMBER truth; it only enforces that
proof checkers approve within the transaction. GAP-01..04 remain OPEN.

Conforms to AtomicClaimBackend and ReceiptVerifier interfaces from
procenka_pro_handoff_claim.py. Intended exclusively for future synthetic QA.
"""
from __future__ import annotations

import secrets
import sqlite3
from contextlib import closing
import tempfile
from datetime import datetime, timezone
from hashlib import sha256
from hmac import compare_digest
from pathlib import Path
from typing import Callable

from procenka_pro_handoff_claim import ClaimIntent, ClaimReceipt


# Trust comes from future, separately authorized integrations, NOT bools in
# ClaimIntent. All four callbacks must use independently verified sources.
EvidenceCheck = Callable[[ClaimIntent], bool]


class SyntheticSQLiteAtomicClaimBackend:
    """Private, file-backed synthetic SQLite backend. Never opens an input DB path.

    SQLite BEGIN IMMEDIATE serializes claim writers for this synthetic file;
    receipt insertion and owner assignment commit together. The generated
    temporary file provides durable on-disk evidence for its lifetime; no
    multi-host or production storage guarantee is implied.
    """

    def __init__(
        self,
        *,
        verify_actor: EvidenceCheck,
        verify_channel: EvidenceCheck,
        verify_web_ownership: EvidenceCheck,
        verify_fresh_member: EvidenceCheck,
    ) -> None:
        checks = (
            verify_actor, verify_channel, verify_web_ownership,
            verify_fresh_member,
        )
        if any(not callable(check) for check in checks):
            raise TypeError("all four trusted evidence checks are required")
        self._checks = checks
        # Cannot point this implementation at production or a caller's DB.
        self._directory = Path(tempfile.mkdtemp(prefix="extremizer-pro-synthetic-"))
        self._db_path = self._directory / "atomic_claim_synthetic.sqlite3"
        with closing(self._connect()) as conn:
            conn.executescript(
                """
                CREATE TABLE synthetic_handoffs (
                    token_digest TEXT PRIMARY KEY,
                    expected_actor_id INTEGER NOT NULL,
                    official_channel_id INTEGER NOT NULL,
                    cart_id TEXT NOT NULL,
                    cart_revision TEXT NOT NULL,
                    expires_epoch INTEGER NOT NULL,
                    telegram_user_id INTEGER,
                    used_epoch INTEGER,
                    CHECK (telegram_user_id IS NULL OR telegram_user_id > 0)
                );
                CREATE TABLE synthetic_cart_claims (
                    cart_id TEXT PRIMARY KEY,
                    cart_revision TEXT NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    token_digest TEXT UNIQUE NOT NULL,
                    FOREIGN KEY (token_digest)
                        REFERENCES synthetic_handoffs(token_digest)
                );
                CREATE TABLE synthetic_claim_receipts (
                    claim_id TEXT PRIMARY KEY,
                    transaction_id TEXT UNIQUE NOT NULL,
                    token_digest TEXT UNIQUE NOT NULL,
                    telegram_user_id INTEGER NOT NULL,
                    channel_id INTEGER NOT NULL,
                    cart_id TEXT NOT NULL,
                    cart_revision TEXT NOT NULL,
                    FOREIGN KEY (token_digest)
                        REFERENCES synthetic_handoffs(token_digest)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path), timeout=5, isolation_level=None)
        conn.execute("PRAGMA busy_timeout = 5000")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _digest(token: str) -> str:
        return sha256(token.encode("utf-8")).hexdigest()

    def seed_synthetic(
        self,
        *,
        expected_actor_id: int,
        channel_id: int,
        cart_id: str,
        cart_revision: str,
        ttl_seconds: int = 3600,
    ) -> ClaimIntent:
        """Create synthetic source-of-truth record; never accepts a token or DB.

        The record's expires_epoch, not ClaimIntent.expires_at, is authoritative.
        Synthetic identity inputs are not proof of real WEB/Telegram ownership.
        """
        if (
            type(expected_actor_id) is not int or expected_actor_id <= 0
            or type(channel_id) is not int or channel_id == 0
            or type(ttl_seconds) is not int or ttl_seconds <= 0
            or not isinstance(cart_id, str) or not cart_id.strip()
            or not isinstance(cart_revision, str) or not cart_revision.strip()
        ):
            raise ValueError("invalid synthetic claim seed")
        token = secrets.token_urlsafe(32)
        digest = self._digest(token)
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                now = int(conn.execute("SELECT strftime('%s','now')").fetchone()[0])
                expires = now + ttl_seconds
                conn.execute(
                    """INSERT INTO synthetic_handoffs
                       (token_digest,expected_actor_id,official_channel_id,
                        cart_id,cart_revision,expires_epoch)
                       VALUES (?,?,?,?,?,?)""",
                    (digest, expected_actor_id, channel_id,
                     cart_id, cart_revision, expires),
                )
                conn.commit()
            except Exception:
                conn.rollback()
                raise
        return ClaimIntent(
            token=token,
            telegram_user_id=expected_actor_id,
            channel_id=channel_id,
            cart_id=cart_id,
            cart_revision=cart_revision,
            expires_at=datetime.fromtimestamp(expires, tz=timezone.utc),
        )

    async def atomic_claim(self, intent: ClaimIntent) -> ClaimReceipt | None:
        """Single CAS + unique receipt transaction; deny/reconcile on failure.

        Only None is a definitive non-commit (replay, expired, mismatch, missing
        proof). Unexpected exceptions propagate to coordinator STOP_RECONCILE.
        """
        if not isinstance(intent, ClaimIntent):
            return None
        try:
            digest = self._digest(intent.token)
            intent_expires = int(intent.expires_at.timestamp())
        except (AttributeError, TypeError, ValueError, UnicodeError, OverflowError):
            return None
        with closing(self._connect()) as conn:
            try:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute(
                    """SELECT expected_actor_id,official_channel_id,cart_id,
                              cart_revision,expires_epoch,telegram_user_id,used_epoch
                       FROM synthetic_handoffs WHERE token_digest = ?""",
                    (digest,),
                ).fetchone()
                now = int(conn.execute("SELECT strftime('%s','now')").fetchone()[0])
                valid = (
                    row is not None
                    and type(intent.telegram_user_id) is int
                    and intent.telegram_user_id > 0
                    and type(intent.channel_id) is int
                    and intent.channel_id != 0
                    and row[0] == intent.telegram_user_id
                    and row[1] == intent.channel_id
                    and row[2] == intent.cart_id
                    and row[3] == intent.cart_revision
                    and row[4] == intent_expires
                    and row[4] > now
                    and row[5] is None
                    and row[6] is None
                )
                if not valid:
                    conn.rollback()
                    return None
                # Mandatory synchronous evidence checks while writer lock held.
                # No trust is inferred from a supplied ID or source-claimed flag.
                if not all(check(intent) is True for check in self._checks):
                    conn.rollback()
                    return None

                changed = conn.execute(
                    """UPDATE synthetic_handoffs
                       SET telegram_user_id=?,
                           used_epoch=CAST(strftime('%s','now') AS INTEGER)
                       WHERE token_digest=? AND expected_actor_id=?
                         AND official_channel_id=? AND cart_id=?
                         AND cart_revision=?
                         AND expires_epoch > CAST(strftime('%s','now') AS INTEGER)
                         AND expires_epoch=? AND telegram_user_id IS NULL
                         AND used_epoch IS NULL""",
                    (intent.telegram_user_id, digest,
                     intent.telegram_user_id, intent.channel_id, intent.cart_id,
                     intent.cart_revision, intent_expires),
                ).rowcount
                if changed != 1:
                    conn.rollback()
                    return None
                # Cart ownership is unique across all tokens and revisions.
                # Token CAS and cart ownership are committed or rolled back
                # together. A second token for the same cart cannot reassign it.
                cart_claimed = conn.execute(
                    """INSERT OR IGNORE INTO synthetic_cart_claims
                       (cart_id,cart_revision,telegram_user_id,token_digest)
                       VALUES (?,?,?,?)""",
                    (intent.cart_id, intent.cart_revision,
                     intent.telegram_user_id, digest),
                ).rowcount
                if cart_claimed != 1:
                    conn.rollback()
                    return None
                receipt = ClaimReceipt(
                    claim_id=secrets.token_hex(16),
                    transaction_id=secrets.token_hex(16),
                    token_digest=digest,
                    telegram_user_id=intent.telegram_user_id,
                    channel_id=intent.channel_id,
                    cart_id=intent.cart_id,
                    cart_revision=intent.cart_revision,
                )
                conn.execute(
                    """INSERT INTO synthetic_claim_receipts
                       (claim_id,transaction_id,token_digest,telegram_user_id,
                        channel_id,cart_id,cart_revision)
                       VALUES (?,?,?,?,?,?,?)""",
                    (receipt.claim_id, receipt.transaction_id, digest,
                     receipt.telegram_user_id, receipt.channel_id,
                     receipt.cart_id, receipt.cart_revision),
                )
                conn.commit()
                return receipt
            except Exception:
                # If commit was ambiguous, caller must STOP_RECONCILE; no retry.
                try:
                    conn.rollback()
                except Exception:
                    pass
                raise

    async def verify_receipt(
        self, intent: ClaimIntent, receipt: ClaimReceipt
    ) -> bool:
        """Read authoritative synthetic committed state, not receipt alone."""
        if not isinstance(intent, ClaimIntent) or not isinstance(receipt, ClaimReceipt):
            return False
        try:
            expected_digest = self._digest(intent.token)
            digest_matches = compare_digest(receipt.token_digest, expected_digest)
        except (TypeError, UnicodeError, AttributeError):
            return False
        if not digest_matches:
            return False
        with closing(self._connect()) as conn:
            row = conn.execute(
                """SELECT r.claim_id,r.transaction_id,r.telegram_user_id,
                          r.channel_id,r.cart_id,r.cart_revision,
                          h.telegram_user_id,h.used_epoch,h.expires_epoch,
                          h.expected_actor_id,h.official_channel_id,
                          h.cart_id,h.cart_revision,
                          c.telegram_user_id,c.cart_revision,c.token_digest
                   FROM synthetic_claim_receipts r
                   JOIN synthetic_handoffs h ON h.token_digest=r.token_digest
                   JOIN synthetic_cart_claims c
                     ON c.cart_id=h.cart_id
                    AND c.cart_revision=h.cart_revision
                    AND c.token_digest=h.token_digest
                   WHERE r.token_digest=?""",
                (expected_digest,),
            ).fetchone()
        return bool(
            row is not None
            and row[0] == receipt.claim_id
            and row[1] == receipt.transaction_id
            and row[2] == receipt.telegram_user_id == intent.telegram_user_id
            and row[3] == receipt.channel_id == intent.channel_id
            and row[4] == receipt.cart_id == intent.cart_id
            and row[5] == receipt.cart_revision == intent.cart_revision
            and row[6] == intent.telegram_user_id
            and row[7] is not None
            and row[8] == int(intent.expires_at.timestamp())
            and row[9] == receipt.telegram_user_id == intent.telegram_user_id
            and row[10] == receipt.channel_id == intent.channel_id
            and row[11] == receipt.cart_id == intent.cart_id
            and row[12] == receipt.cart_revision == intent.cart_revision
            and row[13] == receipt.telegram_user_id == intent.telegram_user_id
            and row[14] == receipt.cart_revision == intent.cart_revision
            and row[15] == expected_digest
        )
