"""Synthetic-only staging receiver; NO HTTP server, DB or external IO.

Future Railway endpoint must replace this in-memory replay ledger with an
atomic persistent nonce store and explicitly provisioned staging-only secret.
"""
from __future__ import annotations

from collections.abc import MutableSet
from dataclasses import dataclass, field
from datetime import datetime

from oem_cache_bridge_v1 import BridgeRejected, verify_and_plan


@dataclass
class SyntheticReceiver:
    secret: bytes
    used_nonces: MutableSet[str] = field(default_factory=set)

    def deliver(self, body: bytes, headers: dict, *, now: datetime) -> dict:
        """Emulate one authenticated inbound POST without opening a listener."""
        plan = verify_and_plan(
            body, headers, self.secret, used_nonces=self.used_nonces, now=now
        )
        if plan["action"] == "IDENTITY_ONLY_NO_PRICE_WRITE":
            return {
                "accepted": True,
                "identity_only": True,
                "cache_write": False,
                "data_persisted": False,
            }
        # The priced path must not reach a storage writer in this staging pilot.
        raise BridgeRejected("priced_cache_write_not_enabled_in_stage")
