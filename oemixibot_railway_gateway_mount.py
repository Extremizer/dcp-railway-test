"""Explicit Railway mount for the OEMixi secure price consumer endpoint.

Not imported by production web_app.py. Release Control must explicitly mount it
into the authoritative server service after owner GO.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import dcp_price_gateway
from oemixi_secure_gateway_transport import build_router

DEALER_MAX_AGE_HOURS = 24.0
MSRP_MAX_AGE_HOURS = 720.0


def attach_oemixi_gateway(
    app,
    *,
    coordination_db: Path | str,
    gateway_secret: str,
    dealer_max_age_hours: float = DEALER_MAX_AGE_HOURS,
    msrp_max_age_hours: float = MSRP_MAX_AGE_HOURS,
    wait_seconds: float = 5.0,
):
    """Attach one consumer router to the existing authoritative gateway."""
    db_file = str(coordination_db)

    def shared_gateway(manufacturer, oem, *, max_age_hours, wait_seconds=wait_seconds):
        return dcp_price_gateway.request_price(
            db_file,
            manufacturer,
            oem,
            max_age_hours=max_age_hours,
            wait_seconds=wait_seconds,
        )

    router = build_router(
        db_file=db_file,
        root_secret=gateway_secret,
        shared_gateway=shared_gateway,
        server_max_age_hours=dealer_max_age_hours,
        msrp_max_age_hours=msrp_max_age_hours,
        wait_seconds=wait_seconds,
    )
    app.include_router(router)
    return router


__all__ = [
    "DEALER_MAX_AGE_HOURS",
    "MSRP_MAX_AGE_HOURS",
    "attach_oemixi_gateway",
]
