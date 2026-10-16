"""Production wiring for ADMIN-only Polaris Dealer Price through Railway Category Authority.

This explicit entrypoint keeps the normal dealer/global price path unchanged.
Category Authority is fetched read-only from the current Railway production
service over a signed HTTPS request.
"""
from __future__ import annotations

import os

from oemixi_admin_polaris_browser_price import admin_browser_price
from oemixi_category_authority_client import (
    CategoryAuthorityTransportError,
    HTTPSCategoryAuthorityResolver,
)
from polaris_probe_errors import ProbeFailure

DEFAULT_CATEGORY_AUTHORITY_BASE_URL = (
    "https://dcp-railway-test-production.up.railway.app"
)


def _root_secret(root_secret=None, token=None) -> str:
    value = str(
        root_secret
        or token
        or os.getenv("OEMIXIBOT_TOKEN", "")
    ).strip()
    if len(value) < 16:
        raise RuntimeError("OEMIXIBOT_TOKEN_REQUIRED")
    return value


def _base_url(base_url=None) -> str:
    value = str(
        base_url
        or os.getenv("EXTREMIZER_CATEGORY_AUTHORITY_BASE_URL", "")
        or DEFAULT_CATEGORY_AUTHORITY_BASE_URL
    ).strip()
    if not value:
        raise RuntimeError("CATEGORY_AUTHORITY_BASE_URL_REQUIRED")
    return value


def make_category_resolver(*, base_url=None, root_secret=None, token=None):
    transport = HTTPSCategoryAuthorityResolver(
        base_url=_base_url(base_url),
        root_secret=_root_secret(root_secret, token),
    )

    def resolve_category(manufacturer, requested_oem):
        try:
            return transport(manufacturer, requested_oem)
        except CategoryAuthorityTransportError as exc:
            code = exc.code
            if code not in {
                "CATEGORY_AUTHORITY_UNAVAILABLE",
                "CATEGORY_AUTHORITY_AUTH_FAILED",
                "CATEGORY_AUTHORITY_INVALID_RESPONSE",
                "CATEGORY_AUTHORITY_RESPONSE_TOO_LARGE",
                "CATEGORY_AUTHORITY_HTTP_ERROR",
            }:
                code = "CATEGORY_AUTHORITY_UNAVAILABLE"
            if code in {
                "CATEGORY_AUTHORITY_RESPONSE_TOO_LARGE",
                "CATEGORY_AUTHORITY_HTTP_ERROR",
            }:
                code = "CATEGORY_AUTHORITY_INVALID_RESPONSE"
            raise ProbeFailure(code)

    return resolve_category


def make_admin_polaris_provider(*, base_url=None, root_secret=None, token=None):
    category_resolver = make_category_resolver(
        base_url=base_url,
        root_secret=root_secret,
        token=token,
    )

    def provider(oem):
        # Railway Category Authority owns supersession/replacement resolution.
        # DCP must probe the canonical resolved OEM rather than re-submitting the
        # obsolete requested OEM and duplicating authority in browser navigation.
        evidence = category_resolver("Polaris", oem)
        if not isinstance(evidence, dict):
            return {
                "status":"DEALER_PRICE_UNVERIFIED",
                "requested_oem":oem,
                "dealer_price_usd":None,
                "admin_unit_price_usd":None,
                "production_found":False,
                "reason":"CATEGORY_UNVERIFIED",
            }
        resolved = str(evidence.get("resolved_oem") or "").strip().upper()
        if not resolved:
            return {
                "status":"DEALER_PRICE_UNVERIFIED",
                "requested_oem":oem,
                "dealer_price_usd":None,
                "admin_unit_price_usd":None,
                "production_found":False,
                "reason":"CATEGORY_UNVERIFIED",
            }

        def canonical_category(manufacturer, candidate_oem):
            if manufacturer != "Polaris" or candidate_oem != resolved:
                return None
            return evidence

        result = admin_browser_price(
            resolved,
            category_resolver=canonical_category,
        )
        if not isinstance(result, dict):
            return {
                "status":"DEALER_PRICE_UNVERIFIED",
                "requested_oem":oem,
                "resolved_oem":resolved,
                "dealer_price_usd":None,
                "admin_unit_price_usd":None,
                "production_found":False,
                "reason":"ADMIN_PROBE_INVALID_RESULT",
            }
        return {
            **result,
            "oem":oem,
            "requested_oem":oem,
            "resolved_oem":resolved,
        }

    return provider


def build_application(
    token=None,
    *,
    base_url=None,
    root_secret=None,
):
    import oemixibot_app

    resolved_token = str(token or os.getenv("OEMIXIBOT_TOKEN", "")).strip()
    if not resolved_token:
        raise RuntimeError("OEMIXIBOT_TOKEN_REQUIRED")

    application = oemixibot_app.build_application(resolved_token)
    oemixibot_app.bind_admin_polaris_price_resolver(
        application,
        make_admin_polaris_provider(
            base_url=base_url,
            root_secret=root_secret,
            token=resolved_token,
        ),
    )
    return application


def main():
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.info("OEMixiBOT ADMIN Polaris production runtime starting")
    build_application().run_polling(drop_pending_updates=False)


if __name__ == "__main__":
    main()
