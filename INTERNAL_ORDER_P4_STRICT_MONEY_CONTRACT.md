# INTERNAL ORDER P4 — STRICT MONEY / QUANTITY CONTRACT

Status: isolated P4 contract. It does not alter FinanceEngine historical arithmetic.

## Units

Internal Order Core v0.1 uses whole RUB integers for:

- customer_unit_rub;
- customer_amount_rub C;
- partner_unit_rub;
- partner_amount_rub P;
- confirmed_amount_rub.

Quantity is a positive integer count.

## Accepted runtime types

For money inputs handled by the P4 Internal service:

- Python type must be exactly `int`;
- `bool` is rejected even though bool subclasses int;
- negative values are rejected;
- float values are rejected;
- decimal strings are rejected;
- integer strings are rejected rather than silently coerced;
- NaN and Infinity are rejected because floats are rejected;
- no `int(value)` truncation/coercion is allowed.

For quantities:

- exact Python `int`;
- > 0;
- bool/float/string/zero/negative rejected.

## Persisted shared order data

Authoritative C is calculated only from persisted shared `order_items` after
strict validation of:

- quantity;
- customer_unit_rub.

If SQLite returns float/text values for either field, snapshot creation fails
closed.

Python bool values already persisted by SQLite are indistinguishable from
integer 0/1 after storage. Therefore the real order persistence boundary must
also enforce this contract before writing shared `order_items`.

P4 does not modify the existing production order writer or FinanceEngine.

## Arithmetic

C:

`SUM(customer_unit_rub * quantity)`

P in the authoritative-terms prototype:

`SUM(partner_unit_rub * confirmed_quantity)`

Both calculations use integer arithmetic only.

No rounding is performed inside Internal Order Core P4.
