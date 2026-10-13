"""Internal Order Core v0.1 isolated P2 regressions."""
from pathlib import Path
import sqlite3
import tempfile
import unittest

from common_finance_contract import PaymentRoute
from internal_order_core_v01 import (
    AdminActorContext,
    WarehouseActorContext,
    confirm_internal_direct_payment,
    create_payment_snapshot,
    get_payment_snapshot,
    init,
    prepare_admin_extremizer_exception,
)
import warehouse_balance_v14 as wb


def create_shared_schema(db):
    with sqlite3.connect(db) as c:
        c.executescript(
            """
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              telegram_user_id INTEGER NOT NULL
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              offer_source TEXT NOT NULL,
              warehouse_id INTEGER,
              quantity INTEGER NOT NULL,
              customer_unit_rub INTEGER,
              payment_route TEXT NOT NULL
            );
            """
        )


class InternalOrderCoreV01Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "p2.db")
        create_shared_schema(self.db)
        init(self.db)

    def add_internal_order(
        self,
        order_id="I-P2-1",
        *,
        warehouse_id=1,
        prices=(10000, 9500),
        route="direct_partner",
        status="assembled_unpaid",
    ):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (order_id, status, "2026-10-13T20:00:00+02:00", 7001),
            )
            for pos, price in enumerate(prices, 1):
                c.execute(
                    """INSERT INTO order_items(
                         order_id,position,offer_source,warehouse_id,
                         quantity,customer_unit_rub,payment_route)
                       VALUES(?,?,'warehouse',?,1,?,?)""",
                    (order_id, pos, warehouse_id, price, route),
                )

    def add_usa_order(self, order_id="E-P2-USA"):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (order_id, "new", "2026-10-13T20:00:00+02:00", 7001),
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,offer_source,warehouse_id,
                     quantity,customer_unit_rub,payment_route)
                   VALUES(?,1,'usa',NULL,1,5000,'extremizer_balance')""",
                (order_id,),
            )

    def snapshot(self, order_id="I-P2-1", *, p=16575, warehouse=1):
        return create_payment_snapshot(
            self.db,
            order_id=order_id,
            client_id="client:1001",
            partner_amount_rub=p,
            created_by="internal-terms:warehouse-1",
            warehouse_order_number="5234",
            expected_warehouse_id=warehouse,
        )

    def actor(self, warehouse=1, authorized=True):
        return WarehouseActorContext(
            actual_actor=f"warehouse:{warehouse}:user-7",
            effective_subject=f"warehouse:{warehouse}",
            warehouse_id=warehouse,
            authorized=authorized,
        )

    def table_counts(self, order_id):
        with sqlite3.connect(self.db) as c:
            state = c.execute(
                """SELECT payment_status FROM internal_order_payment_state_v01
                   WHERE order_id=?""",
                (order_id,),
            ).fetchone()
            return {
                "payment_status": state[0] if state else None,
                "confirmations": c.execute(
                    """SELECT COUNT(*) FROM warehouse_direct_payment_confirmations
                       WHERE order_id=?""",
                    (order_id,),
                ).fetchone()[0],
                "warehouse_ledger": c.execute(
                    """SELECT COUNT(*) FROM warehouse_internal_balance_ledger
                       WHERE order_id=?""",
                    (order_id,),
                ).fetchone()[0],
                "client_info": c.execute(
                    """SELECT COUNT(*) FROM finance_ledger
                       WHERE order_id=? AND event_type='INFO_DIRECT_WAREHOUSE_PAYMENT'""",
                    (order_id,),
                ).fetchone()[0],
                "audit": c.execute(
                    """SELECT COUNT(*) FROM internal_order_audit_v01
                       WHERE order_id=? AND action='CONFIRM_INTERNAL_DIRECT_PAYMENT'""",
                    (order_id,),
                ).fetchone()[0],
            }

    def confirm(self, order_id="I-P2-1", confirmation_id="confirm-1", **kwargs):
        return confirm_internal_direct_payment(
            self.db,
            order_id=order_id,
            confirmation_id=confirmation_id,
            actor_context=kwargs.pop("actor_context", self.actor()),
            payment_confirmed=kwargs.pop("payment_confirmed", True),
            confirmed_amount_rub=kwargs.pop("confirmed_amount_rub", 19500),
            **kwargs,
        )

    def test_authoritative_snapshot_derives_c_and_route_from_shared_order(self):
        self.add_internal_order()
        snap = self.snapshot()
        self.assertEqual(snap["order_id"], "I-P2-1")
        self.assertEqual(snap["warehouse_id"], 1)
        self.assertEqual(snap["warehouse_order_number"], "5234")
        self.assertEqual(snap["client_id"], "client:1001")
        self.assertEqual(snap["customer_amount_rub"], 19500)
        self.assertEqual(snap["partner_amount_rub"], 16575)
        self.assertEqual(snap["payment_route"], "direct_partner")
        self.assertEqual(snap["order_status_snapshot"], "assembled_unpaid")

    def test_snapshot_is_immutable_and_conflicting_p_is_rejected(self):
        self.add_internal_order()
        original = self.snapshot()
        replay = self.snapshot()
        self.assertEqual(original, replay)
        with self.assertRaisesRegex(ValueError, "snapshot conflict"):
            create_payment_snapshot(
                self.db,
                order_id="I-P2-1",
                client_id="client:1001",
                partner_amount_rub=16000,
                created_by="internal-terms:warehouse-1",
                warehouse_order_number="5234",
                expected_warehouse_id=1,
            )
        self.assertEqual(get_payment_snapshot(self.db, "I-P2-1"), original)

    def test_full_direct_payment_one_commit(self):
        self.add_internal_order()
        self.snapshot()
        result = self.confirm()
        self.assertEqual(result["payment_status"], "PAID")
        self.assertEqual(result["customer_amount_rub"], 19500)
        self.assertEqual(result["partner_amount_rub"], 16575)
        self.assertEqual(result["difference_rub"], 2925)
        self.assertEqual(
            self.table_counts("I-P2-1"),
            {
                "payment_status": "PAID",
                "confirmations": 1,
                "warehouse_ledger": 1,
                "client_info": 1,
                "audit": 1,
            },
        )
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_exact_replay_creates_no_second_financial_or_audit_event(self):
        self.add_internal_order()
        self.snapshot()
        first = self.confirm()
        second = self.confirm()
        self.assertEqual(first, second)
        self.assertEqual(
            self.table_counts("I-P2-1"),
            {
                "payment_status": "PAID",
                "confirmations": 1,
                "warehouse_ledger": 1,
                "client_info": 1,
                "audit": 1,
            },
        )
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_conflicting_replay_rejected_and_history_unchanged(self):
        self.add_internal_order()
        self.snapshot()
        self.confirm()
        before = self.table_counts("I-P2-1")
        with self.assertRaisesRegex(ValueError, "confirmation conflict"):
            self.confirm(
                actor_context=WarehouseActorContext(
                    actual_actor="warehouse:1:user-8",
                    effective_subject="warehouse:1",
                    warehouse_id=1,
                    authorized=True,
                )
            )
        self.assertEqual(self.table_counts("I-P2-1"), before)
        self.assertEqual(wb.balance_summary(self.db, 1)["available_rub"], 2925)

    def test_cross_warehouse_actor_is_denied_before_finance(self):
        self.add_internal_order()
        self.snapshot()
        with self.assertRaisesRegex(PermissionError, "CROSS_WAREHOUSE"):
            self.confirm(actor_context=self.actor(warehouse=2))
        self.assertEqual(
            self.table_counts("I-P2-1"),
            {
                "payment_status": "DUE",
                "confirmations": 0,
                "warehouse_ledger": 0,
                "client_info": 0,
                "audit": 0,
            },
        )

    def test_unauthorized_actor_context_is_denied(self):
        self.add_internal_order()
        self.snapshot()
        with self.assertRaisesRegex(PermissionError, "NOT_AUTHORIZED"):
            self.confirm(actor_context=self.actor(authorized=False))
        self.assertEqual(self.table_counts("I-P2-1")["payment_status"], "DUE")

    def test_partial_or_wrong_amount_is_fail_closed(self):
        self.add_internal_order()
        self.snapshot()
        with self.assertRaisesRegex(ValueError, "FULL_PAYMENT_CONFIRMATION_REQUIRED"):
            self.confirm(payment_confirmed=False)
        with self.assertRaisesRegex(ValueError, "FULL_PAYMENT_AMOUNT_MISMATCH"):
            self.confirm(confirmed_amount_rub=19000)
        self.assertEqual(self.table_counts("I-P2-1")["payment_status"], "DUE")

    def test_order_without_p_snapshot_fails_closed(self):
        self.add_internal_order()
        with self.assertRaisesRegex(ValueError, "INTERNAL_PAYMENT_SNAPSHOT_REQUIRED"):
            self.confirm()
        with sqlite3.connect(self.db) as c:
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM warehouse_internal_balance_ledger"
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                c.execute(
                    "SELECT COUNT(*) FROM finance_ledger"
                ).fetchone()[0],
                0,
            )

    def test_changed_shared_order_status_invalidates_payment_snapshot(self):
        self.add_internal_order()
        self.snapshot()
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE orders SET status='cancelled' WHERE order_id='I-P2-1'"
            )
        with self.assertRaisesRegex(ValueError, "STATUS_CHANGED"):
            self.confirm()
        self.assertEqual(self.table_counts("I-P2-1")["payment_status"], "DUE")

    def test_rollback_after_every_internal_step(self):
        for step in ("payment_state", "warehouse_accrual", "client_info", "audit"):
            with self.subTest(step=step):
                order_id = f"I-ROLL-{step}"
                self.add_internal_order(order_id=order_id)
                self.snapshot(order_id=order_id)

                def fail(current):
                    if current == step:
                        raise RuntimeError(f"injected:{step}")

                with self.assertRaisesRegex(RuntimeError, f"injected:{step}"):
                    self.confirm(
                        order_id=order_id,
                        confirmation_id=f"confirm-{step}",
                        _failure_hook=fail,
                    )
                self.assertEqual(
                    self.table_counts(order_id),
                    {
                        "payment_status": "DUE",
                        "confirmations": 0,
                        "warehouse_ledger": 0,
                        "client_info": 0,
                        "audit": 0,
                    },
                )

    def test_usa_order_is_rejected_by_internal_snapshot_and_unchanged(self):
        self.add_usa_order()
        with self.assertRaisesRegex(ValueError, "ONLY_WAREHOUSE_LINES"):
            create_payment_snapshot(
                self.db,
                order_id="E-P2-USA",
                client_id="client:1001",
                partner_amount_rub=4000,
                created_by="test",
            )
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                """SELECT offer_source,payment_route,customer_unit_rub
                   FROM order_items WHERE order_id='E-P2-USA'"""
            ).fetchone()
        self.assertEqual(row, ("usa", "extremizer_balance", 5000))

    def test_existing_explicit_internal_snapshot_route_is_not_rewritten(self):
        self.add_internal_order(route="extremizer_balance")
        snap = self.snapshot()
        self.assertEqual(snap["payment_route"], "extremizer_balance")
        with self.assertRaisesRegex(ValueError, "REQUIRES_DIRECT_PARTNER"):
            self.confirm()
        self.assertEqual(
            get_payment_snapshot(self.db, "I-P2-1")["payment_route"],
            "extremizer_balance",
        )

    def test_admin_exception_requires_full_p_balance(self):
        self.add_internal_order()
        self.snapshot()

        # Only 10,000 available; P is 16,575.
        wb.confirm_direct_payment_and_accrue(
            self.db,
            confirmation_id="seed-margin",
            warehouse_id=1,
            order_id="I-SEED",
            customer_amount_rub=20000,
            partner_amount_rub=10000,
            actual_actor="warehouse:1:seed",
            effective_subject="warehouse:1",
        )
        with self.assertRaisesRegex(ValueError, "INSUFFICIENT_WAREHOUSE_BALANCE"):
            prepare_admin_extremizer_exception(
                self.db,
                order_id="I-P2-1",
                reservation_id="admin-reserve-1",
                admin_context=AdminActorContext(
                    actual_actor="admin:1",
                    effective_subject="admin:1",
                    authorized=True,
                ),
            )
        self.assertEqual(wb.balance_summary(self.db, 1)["reserved_rub"], 0)
        self.assertEqual(
            get_payment_snapshot(self.db, "I-P2-1")["payment_route"],
            PaymentRoute.DIRECT_PARTNER.value,
        )

    def test_admin_exception_reserves_full_p_but_does_not_rewrite_route(self):
        self.add_internal_order()
        self.snapshot()
        wb.confirm_direct_payment_and_accrue(
            self.db,
            confirmation_id="seed-full",
            warehouse_id=1,
            order_id="I-SEED-FULL",
            customer_amount_rub=30000,
            partner_amount_rub=10000,
            actual_actor="warehouse:1:seed",
            effective_subject="warehouse:1",
        )
        result = prepare_admin_extremizer_exception(
            self.db,
            order_id="I-P2-1",
            reservation_id="admin-reserve-full",
            admin_context=AdminActorContext(
                actual_actor="admin:1",
                effective_subject="admin:1",
                authorized=True,
            ),
        )
        self.assertEqual(result["partner_amount_rub"], 16575)
        self.assertEqual(result["reservation_status"], "reserved")
        self.assertEqual(result["proposed_payment_route"], "extremizer_balance")
        self.assertEqual(result["persisted_payment_route"], "direct_partner")
        summary = wb.balance_summary(self.db, 1)
        self.assertEqual(summary["reserved_rub"], 16575)
        self.assertEqual(summary["available_rub"], 3425)

    def test_admin_exception_requires_authorized_admin(self):
        self.add_internal_order()
        self.snapshot()
        with self.assertRaisesRegex(PermissionError, "ADMIN_NOT_AUTHORIZED"):
            prepare_admin_extremizer_exception(
                self.db,
                order_id="I-P2-1",
                reservation_id="no-admin",
                admin_context=AdminActorContext(
                    actual_actor="admin:1",
                    effective_subject="admin:1",
                    authorized=False,
                ),
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
