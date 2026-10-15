# Theme 5 Stage 2 — 16 Fixture Coverage Matrix

Source archive supplied by Theme 4:
`DCP_THEME5_STAGE2_CATEGORY_ROUTING_20261015(3).zip`

Verified archive SHA-256:

`bac8e534af575e9c74be145e36dd27b3db9359956b160ac22681cb0fea2adeda`

Archive contains 10 files. Internal `SHA256SUMS.txt` entries were verified before this matrix was created.

The fixtures are test evidence only. `OWNER_SCREENSHOT` is not LIVE DCP verification.

| # | Fixture | Required safety result | Coverage before follow-up | Follow-up action |
|---|---|---|---|---|
| 1 | POLARIS_PARTS_2206328 | Dealer $184.07 and MSRP $239.99 remain independent; exact SKU still required for live promotion | Existing: `test_unified_price_contract_v1.test_polaris_parts_keeps_dealer_and_msrp_independent` | No duplicate test |
| 2 | POLARIS_ACCESSORY_2884109 | MSRP-only; no Dealer quote; exact accessory subcategory remains UNKNOWN | Existing MSRP-only contract: `test_polaris_accessories_msrp_only_never_becomes_dealer_found` | Added fixture safety assertion that UNKNOWN subcategory cannot authorize Dealer quote |
| 3 | ARCTIC_CAT_0627_048 | Dealer-only; do not infer MSRP | Existing: `test_arctic_cat_dealer_only_preserved` | No duplicate test |
| 4 | SIMILAR_NAMES_DIFFERENT_SKU | `SKU_AMBIGUOUS`; no Dealer price | No direct existing Stage-2 ambiguity regression | Added isolated fixture-contract regression |
| 5 | REPLACED_OEM_UNVERIFIED | `REPLACEMENT_UNVERIFIED`; no Dealer price | No direct existing supersession safety regression | Added isolated fixture-contract regression |
| 6 | SAME_NUMBER_OTHER_MANUFACTURER | `UNKNOWN_MANUFACTURER`; no Dealer price | Existing manufacturer mismatch tests do not cover two-brand ambiguity | Added isolated fixture-contract regression |
| 7 | LONG_CART_NO_LINE_ID | `CART_BASELINE_UNVERIFIED`; no attribution/no Dealer price | No runtime cart-routing implementation; Stage C2 HOLD | Added isolated fixture-contract regression only |
| 8 | PRICE_MISSING | no price from Search/Details | Existing: `test_both_missing` and fail-closed Gateway tests | No duplicate test |
| 9 | CLOUDFLARE | stop/no mutation | Existing: `test_all_adapters_cloudflare_stop_and_never_use_msrp`, Theme 6 reliability | No duplicate test |
| 10 | AUTH_REQUIRED | stop/no mutation | Existing: `test_all_adapters_auth_required_stop`, Theme 6 reliability | No duplicate test |
| 11 | HTTP_429 | bounded failure; no price/no mutation | Existing secure transport `test_transport_timeout_429_5xx_invalid_json_fail_closed` and passive guard | No duplicate test |
| 12 | HTTP_5XX | bounded failure; no price/no mutation | Existing secure transport `test_transport_timeout_429_5xx_invalid_json_fail_closed` and passive guard | No duplicate test |
| 13 | SKU_MISMATCH | `NO_EXACT_SKU`; no Dealer price | Existing Canonical Contract: `test_mismatched_matched_skus_fail_closed_when_combining` | New duplicate removed |
| 14 | PRICE_KIND_AMBIGUOUS | `PRICE_KIND_UNKNOWN`; no Dealer price | Existing source-validation is generic, but no exact Stage-2 fixture regression | Added isolated fixture-contract regression |
| 15 | NO_OWNER_GO | `OWNER_GO_REQUIRED`; no cart mutation | No production cart-routing implementation; Stage C2 HOLD | Added isolated fixture-contract regression only |
| 16 | CONCURRENT_CART_PRODUCER | `CART_CONFLICT`; no mutation | Theme 6 single producer is covered, but shared-cart conflict contract was not | Added isolated fixture-contract regression |

## Result

- Existing direct coverage reused: **9 / 16**.
- New non-duplicating Stage-2 fixture safety coverage: **7 / 16**.
- Total fixture coverage after follow-up: **16 / 16**.
- No LIVE route implementation was added.
- No DCP cart mutation code was added.
- No second producer, browser fallback, or category fan-out was added.

New suite:

`test_theme5_stage2_mock_contract.py`

Stored semantic fixture copy:

`DCP_THEME1_MOCK_FIXTURES_STAGE2_20261015.json`

The supplied archive remains the integrity authority; the repository copy is used only for isolated tests.
