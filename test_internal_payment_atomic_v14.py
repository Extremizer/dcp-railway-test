"""CONTRACT v1.4 P1 regression: one caller-owned Internal payment transaction."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from client_finance_schema import ensure_client_finance_schema
import client_wallet_info_v14 as info
import warehouse_balance_v14 as wb


class InternalAtomicPaymentV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "atomic.db")
        ensure_client_finance_schema(self.db)
        wb.init(self.db)
        with sqlite3.connect(self.db) as c:
            c.execute(
                """CREATE TABLE internal_payment_state(
                     order_id TEXT PRIMARY KEY,
                     warehouse_id INTEGER NOT NULL,
                     client_id TEXT NOT NULL,
                     status TEXT NOT NULL
                   )"""
            )
            c.execute(
                "INSERT INTO internal_payment_state VALUES('I-ATOMIC',1,'c1','DUE')"
            )

    def counts(self):
        with sqlite3.connect(self.db) as c:
            return {
                "status": c.execute(
                    "SELECT status FROM internal_payment_state WHERE order_id='I-ATOMIC'"
                ).fetchone()[0],
                "confirmations": c.execute(
                    "SELECT COUNT(*) FROM warehouse_direct_payment_confirmations"
                ).fetchone()[0],
                "warehouse_ledger": c.execute(
                    "SELECT COUNT(*) FROM warehouse_internal_balance_ledger"
                ).fetchone()[0],
                "client_info": c.execute(
                    "SELECT COUNT(*) FROM finance_ledger "
                    "WHERE event_type='INFO_DIRECT_WAREHOUSE_PAYMENT'"
                ).fetchone()[0],
            }

    def apply_flow(self, *, fail_after=None, confirmation_id="pay-atomic"):
        c = sqlite3.connect(self.db)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE internal_payment_state SET status='PAID' WHERE order_id='I-ATOMIC'"
            )
            if fail_after == "status":
                raise RuntimeError("fail after status")

            wb.confirm_direct_payment_and_accrue(
                self.db,
                confirmation_id=confirmation_id,
                warehouse_id=1,
                order_id="I-ATOMIC",
                customer_amount_rub=19500,
                partner_amount_rub=16575,
                actual_actor="warehouse:user1",
                effective_subject="warehouse:1",
                conn=c,
            )
            self.assertTrue(c.in_transaction)
            if fail_after == "warehouse":
                raise RuntimeError("fail after warehouse")

            info.record_direct_payment(
                self.db,
                client_id="c1",
                order_id="I-ATOMIC",
                warehouse_id=1,
                external_amount_rub=19500,
                reference_id="direct-payment-I-ATOMIC",
                actual_actor="warehouse:user1",
                effective_subject="warehouse:1",
                description="Direct partner payment",
                conn=c,
            )
            self.assertTrue(c.in_transaction)
            if fail_after == "client_info":
                raise RuntimeError("fail after client info")

            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def test_success_commits_all_four_facts_together(self):
        self.apply_flow()
        self.assertEqual(
            self.counts(),
            {
                "status": "PAID",
                "confirmations": 1,
                "warehouse_ledger": 1,
                "client_info": 1,
            },
        )
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_rollback_after_status_leaves_no_finance(self):
        with self.assertRaisesRegex(RuntimeError, "after status"):
            self.apply_flow(fail_after="status")
        self.assertEqual(
            self.counts(),
            {
                "status": "DUE",
                "confirmations": 0,
                "warehouse_ledger": 0,
                "client_info": 0,
            },
        )

    def test_rollback_after_warehouse_accrual_removes_accrual_and_status(self):
        with self.assertRaisesRegex(RuntimeError, "after warehouse"):
            self.apply_flow(fail_after="warehouse")
        self.assertEqual(
            self.counts(),
            {
                "status": "DUE",
                "confirmations": 0,
                "warehouse_ledger": 0,
                "client_info": 0,
            },
        )

    def test_rollback_after_client_info_removes_every_step(self):
        with self.assertRaisesRegex(RuntimeError, "after client info"):
            self.apply_flow(fail_after="client_info")
        self.assertEqual(
            self.counts(),
            {
                "status": "DUE",
                "confirmations": 0,
                "warehouse_ledger": 0,
                "client_info": 0,
            },
        )

    def test_exact_replay_in_new_transaction_is_idempotent(self):
        self.apply_flow()
        self.apply_flow()
        self.assertEqual(
            self.counts(),
            {
                "status": "PAID",
                "confirmations": 1,
                "warehouse_ledger": 1,
                "client_info": 1,
            },
        )
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_conflicting_confirmation_rolls_back_status_change(self):
        self.apply_flow()
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE internal_payment_state SET status='DUE' WHERE order_id='I-ATOMIC'"
            )
        c = sqlite3.connect(self.db)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE internal_payment_state SET status='PAID' WHERE order_id='I-ATOMIC'"
            )
            with self.assertRaisesRegex(ValueError, "confirmation conflict"):
                wb.confirm_direct_payment_and_accrue(
                    self.db,
                    confirmation_id="pay-atomic",
                    warehouse_id=1,
                    order_id="I-ATOMIC",
                    customer_amount_rub=20000,
                    partner_amount_rub=16575,
                    actual_actor="warehouse:user1",
                    effective_subject="warehouse:1",
                    conn=c,
                )
            c.rollback()
        finally:
            c.close()
        state = self.counts()
        self.assertEqual(state["status"], "DUE")
        self.assertEqual(state["confirmations"], 1)
        self.assertEqual(state["warehouse_ledger"], 1)
        self.assertEqual(state["client_info"], 1)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_client_info_conflict_rolls_back_new_warehouse_work(self):
        # Pre-create a conflicting informational reference for another amount.
        info.record_direct_payment(
            self.db,
            client_id="c1",
            order_id="I-ATOMIC",
            warehouse_id=1,
            external_amount_rub=18000,
            reference_id="direct-payment-I-ATOMIC",
            actual_actor="warehouse:user1",
            effective_subject="warehouse:1",
            description="Direct partner payment",
        )
        c = sqlite3.connect(self.db)
        try:
            c.execute("BEGIN IMMEDIATE")
            c.execute(
                "UPDATE internal_payment_state SET status='PAID' WHERE order_id='I-ATOMIC'"
            )
            wb.confirm_direct_payment_and_accrue(
                self.db,
                confirmation_id="pay-atomic",
                warehouse_id=1,
                order_id="I-ATOMIC",
                customer_amount_rub=19500,
                partner_amount_rub=16575,
                actual_actor="warehouse:user1",
                effective_subject="warehouse:1",
                conn=c,
            )
            with self.assertRaisesRegex(ValueError, "informational idempotency conflict"):
                info.record_direct_payment(
                    self.db,
                    client_id="c1",
                    order_id="I-ATOMIC",
                    warehouse_id=1,
                    external_amount_rub=19500,
                    reference_id="direct-payment-I-ATOMIC",
                    actual_actor="warehouse:user1",
                    effective_subject="warehouse:1",
                    description="Direct partner payment",
                    conn=c,
                )
            c.rollback()
        finally:
            c.close()

        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute(
                    "SELECT status FROM internal_payment_state WHERE order_id='I-ATOMIC'"
                ).fetchone()[0],
                "DUE",
            )
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM warehouse_direct_payment_confirmations"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM warehouse_internal_balance_ledger"
                ).fetchone()[0],
                0,
            )
            # Only the pre-existing informational event remains.
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM finance_ledger "
                    "WHERE event_type='INFO_DIRECT_WAREHOUSE_PAYMENT'"
                ).fetchone()[0],
                1,
            )


if __name__ == "__main__":
    unittest.main()
