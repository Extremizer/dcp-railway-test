# Canonical Price Contract v1 — Stage B

Status: ISOLATED CONTRACT READY. No production activation, DCP cart mutation, producer change or schema migration.

## 1. Purpose

One versioned pricing record for OEMixiBOT, Probnik, Procenka and future WEB2 while keeping Dealer Price and MSRP independent.

Version:

`canonical-price-v1`

Canonical fields:

- `version`
- `manufacturer`
- `requested_oem`
- `matched_oem`
- `item_category`
- `dealer_price_usd`
- `dealer_price_source`
- `dealer_verified_at`
- `msrp_usd`
- `msrp_source`
- `msrp_verified_at`
- `identity_status`
- `dealer_price_status`
- `msrp_status`
- `gateway_status`

Each price has independent source, timestamp, freshness decision and validation status.

## 2. Core invariants

1. MSRP is never copied into Dealer Price.
2. Dealer Price is never copied into MSRP.
3. A numeric HTML value is not authoritative merely because it contains `$`.
4. Invalid values are unusable:
   - bool;
   - string-number;
   - zero;
   - negative;
   - NaN;
   - Infinity.
5. Manufacturer/requested OEM/matched SKU identity must be coherent.
6. Different matched SKU claims from Dealer and MSRP evidence fail closed.
7. Freshness policy is explicit per price. Missing policy does not silently use a default.
8. Stale evidence remains non-authoritative for consumer pricing.
9. Dealer source must be explicitly dealer-authoritative and must not be MSRP/Retail/RRP-labelled.
10. MSRP source must be explicitly MSRP-authoritative.
11. Gateway/transient failures never become dealer FOUND.
12. No direct-browser fallback exists in CanonicalPriceGatewayAdapter.

## 3. Status model

Observation statuses:

- `VERIFIED`
- `MISSING`
- `INVALID`
- `STALE`
- `SOURCE_UNVERIFIED`
- `IDENTITY_MISMATCH`
- `FRESHNESS_UNSET`

Identity:

- `VERIFIED`
- `INVALID`
- `MISMATCH`

Gateway status is preserved independently, including:

- `FOUND`
- `CLOUDFLARE`
- `AUTH_REQUIRED`
- `RATE_LIMITED`
- `TECHNICAL_ERROR`
- `TIMEOUT`
- `ADMISSION_CLOSED`

## 4. Existing storage compatibility

No existing tables or keys are changed.

Current Dealer Price authority remains:

`dealer_price_cache`

Current MSRP/catalog evidence remains separate, including existing:

`oem_catalog_cache`

and alias/catalog identity mechanisms.

Stage B composes independent observations on read.

Adding MSRP therefore does not UPDATE or overwrite `dealer_price_cache`.

No migration/backfill is included.

## 5. Shared Gateway integration

Architecture remains:

OEMixiBOT / Probnik / Procenka / future WEB2
→ Shared Gateway
→ Verified Cache
→ Shared Broker
→ Single Producer

`CanonicalPriceGatewayAdapter` accepts:

- existing shared Dealer gateway callable;
- separate read-only MSRP reader callable;
- explicit Dealer freshness;
- explicit MSRP freshness.

It does not implement:

- DCP browser;
- producer;
- queue;
- broker;
- cart operations;
- direct fallback.

If Dealer gateway fails but MSRP is independently verified, the result may be informational MSRP-only; Dealer remains unavailable.

## 6. Consumer adapters

### OEMixiBOT

`oemixi_offer_from_contract()`

- `dl_usd` only from VERIFIED Dealer Price;
- MSRP may be present informationally;
- MSRP-only is never dealer `FOUND`;
- no cart/finance operation is created by the adapter.

### Procenka

`procenka_raw_price_inputs()`

Provides only verified raw USD inputs and provenance.

It does not calculate:

- customer RUB;
- RRP RUB;
- benefit.

Existing Procenka pricing functions remain authoritative.

### Probnik

`probnik_raw_price_inputs()`

Provides only:

- verified `dp_usd`;
- verified `msrp_usd`;
- identity/provenance.

Existing Probnik pricing/card functions remain authoritative.

No approved formula, exchange rate, coefficient, rounding or output format is changed.

## 7. Admin OEM card

Stage A remains intact.

Dealer + MSRP:
- show both separately.

Dealer-only:
- show Dealer Price only.

MSRP-only:
- explicitly say Dealer Price unavailable;
- show verified MSRP informationally;
- no cart/order action.

Neither:
- show unavailable/error status.

No FinanceEngine mutations.

## 8. Theme 5 evidence used

The five READ-ONLY Theme 5 research documents were reviewed before contract implementation.

Confirmed bounded examples:

### Polaris Parts 2206328

Owner-observed:
- MSRP: $239.99 from search;
- Dealer Price: $184.07 from authorized cart.

Used only as separate-provenance mock evidence.

### Polaris Accessories 2884109

Owner-observed:
- search/details MSRP: $10.99;
- Dealer Price: UNKNOWN.

Mock contract must remain MSRP-only and non-orderable by Dealer Price logic.

### Arctic Cat 0627-048

Owner-observed:
- Dealer Price: $85.51 from cart observation;
- MSRP: UNKNOWN.

No rule is inferred for other manufacturers.

## 9. Theme 5 limits preserved

Research status remains:

PARTIAL / LIVE DCP EVIDENCE BLOCKED.

Not implemented as production-ready rules:

- universal Polaris Parts routing;
- Accessories → SEE DETAILS routing;
- Oil routing;
- Apparel & Gear routing;
- manufacturer-wide assumptions;
- automatic cart pricing;
- cart mutation.

No ADD TO CART or REMOVE FROM CART was performed.

## 10. Shared-cart safety

If future Dealer Price requires cart mutation:

- explicit owner GO required;
- exclusive producer/lease;
- stable cart-line identity;
- exact SKU/variant;
- baseline/post-state comparison;
- no fuzzy row matching;
- no automatic retry on ambiguity;
- add authorization does not imply remove authorization.

Stage B performs none of these operations.

## 11. Freshness policy

Dealer and MSRP freshness are independent.

No production TTL is selected by this patch.

Both policies must be explicit finite positive values at adapter construction.

Test values are fixtures only and are not production policy.

## 12. Compatibility / rollout

Existing PR #38 SharedDealerPriceAdapter remains unchanged.

Existing PR #39 admin search remains available.

Canonical v1 is an additive layer.

Production activation still requires:

- approved Windows/server transport topology;
- scoped authenticated transport if OEMixi remains Windows-hosted;
- approved Dealer freshness;
- approved MSRP freshness;
- single-producer/first-switch proof;
- Release Control;
- owner GO.

## 13. Stage C boundary

Category-specific Search → Details → Cart routing must wait for sufficiently verified Theme 5 evidence.

If real cart mutation becomes necessary, STOP and request separate owner authorization.

No producer/Cloudflare/agent changes belong to Theme 1 Stage B.
