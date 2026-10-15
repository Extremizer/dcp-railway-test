# WEB2 POWERSPORTS Design Integration Plan

**Status:** integration preparation only. Final graphic montage is blocked until Theme 7 delivers the exact approved horizontal PNG and its SHA-256.

## 1. Objective

Integrate the approved EXTREMIZER LIFE / POWERSPORTS — MACHINE composition into the existing unified WEB2 without creating a second search, pricing, cart, handoff, or checkout implementation.

## 2. Existing functional anchors that must remain unchanged

The current frontend already exposes the stable DOM/function anchors required by the working WEB2:

- `#searchForm`
- `#oemInput`
- `#searchButton`
- `#state`
- `#product`
- `#offers`
- `#qtyMinus`
- `#qtyPlus`
- `#cartButton`
- `#cartPanel`
- `#cartItems`
- `#telegramButton`

Existing JS/API flow:

`/api/v1/oem/{oem}`
→ server price/card
→ `/api/v1/oem/{oem}/stock`
→ USA/RF offers
→ local cart
→ `POST /api/v1/handoff`

No part of this chain needs to be duplicated for POWERSPORTS.

## 3. Minimal visual integration

Primary files:

### `web2/index.html`

Change only composition/markup around the existing functional nodes:

- new POWERSPORTS top section;
- horizontal Theme 7 logo slot;
- navigation LIFE / OEM+ / КОРЗИНА;
- original garage photo container;
- POWERSPORTS / MACHINE headings;
- place existing `#searchForm` inside the approved transparent search treatment;
- retain existing product/offers/cart DOM and IDs.

Do NOT duplicate:
- search form;
- cart;
- product result card;
- handoff CTA.

### `web2/styles.css`

Implement:
- approved photo/background composition;
- transparent OEM input/button treatment;
- Desktop 1440 layout;
- Mobile 390 adaptation;
- navigation and header spacing;
- safe overlay/contrast rules.

Existing product/cart styles may be restyled visually but must retain touch targets, cart z-index/safe-area, overflow behavior and accessibility.

### `web2/app.js`

Expected change: **none** for the design integration.

Only change JS if the final composition requires a pure presentation toggle that cannot be expressed in CSS/HTML. Do not add pricing/search/cart formulas.

### `web2_ui_routes.py`

Prepared exact Theme 7 asset endpoint:

`GET /web2-powersports-logo`

Environment:

- `EXTREMIZER_POWERSPORTS_LOGO_PATH`
- `EXTREMIZER_POWERSPORTS_LOGO_SHA256`

The route serves the PNG only when:
- both values exist;
- SHA is exactly 64 lowercase/uppercase hex characters after normalization;
- file exists;
- file has PNG signature;
- file SHA-256 exactly matches.

No fallback to the square source logo.

## 4. Theme 7 logo installation

After Theme 7 returns the approved horizontal PNG:

1. Record exact filename, dimensions and SHA-256 in Theme 4 handoff.
2. Put the unchanged PNG on the isolated staging filesystem.
3. Set:
   - `EXTREMIZER_POWERSPORTS_LOGO_PATH=/staging-assets/<exact-name>.png`
   - `EXTREMIZER_POWERSPORTS_LOGO_SHA256=<approved-sha>`
4. Verify SHA independently before launch.
5. Use `/web2-powersports-logo` in the final header image.
6. Do not resize the file itself. Browser/CSS display scaling is allowed; crop is not.
7. Keep the square canonical logo as historical source/reference only, not as the final POWERSPORTS header substitute.

## 5. Original garage photo

Theme 3/7 must provide the exact approved original garage image/file reference.

Integration rule:
- never redraw;
- never generate a substitute;
- never alter scene content;
- CSS may use `object-fit: cover` only if Theme 4 explicitly accepts the resulting viewport crop;
- otherwise use contain/background positioning that preserves the whole approved frame.

## 6. Functional preservation rules

POWERSPORTS must keep:

- server-provided customer RUB price;
- client rounding upward to 100 RUB performed server-side;
- `29 200* ₽` format;
- MSRP only when MSRP > customer price;
- benefit only when the server supplies `benefit_pct`;
- USA delivery footnote only when USA offer exists;
- server stock/warehouse offers;
- stock limits;
- cart/localStorage;
- server handoff price revalidation;
- one-time handoff token;
- Procenka confirmation;
- atomic checkout;
- Finance idempotency.

No client-side calculation of price, MSRP or benefit.

## 7. Navigation

Approved labels:

- LIFE
- OEM+
- КОРЗИНА

Preparation behavior:

- КОРЗИНА must activate the existing `#cartButton` / cart drawer, not create a second cart.
- OEM+ should focus/scroll to the existing OEM search.
- LIFE destination remains a Theme 4/owner decision unless a current approved route exists.

Do not invent a LIFE destination in prep.

## 8. Desktop 1440 acceptance

At 1440 px:

- exact Theme 7 horizontal logo fully visible and not cropped;
- LIFE / OEM+ / КОРЗИНА visible in one header row;
- garage photo composition matches approved master;
- POWERSPORTS / MACHINE hierarchy matches approved master;
- transparent OEM search is readable over the image;
- search input/button remain keyboard accessible;
- product result begins below hero without overlay collisions;
- cart drawer remains above header/scrim;
- no horizontal overflow;
- `29 200* ₽` unchanged;
- USA/RF offer CTA usable.

## 9. Mobile 390 acceptance

At 390 px:

- horizontal logo uses CSS scaling only and remains recognizable/unclipped;
- nav does not create horizontal overflow;
- OEM search remains readable and tappable;
- search input/button each meet mobile touch expectations;
- transparent treatment has sufficient contrast over the original photo;
- POWERSPORTS / MACHINE text does not overlap logo/search;
- product/offers remain single-column where needed;
- cart remains full-width and uses safe-area;
- all existing ≥44×44 touch controls remain;
- no horizontal overflow.

If the approved horizontal logo cannot fit the mobile header without becoming illegible, Theme 4 must approve an alternate placement. Do not edit the logo.

## 10. Regression set after final montage

Run the existing regressions, not a duplicate implementation:

### Client UI/API
- `test_web2_staging.py`
- `test_web2_client_mvp.py`
- `test_web2_host_integration.py`

### Unified checkout
- `test_web2_procenka_checkout_e2e.py`
- `_client_checkout_orchestrator_regression.py`
- `_client_checkout_callback_adapter_regression.py`
- `_save_order_shared_tx_regression.py`
- `test_web2_contract.py`

### Add only visual-specific assertions
- POWERSPORTS logo visible when exact asset+SHA provided;
- logo unavailable on missing/wrong SHA;
- Desktop 1440 no overlap/overflow;
- Mobile 390 no overflow;
- transparent search controls visible/readable;
- cart/open-state still passes existing layering/touch assertions.

## 11. Explicit non-goals

- no new search API;
- no new pricing service;
- no new cart state;
- no new checkout;
- no FinanceEngine changes;
- no production route changes;
- no live DCP;
- no real Telegram order;
- no final graphic montage before Theme 7 result.
