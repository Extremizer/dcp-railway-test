# EXTREMIZER — WEB ADMIN «Заказы» AD-002 / GO-A

Document: EXT-T4-T2-AD002-GOA-ISOLATED-VISUAL-IMPLEMENTATION-20261104-V01
Status: ISOLATED VISUAL DEMO / NOT CONNECTED TO REAL ORDERS / GO-PREVIEW HOLD
Design: страница_заказов_с_лаймовой_навигацией.png, 1534×1025
Design source SHA256: e4d68e8c73d58eff5fb3649bdd723469293d3cef73fb11c4e6fe8be1a70e9690

## Allowed scope
Self-contained static HTML/CSS/JS. Exactly 9 owner-image demonstration rows, entered as immutable local fixture in demo-orders.js; page may filter a display subset but does not mutate any entry. Six columns in the accepted order: заказ | отслеживание | клиент | стоимость | статус | размещение заказа.

Visual details: graphite nav, lime #BFF803, alternating white and gray rows, accepted +18px row-height intent, blue read-only order/client text, gray status/place pills, filters/search, inactive settings and Excel controls, fixed pagination.

## Original binary assets and source SHA256
- assets/Extremizer_vector(10).svg — 0651e2d482c496ac135497910506b1ae7d0672d55d6e47f9cf7f87f87b3c5afa
- assets/fonts/Manrope-variable.ttf — 3ae11c49db0455a3cc33e37d380f20fdb8c7f8b41dc07625c177e3d87a9d6ae6
- assets/fonts/Exo2-variable.ttf — 205a448676a2586f9c57c25f3d5c58ca8db7e6cf5edf7506783a010c6fe2bfb5

Font use: Manrope normal 400/500 for UI; Exo 2 normal 600 for title/headers. Space Grotesk 700 is not required by this Russian admin page and is not copied.

## Boundaries
No production data, API calls, browser storage, server, DB, tracking, real messages, finance, write-sensitive actions, Excel download, status mutation, accounting counters or real authentication. Yellow message marks and numeric counters are synthetic visual fixture elements only. Order/client blue text is not linked; navigation, account, tracking, settings, Excel and pagination are inactive. Search and selected filter controls operate only on nine local fixture rows.

No mobile design claim; narrow widths permit horizontal scrolling and require separately approved responsive design. The layout has not been browser-tested or screenshot-verified in GO-A.

## Gates
GO-A permits writing only files under web_ad002_demo/ in isolated feature branch, no merge/deploy. GO-PREVIEW is required before running any QA/build/server/browser test. GO-B is required for vetted RBAC-safe COMMON CORE integration. GO-RELEASE is separate and requires owner acceptance.
FINANCE SAFETY HOLD ACTIVE.
