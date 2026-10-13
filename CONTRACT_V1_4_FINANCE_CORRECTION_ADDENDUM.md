# CONTRACT v1.4 — FINANCE CORRECTION ADDENDUM

Base: CONTRACT v1.3. This addendum changes only the approved finance/acting-as points below.
No production enablement is implied.

## 1. Partner warehouse balance — final owner rule

Each partner warehouse has an independent EXTREMIZER internal balance.

Five-column history keeps the approved role-specific order columns. The balance
event semantics are:

- unpaid Internal order: no partner-balance posting;
- full direct client payment confirmed by the authorized warehouse actor:
  post exactly one `+(C-P)`;
- another order paid fully from accumulated EXTREMIZER warehouse balance:
  post exactly one `-P`;
- historical accruals are never rewritten after later spending;
- current warehouse balance is the sum of the full warehouse ledger history,
  not the visible filtered page.

Example:

`+2 925 +1 500 +3 000 +4 500 -8 500 = 3 425 RUB`.

Direct-payment confirmation and `+(C-P)` are one atomic transaction.
Duplicate/replayed confirmation returns the same result and never adds again.
Warehouse balances are isolated by `warehouse_id`.

Warehouse-balance spending is full-coverage only:

`available >= P`.

Partial use or `warehouse balance + another payment source` is prohibited.
Reservation/application must serialize concurrent attempts.

## 2. Internal payment routing

Default:

client pays the selected partner warehouse directly.

A cart containing multiple warehouses produces separate Internal orders and
separate payment obligations per warehouse. Client does not choose the payee.

Administrative exception:

admin may assign payment to EXTREMIZER only when the accumulated balance of that
same partner warehouse fully covers `P`.

The warehouse balance is reserved/applied atomically for the full `P`.
No partial balance use is allowed.

## 3. Direct warehouse payment in client RUB history

Direct client -> warehouse payment does not change the client's EXTREMIZER RUB
wallet. Direct warehouse -> client refund also does not change that wallet.

Both are recorded as informational history events with wallet delta exactly
`0 RUB`, while preserving the real external amount and source reference.

No fake PAYMENT and no fake ORDER_CHARGE may be created for direct partner flow.

## 4. Client pays EXTREMIZER for Internal order

When admin explicitly assigns EXTREMIZER as payment recipient, actual money flow
is reflected in the normal client RUB wallet:

1. confirmed receipt of client money -> one real `PAYMENT +C`;
2. order settlement -> one real `ORDER_CHARGE -C`.

Both are idempotent and tied to the order/payment references. Replays do not
create a second top-up or second charge.

This closes the previous A/B uncertainty for this approved administrative path:
the client EXTREMIZER wallet is used.

## 5. Dealer USD wallet — final owner rule

Dealer order placement is not limited by current USD balance.

At order confirmation:

- one full USD `ORDER_CHARGE` is posted;
- negative dealer balance is allowed;
- there is no credit limit;
- insufficient funds do not block creating/confirming the order.

No legacy credit-limit value authorizes or blocks the order.

DCP procurement is blocked while dealer aggregate USD wallet balance is below
zero. Payments reduce the aggregate debt. The order charge remains individually
linked to its order and is never posted twice.

Other debits (manual debit, delivery charge) do not automatically inherit the
order-overdraft permission.

If future business needs selective release of one DCP order while several
dealer orders are unpaid, an explicit payment-to-order allocation contract is
still required; v1.4 does not invent one.

## 6. Refunds

Client may submit a request but cannot unilaterally cancel an order.

Decision owner:

- Internal: partner warehouse;
- External: EXTREMIZER.

Money is returned by the party that actually received the payment.

Refund and supporting document are linked to the order. Finance history is
append-only: original payment/charge/accrual rows remain, and any approved
refund/correction is a new idempotent event.

## 7. Acting-As

CONTRACT v1.3 remains in force.

Admin Acting-As must preserve:

- actual admin actor;
- effective subject;
- role and data scope;
- reason;
- action;
- timestamp.

Acting-As never bypasses balance, quantity, status, refund, warehouse-isolation,
or idempotency guards.

Critical financial actions require an additional admin permission and retain the
actual administrator as the executor in audit.

## 8. Isolated implementation boundary

Branch:

`finance-contract-v14-20261013`

Base:

`74ce11eb864ee4d852c60e9ffdb140ef73a46517`

No merge, deployment, WEB2 enablement, production DB write, or production
migration is authorized by this addendum.
