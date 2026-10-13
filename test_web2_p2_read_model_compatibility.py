from __future__ import annotations

import unittest

from web2_finance_read_model_adapter import (
    ReadModelContractError,
    adapt_warehouse_history,
    to_fixture_dict,
)


def history_row(**overrides):
    row = {
        "warehouse_id": 1,
        "date": "2026-10-13",
        "extremizer_order_number": "I-P2-1",
        "warehouse_order_number": "5234",
        "customer_amount_rub": 19500,
        "partner_amount_rub": None,
        "partner_amount_source": None,
        "balance_rub": None,
    }
    row.update(overrides)
    return {
        "summary": {
            "warehouse_id": 1,
            "posted_balance_rub": 3425,
            "reserved_balance_rub": 1000,
            "available_balance_rub": 2425,
        },
        "rows": [row],
    }


def metadata(**overrides):
    meta = {
        "warehouse_id": 1,
        "status": "Собран, не оплачен",
        "client": "Клиент",
        "oems": ["417300574"],
    }
    meta.update(overrides)
    return {"I-P2-1": meta}


class Web2P2CompatibilityTests(unittest.TestCase):
    def test_due_paid_is_separate_from_business_status(self):
        for payment_status in ("DUE", "PAID"):
            with self.subTest(payment_status=payment_status):
                model = adapt_warehouse_history(
                    history_row(payment_status=payment_status),
                    order_metadata_by_id=metadata(status="Собран, не оплачен"),
                )
                row = model.rows[0]
                self.assertEqual(row.payment_status, payment_status)
                self.assertEqual(row.status, "Собран, не оплачен")
                self.assertEqual(row.status_key, "assembled-unpaid")

    def test_invalid_payment_status_fails_closed(self):
        with self.assertRaisesRegex(ReadModelContractError, "INVALID_PAYMENT_STATUS"):
            adapt_warehouse_history(
                history_row(payment_status="WAITING"),
                order_metadata_by_id=metadata(),
            )

    def test_missing_authoritative_p_stays_none_and_serializes_for_em_dash(self):
        model = adapt_warehouse_history(
            history_row(partner_amount_rub=None, partner_amount_source=None),
            order_metadata_by_id=metadata(),
        )
        row = model.rows[0]
        self.assertIsNone(row.partner_amount_rub)
        self.assertFalse(row.partner_amount_authoritative)
        self.assertIsNone(row.balance_delta_rub)
        payload = to_fixture_dict(model)
        self.assertIsNone(payload["warehouse"]["rows"][0]["partnerAmountRub"])
        self.assertIsNone(payload["warehouse"]["rows"][0]["balanceDeltaRub"])

    def test_exact_warehouse_scope_required_for_rows_and_metadata(self):
        with self.assertRaisesRegex(ReadModelContractError, "WAREHOUSE_SCOPE_MISMATCH"):
            adapt_warehouse_history(
                history_row(warehouse_id=2),
                order_metadata_by_id=metadata(warehouse_id=2),
            )
        with self.assertRaisesRegex(ReadModelContractError, "ORDER_METADATA_SCOPE_MISMATCH"):
            adapt_warehouse_history(
                history_row(),
                order_metadata_by_id=metadata(warehouse_id=2),
            )

    def test_scoped_order_metadata_is_mandatory(self):
        with self.assertRaisesRegex(ReadModelContractError, "ORDER_METADATA_REQUIRED"):
            adapt_warehouse_history(history_row(), order_metadata_by_id={})
        with self.assertRaisesRegex(ReadModelContractError, "MISSING_REQUIRED_FIELD:oems"):
            adapt_warehouse_history(
                history_row(),
                order_metadata_by_id={
                    "I-P2-1": {
                        "warehouse_id": 1,
                        "status": "Новый",
                        "client": "Клиент",
                    }
                },
            )

    def test_summary_is_server_full_history_total_not_visible_rows(self):
        raw = history_row()
        raw["rows"] = []
        model = adapt_warehouse_history(raw, order_metadata_by_id={})
        self.assertEqual(model.rows, ())
        self.assertEqual(model.summary.posted_rub, 3425)
        self.assertEqual(model.summary.full_history_balance_rub, 3425)
        self.assertEqual(model.summary.available_rub, 2425)

    def test_p2_authoritative_sources_remain_supported(self):
        for source, balance in (
            ("direct_payment_confirmation", 2925),
            ("warehouse_balance_reservation", -8500),
        ):
            with self.subTest(source=source):
                model = adapt_warehouse_history(
                    history_row(
                        partner_amount_rub=16575 if balance > 0 else 8500,
                        partner_amount_source=source,
                        balance_rub=balance,
                    ),
                    order_metadata_by_id=metadata(status="Отгружен"),
                )
                self.assertTrue(model.rows[0].partner_amount_authoritative)
                self.assertEqual(model.rows[0].payment_source, source)

    def test_internal_payment_snapshot_is_compat_only_and_not_default_wiring(self):
        raw = history_row(
            partner_amount_rub=16575,
            partner_amount_source="internal_payment_snapshot",
            payment_status="DUE",
        )
        with self.assertRaisesRegex(
            ReadModelContractError,
            "INTERNAL_PAYMENT_SNAPSHOT_COMPAT_NOT_ENABLED",
        ):
            adapt_warehouse_history(raw, order_metadata_by_id=metadata())

        model = adapt_warehouse_history(
            raw,
            order_metadata_by_id=metadata(),
            allow_internal_payment_snapshot_compat=True,
        )
        row = model.rows[0]
        self.assertEqual(row.payment_source, "internal_payment_snapshot")
        self.assertEqual(row.payment_status, "DUE")
        self.assertTrue(row.partner_amount_authoritative)

    def test_unknown_partner_source_fails_closed(self):
        with self.assertRaisesRegex(ReadModelContractError, "UNAPPROVED_PARTNER_AMOUNT_SOURCE"):
            adapt_warehouse_history(
                history_row(
                    partner_amount_rub=100,
                    partner_amount_source="legacy_partner_settlement",
                ),
                order_metadata_by_id=metadata(),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
