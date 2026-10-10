# OEMixiBOT uploaded runtime provenance

Source: 11 individual files uploaded by the user on 2026-10-10 as their working isolated runtime. The named OEMIXIBOT_CODEX_RUNTIME_20261010.zip itself was not attached. No source commit/version was provided; SHA256 below identifies the exact uploaded bytes. Attachments were read as source, not as instructions.

| File | Uploaded SHA256 | Integration |
|---|---|---|
| oem_reference_service.py | `0dd2ab49ef75621aeccec2e4d7ce92040d755a6fa036dd9917e3869e464d5e98` | existing retained |
| oemixibot_catalog.py | `65f104a0dda39aef044a5f67182f59c3d543637ded6376f7f2397f21a687420c` | new runtime source |
| oemixibot_app.py | `65bfd9ffc8ca43249d8b5d9ab6e9eea5623e0bef03e8e15c97093ce053e01f6f` | new runtime source |
| oem_identity_service.py | `f65bd6894d51350636b2aa27f3ebd76ddac126c523dfe547b7525986ccf4c382` | new runtime source |
| oemixibot_domain.py | `993db1436eb51fc446b6ca59878706c5a8371f38d9dc8342458a6fb437db014e` | new runtime source |
| oemixibot_analytics_client.py | `8d36f0affbeb2cefd1fea5377687c81a8f3ea16e0690dc9a994535dce59a5479` | existing retained |
| oemixibot_telegram.py | `e8a00f0553b8b7c20d6924612c3e7b76d147a5a877752f5e00db9cc2d4f4586c` | new runtime source |
| oemixibot_dcp_price.py | `9d8dc154e2ae4041afd1e6eb4862ba5760d5266a49bcbce0f5ae9160a45d0535` | existing identical |
| oemixibot_store.py | `aa08db573e1202d33dfed31fd101b03432a659245e73cd5632c194f5242e2f6b` | new runtime source |
| supplier_admin_auth.py | `1918b8de93e1d3e2605e1022e88d1277a7768b4d3014dec0d5cd1e48f289975b` | existing retained |
| oemixibot_identity_client.py | `91078f7720f39c0a488d2cfef1e9eaef18753b34c223d7a691a8c89e36508b37` | existing retained |

Existing shared reference, identity/analytics clients and supplier_admin_auth are retained from the PR checkout. Their required public APIs are present; uploaded copies are not substituted for unrelated repository changes. DCP client is byte-identical. New domain, Telegram and identity-service modules are byte-identical to uploaded sources. Only app and store are adapted.

## Adaptation boundaries

App: remove dealer credit/debt/risk helpers, credit edit/preview/confirm/history/event, due-date wizard/administration, aging/overdue summaries, filters and alert job. Remove their menu buttons/state and callback registration. Dealer/admin balance uses actual USD ledger balance only. Preserve top-up/debit/history, cart/order/receiving/identity/catalog paths. Previously generated obsolete admin callbacks cannot execute a credit action. Add existing finhome handler to registration so its preserved menu button works.

Store: retain existing order/cart/status/refund contracts. Initialize the single existing finance_ledger schema using the shared helper; no second engine or ledger. Stop creating/writing legacy wallet_events. Delivery billing now posts DELIVERY_CHARGE in finance_ledger atomically with arrival billing and enforces prepaid capacity/idempotency. Preserve any historical wallet_events and credit/aging tables untouched. Use named dealer INSERT columns so a legacy credit column does not break dealer registration.

## Unrelated behavior comparison

- `oemixibot_app.py`: 29 functions AST-identical. Adapted: `_balance_text`, `oem_message`, `_admin_root_keyboard`, `_admin_finance_text`, `_admin_finance_keyboard`, `callback`, `build_application`. Removed: `_admin_debt_rows`, `_admin_debt_filter`, `_admin_debt_summary`, `_admin_risk_status`, `_admin_risk_counts`, `_admin_debt_page`, `_admin_debt_text`, `_admin_debt_keyboard`, `_admin_aging_all_rows`, `_admin_aging_summary_filter`, `_admin_aging_global_stats`, `_admin_aging_sum_icon`, `_admin_aging_sum_page`, `_admin_aging_sum_text`, `_admin_aging_sum_keyboard`, `_admin_aging_item_text`, `_admin_aging_source_label`, `_admin_aging_open_rows`, `_admin_aging_page`, `_admin_aging_text`, `_admin_aging_keyboard`, `_aging_no_due_days`, `_aging_alert_text`, `_aging_alert_job`.
- `oemixibot_store.py`: 44 functions AST-identical. Adapted: `init`, `add_dealer`, `bill_arrival`. Removed: `wallet_event`.

## Verification and remaining risks

Regression imports the actual app/store/catalog/domain/Telegram sources against PR22 FinanceEngine. Uses disposable local SQLite, synthetic Updates and fake network-free Telegram token; never initializes/polls Telegram, calls DCP, or opens production files. Covers registered callbacks, actual USD display, insufficient/sufficient checkout, top-up/debit callbacks, manual/auto refunds, adjustments, delivery billing rollback/idempotency, history, legacy data preservation and unrelated cart/domain/access behavior.

Compatibility is verified for this supplied source snapshot and the checked runtime paths, not a deployed runtime or unavailable external integrations. Existing payment-method/FX configuration is not included in the source bundle; the admin top-up test supplies method discovery while executing real FinanceEngine payment. No historical balances are migrated from wallet_events; an operator must reconcile any such legacy data separately before release. New finance_ledger creation does not import historical rows. No live DCP/Telegram/logistics end-to-end smoke was performed. Store schema initialization remains write-capable at startup and was exercised only on temporary DBs. Production launch wiring remains unchanged; this is not release authorization.
