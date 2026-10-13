# EXTREMIZER WEB — HANDOFF

## ТЕМА 2 → ТЕМА 4

**Направление:** WEB2 UI / Finance v1.4 / Role-Based Zones  
**STATUS: WEB2 UI ISOLATED PASS / FINANCE WIRING PENDING**  
**Дата:** 13.10.2026

### 1. Контрольная точка

Изолированная UI-реализация выполнена поверх Finance v1.4 base без изменения FinanceEngine, OrderEngine, payment routing, production runtime или реальных данных.

Branch:
`web2-ui-finance-v14-20261013`

Finance v1.4 base:
`a1cbce890a5388435f25298817b7a445456d4206`

Implementation commit:
`920fa211ecef7cfe6221ef24d90ea2922b0d09dc`

Evidence commit:
`2ca535dffc11a16e3b097de546a170079cc288a3`

### 2. Реализовано

1. Warehouse History Desktop ADMIN.
2. Warehouse History Desktop SKLAD.
3. Warehouse History Mobile structured cards.
4. Client Payments / Wallet History fixture preview.
5. Dealer USD Balance / DCP Financial Gate fixture preview.
6. Persistent Acting-As fixture indicator.

UI consumes only `finance_role_preview_fixture.json`.
No finance truth is calculated from browser localStorage.

### 3. Warehouse History contract

Desktop uses exactly five main columns.

ADMIN:
`Дата | № нашего заказа / № заказа на складе | Сумма для клиента | Сумма по условиям склада | Баланс`

SKLAD uses the same five fields with the warehouse number first.

Verified:
- default 15 rows;
- 20-row mode;
- pagination;
- search by both order numbers, client, OEM and date;
- both order numbers open the same fixture order card;
- unpaid Balance is blank;
- `−P` strikes the client amount;
- posted/reserved/available displayed separately;
- full-history total remains `3 425 ₽` regardless of filter;
- control row `I17J-5675 / 5241 | ~~10 000 ₽~~ | 8 500 ₽ | −8 500 ₽`.

### 4. Palette

Verified exact colors:
- Новый — `#FFFFFF`;
- Собран, не оплачен — `#FF6B65`;
- Собран, оплачен — `#FFF176`;
- Отгружен — `#BFF803`;
- System Error — `#FF3030`.

Status text remains visible; color is not the sole status indicator.

### 5. Client Payments preview

Fixture events:
- `INFO_DIRECT_WAREHOUSE_PAYMENT`;
- `INFO_DIRECT_WAREHOUSE_REFUND`.

The real external RUB amount is displayed.
Wallet delta remains exactly `0 ₽`.

No fake PAYMENT or ORDER_CHARGE is rendered.

### 6. Dealer USD / DCP Gate preview

Verified UI separation:
- aggregate Dealer USD balance;
- order status;
- DCP Financial Gate.

Fixture debt state:
- USD balance `−$60.00`;
- order status `Подтвержден`;
- DCP Financial Gate `BLOCKED`;
- `ORDER_CHARGE ×1`.

Fixture repayment to `$0.00` changes the displayed finance gate to `READY`.

No credit-limit UI exists.
Preview explicitly states that supplier/DCP enforcement is not yet wired.

### 7. Acting-As preview

Persistent fixture banner displays:
- actual ADMIN;
- effective subject;
- effective role;
- explicit return/exit action.

Exit is local UI only and explicitly reports that no real Acting-As session was changed.

No real user is connected and no credential is requested.

### 8. UI regression

Command:
`py -3 -m unittest -v test_web2_finance_role_ui.py`

Result:
`Ran 7 tests in 26.925s — OK`

PASS:
- five Desktop columns;
- ADMIN/SKLAD number order;
- same order card from both numbers;
- search;
- default 15 / option 20;
- full-history balance invariant;
- blank unpaid balance;
- struck client amount for `−P`;
- exact status palette + text;
- Mobile five-field cards, no horizontal overflow;
- Client informational events with wallet delta 0;
- Dealer negative balance + separate gate + one charge;
- Acting-As fixture indicator.

### 9. Required server-side read models / APIs before wiring

No endpoint names below are claimed to exist yet. COMMON CORE / Theme 1 remains authoritative.

**Warehouse History read model**
Needs:
- warehouse_id;
- posted_rub;
- reserved_rub;
- available_rub;
- full_history_balance_rub;
- paginated rows containing date, canonical EXTREMIZER order number, canonical warehouse order number, client display/search field, OEMs, customer amount, partner amount, ready-to-render balance delta/blank-unpaid semantic, payment-source/strike semantic, status code + text.

Authorization:
ADMIN capability or exact warehouse resource scope.

**Client Wallet read model**
Needs:
- client_id;
- current RUB wallet balance;
- event_type;
- order_id;
- description;
- wallet delta amount;
- source_amount/source_currency;
- reference_type/reference_id;
- created_at.

Authorization:
exact client scope or authorized Acting-As intersection.

**Dealer USD Finance read model**
Needs:
- dealer_id;
- aggregate USD balance;
- dcp_financial_gate_ready;
- dealer order snapshot/status;
- order total USD;
- idempotent ORDER_CHARGE evidence/reference.

No credit-limit mechanism.
Gate readiness must not be presented as proof of supplier enforcement until supplier/DCP wiring exists.

**Acting-As read/session model**
Needs:
- actual admin actor;
- effective subject;
- effective role;
- data/resource scope;
- active session id/status;
- expiry if required by Theme 4;
- explicit end/return action.

Authorization:
ACT_AS, plus ACT_AS_CRITICAL for critical writes and normal business guards.

**Order Card read model**
Needs role-scoped projection of:
- canonical EXTREMIZER number;
- canonical warehouse number where applicable;
- customer/partner amounts;
- status;
- positions/quantities;
- permitted documents/tracking;
- searchable client/OEM metadata.

Both displayed order numbers must resolve to the same authoritative order.

### 10. Integration gates still open

Real Finance wiring remains blocked until Theme 1 / COMMON CORE closes:

1. Internal default payment routing in persisted Order Core snapshots: warehouse must use authoritative CLIENT → WAREHOUSE rule.
2. Warehouse Balance v1.4 authoritative history read model.
3. Canonical warehouse-order-number field/source.
4. Scoped order-card projections by role/resource.
5. Acting-As real identity resolver/router.
6. Supplier/DCP enforcement of `dcp_procurement_ready()` before automatic DCP blocking can be claimed.

### 11. Changed-file boundary

Compared with base `a1cbce...`, this branch only adds isolated UI preview, fixtures, UI tests, documentation and screenshots.

No existing FinanceEngine, OrderEngine, payment-routing, ledger or production runtime file is modified.

### 12. Safety

NO PRODUCTION CHANGES  
NO MERGE  
NO DEPLOY  
NO WEB2 ENABLEMENT  
NO PRODUCTION DB WRITES  
NO REAL PAYMENTS  
NO REAL ORDER MUTATIONS  
NO REAL ACTING-AS USER CONNECTION  
NO DCP SUPPLIER SUBMISSION

## FINAL STATUS

`WEB2 UI ISOLATED PASS / FINANCE WIRING PENDING`
