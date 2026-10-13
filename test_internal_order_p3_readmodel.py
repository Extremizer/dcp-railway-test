"""Internal Order Core P3 read-model / scoped-read isolated regressions."""
from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import sqlite3
import tempfile
import unittest

from internal_order_core_v01 import (
    WarehouseActorContext,
    confirm_internal_direct_payment,
    create_payment_snapshot,
    init,
)
from internal_order_p3_read_service import (
    READ_CONTRACT_VERSION,
    READ_WAREHOUSE_HISTORY,
    ROLE_ADMIN,
    ROLE_RF_WAREHOUSE,
    ScopedOrderMetadata,
    WarehouseReadAuthorizationContext,
    read_scoped_warehouse_history,
)
import warehouse_balance_v14 as wb
import warehouse_history_v14 as history


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


class InternalOrderP3ReadModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "p3.db")
        create_shared_schema(self.db)
        init(self.db)

    def add_order(
        self,
        order_id,
        *,
        warehouse_id=1,
        customer_amount=19500,
        route="direct_partner",
        status="assembled_unpaid",
        offer_source="warehouse",
        telegram_user_id=7001,
        date="2026-10-13T20:00:00+02:00",
    ):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (order_id, status, date, telegram_user_id),
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,offer_source,warehouse_id,
                     quantity,customer_unit_rub,payment_route)
                   VALUES(?,1,?,?,?,?,?)""",
                (
                    order_id,
                    offer_source,
                    warehouse_id if offer_source == "warehouse" else None,
                    1,
                    customer_amount,
                    route,
                ),
            )

    def snapshot(
        self,
        order_id,
        *,
        partner_amount=16575,
        client_id="client:1001",
        warehouse_id=1,
        warehouse_order_number="5234",
    ):
        return create_payment_snapshot(
            self.db,
            order_id=order_id,
            client_id=client_id,
            partner_amount_rub=partner_amount,
            created_by=f"internal-terms:warehouse-{warehouse_id}",
            warehouse_order_number=warehouse_order_number,
            expected_warehouse_id=warehouse_id,
        )

    def auth(
        self,
        warehouse_id=1,
        *,
        role=ROLE_RF_WAREHOUSE,
        authorized=True,
        permissions=(READ_WAREHOUSE_HISTORY,),
        acting_as=False,
    ):
        return WarehouseReadAuthorizationContext(
            actual_actor="admin:1" if role == ROLE_ADMIN else f"warehouse:{warehouse_id}:user-7",
            effective_subject=f"warehouse:{warehouse_id}",
            effective_role=role,
            warehouse_id=warehouse_id,
            permissions=permissions,
            authorized=authorized,
            acting_as=acting_as,
        )

    def meta(
        self,
        order_id,
        *,
        warehouse_id=1,
        client_id="client:1001",
        client_display="Дмитрий К.",
        oems=("417300574",),
        status="Собран, не оплачен",
    ):
        return ScopedOrderMetadata(
            order_id=order_id,
            warehouse_id=warehouse_id,
            client_id=client_id,
            client_display=client_display,
            oems=oems,
            status=status,
        )

    def read(self, metadata, **kwargs):
        return read_scoped_warehouse_history(
            self.db,
            requested_warehouse_id=kwargs.pop("warehouse_id", 1),
            auth_context=kwargs.pop("auth_context", self.auth()),
            order_metadata=metadata,
            **kwargs,
        )

    def confirm(self, order_id, confirmation_id="p3-confirm-1", **kwargs):
        return confirm_internal_direct_payment(
            self.db,
            order_id=order_id,
            confirmation_id=confirmation_id,
            actor_context=kwargs.pop(
                "actor_context",
                WarehouseActorContext(
                    actual_actor="warehouse:1:user-7",
                    effective_subject="warehouse:1",
                    warehouse_id=1,
                    authorized=True,
                ),
            ),
            payment_confirmed=True,
            confirmed_amount_rub=kwargs.pop("confirmed_amount_rub", 19500),
            **kwargs,
        )

    def test_due_snapshot_is_authoritative_terms_source(self):
        self.add_order("I-P3-DUE")
        self.snapshot("I-P3-DUE", partner_amount=16575, warehouse_order_number="5234")
        # A legacy-looking value must not influence P.
        with sqlite3.connect(self.db) as c:
            c.execute(
                """CREATE TABLE warehouse_partner_settlements(
                     order_id TEXT, warehouse_id INTEGER, partner_amount_rub INTEGER
                   )"""
            )
            c.execute(
                "INSERT INTO warehouse_partner_settlements VALUES('I-P3-DUE',1,999999)"
            )

        result = self.read({"I-P3-DUE": self.meta("I-P3-DUE")})
        self.assertEqual(result["contract_version"], READ_CONTRACT_VERSION)
        row = result["rows"][0]
        self.assertEqual(row["warehouse_id"], 1)
        self.assertEqual(row["extremizer_order_number"], "I-P3-DUE")
        self.assertEqual(row["warehouse_order_number"], "5234")
        self.assertEqual(row["client_id"], "client:1001")
        self.assertEqual(row["customer_amount_rub"], 19500)
        self.assertEqual(row["partner_amount_rub"], 16575)
        self.assertEqual(row["partner_amount_source"], "internal_payment_snapshot")
        self.assertEqual(row["payment_status"], "DUE")
        self.assertIsNone(row["balance_rub"])
        self.assertEqual(result["summary"]["posted_balance_rub"], 0)

    def test_paid_snapshot_keeps_snapshot_p_and_exposes_posted_effect(self):
        self.add_order("I-P3-PAID")
        self.snapshot("I-P3-PAID", partner_amount=16575)
        self.confirm("I-P3-PAID")
        result = self.read({"I-P3-PAID": self.meta("I-P3-PAID")})
        row = result["rows"][0]
        self.assertEqual(row["payment_status"], "PAID")
        self.assertEqual(row["partner_amount_rub"], 16575)
        self.assertEqual(row["partner_amount_source"], "internal_payment_snapshot")
        self.assertEqual(row["balance_rub"], 2925)
        self.assertEqual(result["summary"]["posted_balance_rub"], 2925)
        self.assertEqual(result["summary"]["available_balance_rub"], 2925)

    def test_missing_snapshot_p_stays_none_even_with_legacy_partner_value(self):
        self.add_order("I-P3-NO-P", customer_amount=12000)
        with sqlite3.connect(self.db) as c:
            c.execute(
                """CREATE TABLE warehouse_partner_settlements(
                     order_id TEXT, warehouse_id INTEGER, partner_amount_rub INTEGER
                   )"""
            )
            c.execute(
                "INSERT INTO warehouse_partner_settlements VALUES('I-P3-NO-P',1,7777)"
            )
        result = self.read(
            {"I-P3-NO-P": self.meta("I-P3-NO-P", client_id="client:2002")}
        )
        row = result["rows"][0]
        self.assertIsNone(row["partner_amount_rub"])
        self.assertIsNone(row["partner_amount_source"])
        self.assertIsNone(row["payment_status"])
        self.assertIsNone(row["balance_rub"])

    def test_snapshot_warehouse_mismatch_fails_closed(self):
        self.add_order("I-P3-BAD-WH", warehouse_id=1)
        self.snapshot("I-P3-BAD-WH", warehouse_id=1)
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE internal_order_payment_snapshots_v01
                   SET warehouse_id=2 WHERE order_id='I-P3-BAD-WH'"""
            )
        with self.assertRaisesRegex(ValueError, "WAREHOUSE_MISMATCH"):
            history.read_history(self.db, 1)

    def test_scoped_service_enforces_exact_warehouse_isolation(self):
        self.add_order("I-P3-W1", warehouse_id=1)
        self.snapshot("I-P3-W1", warehouse_id=1, warehouse_order_number="W1")
        self.add_order("I-P3-W2", warehouse_id=2, customer_amount=21000)
        self.snapshot(
            "I-P3-W2",
            warehouse_id=2,
            partner_amount=18000,
            client_id="client:2002",
            warehouse_order_number="W2",
        )
        meta1 = {"I-P3-W1": self.meta("I-P3-W1", warehouse_id=1)}
        result = self.read(meta1)
        self.assertEqual([r["extremizer_order_number"] for r in result["rows"]], ["I-P3-W1"])
        with self.assertRaisesRegex(PermissionError, "SCOPE_MISMATCH"):
            self.read(
                {"I-P3-W2": self.meta(
                    "I-P3-W2", warehouse_id=2, client_id="client:2002"
                )},
                warehouse_id=2,
                auth_context=self.auth(warehouse_id=1),
            )

    def test_admin_and_sklad_require_same_exact_read_capability_scope(self):
        self.add_order("I-P3-AUTH")
        self.snapshot("I-P3-AUTH")
        metadata = {"I-P3-AUTH": self.meta("I-P3-AUTH")}
        admin = self.auth(role=ROLE_ADMIN, warehouse_id=1, acting_as=False)
        sklad = self.auth(role=ROLE_RF_WAREHOUSE, warehouse_id=1)
        self.assertEqual(self.read(metadata, auth_context=admin)["scope"]["warehouse_id"], 1)
        self.assertEqual(self.read(metadata, auth_context=sklad)["scope"]["warehouse_id"], 1)
        with self.assertRaisesRegex(PermissionError, "CAPABILITY_REQUIRED"):
            self.read(metadata, auth_context=self.auth(permissions=()))
        with self.assertRaisesRegex(PermissionError, "NOT_AUTHORIZED"):
            self.read(metadata, auth_context=self.auth(authorized=False))

    def test_wrong_client_or_order_metadata_fails_closed(self):
        self.add_order("I-P3-LINK")
        self.snapshot("I-P3-LINK", client_id="client:1001")

        bad_client = {"I-P3-LINK": self.meta("I-P3-LINK", client_id="client:9999")}
        with self.assertRaisesRegex(ValueError, "CLIENT_LINK_MISMATCH"):
            self.read(bad_client)

        wrong_order_object = {
            "I-P3-LINK": self.meta("I-P3-OTHER", client_id="client:1001")
        }
        with self.assertRaisesRegex(ValueError, "KEY_MISMATCH"):
            self.read(wrong_order_object)

        with self.assertRaisesRegex(ValueError, "ORDER_METADATA_REQUIRED"):
            self.read({})

    def test_scoped_metadata_supplies_status_client_oem_and_search(self):
        self.add_order("I-P3-SEARCH")
        self.snapshot("I-P3-SEARCH")
        metadata = {
            "I-P3-SEARCH": self.meta(
                "I-P3-SEARCH",
                client_display="Иван Петров",
                oems=("705402449", "860200987"),
                status="Собран, не оплачен",
            )
        }
        for query in ("Иван", "705402449", "Собран", "5234", "I-P3-SEARCH"):
            with self.subTest(query=query):
                result = self.read(metadata, search=query)
                self.assertEqual(len(result["rows"]), 1)
        row = self.read(metadata)["rows"][0]
        self.assertEqual(row["client_display"], "Иван Петров")
        self.assertEqual(row["oems"], ("705402449", "860200987"))
        self.assertEqual(row["order_status"], "Собран, не оплачен")

    def test_read_service_is_database_read_only(self):
        self.add_order("I-P3-RO")
        self.snapshot("I-P3-RO")
        metadata = {"I-P3-RO": self.meta("I-P3-RO")}
        before = sha256(Path(self.db).read_bytes()).hexdigest()
        self.read(metadata)
        after = sha256(Path(self.db).read_bytes()).hexdigest()
        self.assertEqual(after, before)

    def test_failed_payment_rollback_keeps_due_snapshot_and_blank_balance(self):
        self.add_order("I-P3-ROLL")
        self.snapshot("I-P3-ROLL")

        def fail(step):
            if step == "warehouse_accrual":
                raise RuntimeError("injected:p3")

        with self.assertRaisesRegex(RuntimeError, "injected:p3"):
            self.confirm(
                "I-P3-ROLL",
                confirmation_id="p3-rollback",
                _failure_hook=fail,
            )

        result = self.read({"I-P3-ROLL": self.meta("I-P3-ROLL")})
        row = result["rows"][0]
        self.assertEqual(row["payment_status"], "DUE")
        self.assertEqual(row["partner_amount_rub"], 16575)
        self.assertEqual(row["partner_amount_source"], "internal_payment_snapshot")
        self.assertIsNone(row["balance_rub"])
        self.assertEqual(result["summary"]["posted_balance_rub"], 0)

    def test_control_3425_is_global_and_not_changed_by_pagination_or_search(self):
        metadata = {}
        differences = (2925, 1500, 3000, 4500)
        for idx, difference in enumerate(differences, 1):
            order_id = f"I-P3-C{idx}"
            partner = 10000
            self.add_order(order_id, customer_amount=partner + difference)
            wb.confirm_direct_payment_and_accrue(
                self.db,
                confirmation_id=f"legacy-compatible-{idx}",
                warehouse_id=1,
                order_id=order_id,
                customer_amount_rub=partner + difference,
                partner_amount_rub=partner,
                actual_actor="warehouse:1:legacy-compatible",
                effective_subject="warehouse:1",
            )
            metadata[order_id] = self.meta(
                order_id,
                client_id=f"client:{3000 + idx}",
                warehouse_id=1,
            )

        self.add_order("I-P3-SPEND", customer_amount=8500)
        wb.reserve_full_balance(
            self.db,
            reservation_id="p3-spend-8500",
            warehouse_id=1,
            order_id="I-P3-SPEND",
            required_amount_rub=8500,
            actual_actor="admin:1",
        )
        wb.apply_reserved_balance(
            self.db,
            reservation_id="p3-spend-8500",
            actual_actor="admin:1",
        )
        metadata["I-P3-SPEND"] = self.meta(
            "I-P3-SPEND", client_id="client:3999"
        )

        page = self.read(metadata, limit=1)
        self.assertEqual(len(page["rows"]), 1)
        self.assertEqual(page["summary"]["posted_balance_rub"], 3425)
        self.assertEqual(page["summary"]["available_balance_rub"], 3425)

        hidden = self.read(metadata, search="definitely-not-an-order", limit=1)
        self.assertEqual(hidden["rows"], [])
        self.assertEqual(hidden["summary"]["posted_balance_rub"], 3425)
        self.assertEqual(hidden["summary"]["available_balance_rub"], 3425)

    def test_usa_order_remains_outside_internal_warehouse_read_model(self):
        self.add_order(
            "E-P3-USA",
            offer_source="usa",
            warehouse_id=None,
            customer_amount=5000,
            route="extremizer_balance",
        )
        with self.assertRaisesRegex(ValueError, "ONLY_WAREHOUSE_LINES"):
            create_payment_snapshot(
                self.db,
                order_id="E-P3-USA",
                client_id="client:1001",
                partner_amount_rub=4000,
                created_by="p3-test",
            )
        result = self.read({})
        self.assertEqual(result["rows"], [])
        with sqlite3.connect(self.db) as c:
            row = c.execute(
                """SELECT offer_source,payment_route,customer_unit_rub
                   FROM order_items WHERE order_id='E-P3-USA'"""
            ).fetchone()
        self.assertEqual(row, ("usa", "extremizer_balance", 5000))


if __name__ == "__main__":
    unittest.main(verbosity=2)
