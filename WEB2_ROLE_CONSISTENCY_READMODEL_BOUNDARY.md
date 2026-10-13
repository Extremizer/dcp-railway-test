# WEB2 Role Consistency / Read-Model v1.4 — Isolated Boundary

**Target:** WEB2 UI ROLE-CONSISTENT / READ-MODEL READY / ISOLATED CI PASS

## Included

- Role-consistent fixture preview.
- Explicit presentation-only DEMO MODE.
- Four warehouse business statuses.
- Separate System Error message channel using `#FF3030`.
- Typed WEB2 adapter for Theme 1 `warehouse_history_v14.py`.
- Fail-closed authoritative P handling.
- Browser escaping for dynamic future server strings.
- Local adapter and browser regressions.
- Isolated GitHub Actions workflow.
- Desktop/Mobile screenshots and handoff package.

## Not included

- No Finance/Core changes by Theme 2.
- No payment-route changes.
- No finance/order write endpoints.
- No production DB access.
- No real warehouse/client/dealer authentication.
- No real Acting-As session.
- No DCP submission changes.
- No merge.
- No deploy.
- No WEB2 production enablement.

## Role rule

Outside DEMO MODE the effective role determines the visible working surface.

Current fixture:
`rf_warehouse_user → SKLAD`.

ADMIN/SKLAD visual comparison is only possible after the separate, prominently-labelled DEMO MODE is enabled. DEMO MODE is not an authorization mechanism.

The preview Acting-As exit control explicitly states that no server session is connected, changed or ended.

## Status rule

Business order statuses:
1. Новый — `#FFFFFF`
2. Собран, не оплачен — `#FF6B65`
3. Собран, оплачен — `#FFF176`
4. Отгружен — `#BFF803`

System Error `#FF3030` is a separate system alert with context.

## Read-model rule

Finance/history values come from Theme 1 `warehouse_history_v14.py`.
Status/client/OEM metadata must come from an authoritative scoped Order Core projection.

Missing authoritative P remains null and renders as `—`. No browser fallback or synthetic amount is permitted for future server wiring.

## CI rule

The isolated workflow must run on the exact pushed branch HEAD and prove:
- Theme 1 warehouse-history regression on Python 3.12;
- typed adapter regression;
- role-consistency/browser regression;
- JS/Python syntax.

A local test result must not be reported as GitHub CI.
