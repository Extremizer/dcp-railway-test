# EXTREMIZER — first LIVE Polaris 2205250 readiness

**Status:** READINESS ONLY / LIVE DCP HOLD. Existing Railway PHONE-FIRST mock host is a reference only and MUST NOT be modified or deployed. Its source commit (per Theme 4): `75b467f9beada4fab455193c13ef4ddbf3df6f71`; this report does not independently verify that deployed image or Chromium session.

The actual accepted Polaris dealer-price authority is `polaris_dealer_cart_contract.py` (`polaris_parts:authenticated_dealer_cart:exact_line_unit_price`). Existing `polaris_dealer_session_identity_guard.py` uses `dp_sync_agent._preflight_dcp` and read-only CDP identity inspection; it must be combined with PR #64 same-origin `#persistant-nav /account/logoff` and exact `.displayname`. Search MSRP and public client price are NEVER evidence of dealer price. No read-only authoritative 2205250 Dealer Price source has been demonstrated. **If cart changes would be necessary, STOP and obtain separate explicit cart authorization.**

## Exact no-network container command on an approved immutable preloaded Chromium image

The reviewed mock host is not an approved LIVE deployment image. Its immutable image digest is not known in this branch and MUST be supplied by Theme 4; do not use a floating image tag.

```sh
docker run --rm --network none --read-only --cap-drop ALL --security-opt no-new-privileges \
  --mount type=bind,src=/approved/dcp-readonly-state,dst=/state \
  --env EXTREMIZER_DCP_STATE_DIR=/state \
  --env EXTREMIZER_DCP_OEM=2205250 \
  --env EXTREMIZER_DCP_MANUFACTURER=Polaris \
  --entrypoint python APPROVED_IMMUTABLE_LINUX_CHROMIUM_IMAGE@sha256:REVIEWED_DIGEST \
  /app/dcp_first_live_readonly.py
```

This is a proposed command template, NOT directly executable until the immutable image identity and path have been independently verified and this file has been packaged. It has no network and always exits blocked; it does not log in, search, navigate, mutate cart or call broker. After separate owner GO, Theme 4 must approve an actual audited browser/broker wiring and the distinct live command; changing environment flags alone cannot enable LIVE.

**Minimum safe configuration names (never values):** `EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT` (confidential matching only), `EXTREMIZER_DCP_STATE_DIR`, `EXTREMIZER_DCP_BROWSER_SOURCE_VERIFIED`, `EXTREMIZER_DCP_OWNERSHIP_FENCED`, `EXTREMIZER_DCP_SIGNED_BROKER_VERIFIED`, `EXTREMIZER_DCP_LIVE_OWNER_GO`, `EXTREMIZER_DCP_OEM`, `EXTREMIZER_DCP_MANUFACTURER`. Readiness booleans are assertions, not trusted evidence, and must be verified independently.

**One admission blocker:** Polaris dealer price is verified only through authenticated dealer-cart evidence; a no-cart-mutation read-only path for exact OEM 2205250 is NOT PROVEN.

**One action:** Theme 4 documents an authoritative cart-free DCP dealer price source for 2205250 (or requests separate owner approval for the necessary cart operation). Until then, LIVE SEARCH/LOGIN remains HOLD.
