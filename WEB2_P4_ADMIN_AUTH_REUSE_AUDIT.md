# WEB2 P4 — Existing WEB ADMIN Auth Reuse Audit

**Audit type:** READ-ONLY.

## Reusable components found

### supplier_admin_auth.py

Existing primitives:

- `WEB_ADMIN_COOKIE = "extremizer_admin_session"`
- `WEB_ADMIN_SESSION_MAX_AGE = 12 * 60 * 60`
- `issue_web_admin_session()`
- `verify_web_admin_session()`
- `is_web_admin_authorized()`
- `require_web_admin_access()`

Session properties:

- HMAC-SHA256 signature;
- key derived from configured WEB ADMIN token;
- nonce + issued timestamp;
- expiry enforced;
- small future clock-skew tolerance;
- token rotation invalidates old signed sessions;
- constant-time signature/token comparisons.

### web_app.py

Existing WEB ADMIN login:

- validates the existing ADMIN token server-side;
- issues the signed `extremizer_admin_session`;
- sets cookie `HttpOnly`;
- sets `Secure`;
- sets `SameSite=Strict`;
- uses path `/`.

Existing ADMIN pages call the shared authorization helper.

Existing authenticated API example:

`supplier_api._web_admin()`

reuses `require_web_admin_access()` for cookie/header authorization.

## P4 decision

Reuse the signed session verification.

Do **not**:
- create a new ADMIN password;
- create a second login route;
- create a parallel session table;
- store raw ADMIN token in a new cookie.

The isolated WEB2 proof intentionally accepts the signed cookie path only; browser-provided role headers do not establish identity.

## Warehouse employee auth

No authoritative warehouse employee login/membership mechanism was established by this audit.

Therefore warehouse access remains blocked for real wiring.

Disposable `WarehouseReadContext` is a Core test contract, not authentication.

## Production impact

None.

No change to:
- `supplier_admin_auth.py`;
- `web_app.py`;
- production routes;
- real credentials;
- production DB.
