#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import tempfile
import unittest
from pathlib import Path

import fulfillment_slices as fs
import stock_engine
import supplier_channels
import supplier_customer_notify
import supplier_delivery
import supplier_shortages
import warehouse_store
from supplier_order_service import SupplierOrderService


class SupplierConfirmAssemblyPoint6Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp(prefix="supplier_point6_"))
        self.db = self.tmp_path / "orders.db"

        warehouse_store.init_warehouse_db(self.db)
        self.wid = warehouse_store.add_warehouse(
            "WH1", "Moscow", "W1", "Склад 1",
            priority=10, db_file=self.db,
        )
        warehouse_store.ensure_source(
            self.wid, "website", enabled=True, priority=10,
            ttl_minutes=60, db_file=self.db,
        )
        warehouse_store.record_source_stock(
            self.wid, "OEM-1", "in_stock", 10, "website",
            price_rub=1000, db_file=self.db,
        )

        with sqlite3.connect(self.db) as c:
            c.executescript("""
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,
              status TEXT,
              telegram_user_id INTEGER,
              created_at TEXT,
              updated_at TEXT
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY,
              order_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              manufacturer TEXT,
              oem TEXT NOT NULL,
              name TEXT,
              quantity REAL NOT NULL,
              offer_source TEXT,
              warehouse_id INTEGER
            );
            """)
            c.execute(
                "INSERT INTO orders VALUES('ORD-1','confirmed',777,'now','now')"
            )
            c.execute(
                """INSERT INTO order_items
                   VALUES(1,'ORD-1',1,'BRP','OEM-1','Part',2,'warehouse',?)""",
                (self.wid,),
            )

        self.service = SupplierOrderService(self.db)
        order = self.service.create_from_client_order(
            "ORD-1",
            {
                "name":"Client","phone":"+70000000000",
                "city":"Moscow","method":"CDEK","address":"X",
            },
        )[0]
        self.sid = int(order["id"])
        self.supplier_item_id = int(order["items"][0]["id"])
        supplier_channels.save(
            self.db, self.wid, "telegram", "test-destination"
        )

        self._old_notify_shortage = supplier_customer_notify.notify_shortage
        supplier_customer_notify.notify_shortage = (
            lambda service, shortage, public_name:
            "client-shortage-notified"
        )

    def tearDown(self):
        supplier_customer_notify.notify_shortage = self._old_notify_shortage

    def reserve_and_send(self):
        r = stock_engine.reserve_stock(
            self.wid, "OEM-1", 2,
            order_id="ORD-1", order_item_id=1,
            manufacturer="BRP", db_file=self.db,
        )
        self.assertTrue(r["ok"])
        result = supplier_delivery.send_one(
            self.service,
            self.sid,
            {"telegram": lambda destination, text: "telegram:accepted"},
        )
        self.assertEqual(result["status"], "sent")
        return r

    def order_status(self):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                "SELECT status FROM supplier_orders WHERE id=?",
                (self.sid,),
            ).fetchone()[0]

    def reservation(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                """SELECT *
                   FROM warehouse_stock_reservations
                   WHERE order_item_id=1
                   ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            return dict(row) if row else None

    def slices(self):
        return fs.list_for_supplier_order(self.db, self.sid)

    def events(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            return [
                dict(x) for x in c.execute(
                    """SELECT *
                       FROM fulfillment_events
                       ORDER BY id"""
                )
            ]

    def test_root_slice_is_created_before_send(self):
        rows = self.slices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["route"], fs.ROUTE_RF)
        self.assertEqual(
            rows[0]["status"], fs.RF_PENDING_RESERVATION
        )
        self.assertEqual(rows[0]["quantity"], 2)

    def test_send_moves_slice_to_rf_sent(self):
        self.reserve_and_send()
        rows = self.slices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], fs.RF_SENT_TO_WAREHOUSE)
        self.assertEqual(self.reservation()["status"], "committed")

    def test_full_confirmation_and_assembly(self):
        self.reserve_and_send()

        confirmed = self.service.change_status(
            self.sid,
            "confirmed",
            "telegram_admin",
            actor_type="admin",
            actor_id=42,
            source="telegram_admin",
        )
        self.assertEqual(confirmed["status"], "confirmed")
        rows = self.slices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(
            rows[0]["status"], fs.RF_WAREHOUSE_CONFIRMED
        )
        self.assertEqual(self.reservation()["status"], "committed")
        self.assertEqual(self.reservation()["quantity"], 2)

        assembled = self.service.change_status(
            self.sid,
            "assembled",
            "telegram_admin",
            actor_type="admin",
            actor_id=42,
            source="telegram_admin",
        )
        self.assertEqual(assembled["status"], "assembled")
        rows = self.slices()
        self.assertEqual(rows[0]["status"], fs.RF_ASSEMBLED)

        status_events = [
            x for x in self.events()
            if x["event_type"] in {
                "warehouse_confirmed","warehouse_assembled"
            }
        ]
        self.assertEqual(len(status_events), 2)
        self.assertTrue(
            all(x["actor_type"]=="admin" for x in status_events)
        )
        self.assertTrue(
            all(x["actor_id"]=="42" for x in status_events)
        )
        self.assertTrue(
            all(x["source"]=="telegram_admin" for x in status_events)
        )

    def test_confirm_before_send_is_blocked(self):
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.service.change_status(self.sid, "confirmed")
        self.assertEqual(self.order_status(), "not_sent")
        self.assertEqual(
            self.slices()[0]["status"],
            fs.RF_PENDING_RESERVATION,
        )

    def test_assembly_before_confirmation_is_blocked(self):
        self.reserve_and_send()
        with self.assertRaisesRegex(ValueError, "not ready"):
            self.service.change_status(self.sid, "assembled")
        self.assertEqual(self.order_status(), "sent")
        self.assertEqual(
            self.slices()[0]["status"],
            fs.RF_SENT_TO_WAREHOUSE,
        )

    def test_partial_confirmation_splits_slice_and_reduces_reservation(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid,
            self.supplier_item_id,
            1,
            actor_type="admin",
            actor_id=42,
            source="telegram_admin",
        )
        self.assertEqual(shortage["ordered_qty"], 2)
        self.assertEqual(shortage["confirmed_qty"], 1)
        self.assertEqual(shortage["shortage_qty"], 1)
        self.assertEqual(self.order_status(), "sent")

        rows = self.slices()
        self.assertEqual(len(rows), 2)
        self.assertAlmostEqual(
            sum(float(x["quantity"]) for x in rows), 2
        )

        root = next(x for x in rows if x["parent_slice_id"] is None)
        missing = next(x for x in rows if x["parent_slice_id"] is not None)
        self.assertEqual(root["quantity"], 1)
        self.assertEqual(
            root["status"], fs.RF_WAREHOUSE_CONFIRMED
        )
        self.assertIsNone(root["issue"])
        self.assertEqual(missing["quantity"], 1)
        self.assertEqual(
            missing["status"], fs.RF_SENT_TO_WAREHOUSE
        )
        self.assertEqual(missing["issue"], fs.ISSUE_SHORTAGE)

        reservation = self.reservation()
        self.assertEqual(reservation["status"], "committed")
        self.assertEqual(reservation["quantity"], 1)

        available = stock_engine.get_available_stock(
            self.wid, "OEM-1", self.db
        )
        self.assertEqual(available["blocked_quantity"], 1)
        self.assertEqual(available["available_quantity"], 9)

    def test_unresolved_shortage_blocks_confirm_and_assembly(self):
        self.reserve_and_send()
        self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        with self.assertRaisesRegex(ValueError, "недопоставка"):
            self.service.change_status(self.sid, "confirmed")
        self.assertEqual(self.order_status(), "sent")

    def test_partial_refund_resolves_missing_slice_then_allows_assembly(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        decided = self.service.decide_shortage(
            shortage["id"],
            "partial_refund",
            actor_type="client",
            actor_id=777,
            source="telegram_client",
        )
        self.assertEqual(
            decided["status"], "resolved_partial_refund"
        )

        rows = self.slices()
        root = next(x for x in rows if x["parent_slice_id"] is None)
        missing = next(x for x in rows if x["parent_slice_id"] is not None)
        self.assertEqual(
            root["status"], fs.RF_WAREHOUSE_CONFIRMED
        )
        self.assertEqual(missing["status"], fs.RF_CANCELLED)
        self.assertIsNone(missing["issue"])
        self.assertEqual(self.reservation()["quantity"], 1)

        self.service.change_status(self.sid, "confirmed")
        self.service.change_status(self.sid, "assembled")
        rows = self.slices()
        root = next(x for x in rows if x["parent_slice_id"] is None)
        missing = next(x for x in rows if x["parent_slice_id"] is not None)
        self.assertEqual(root["status"], fs.RF_ASSEMBLED)
        self.assertEqual(missing["status"], fs.RF_CANCELLED)

        client_events = [
            x for x in self.events()
            if x["event_type"]=="shortage_refunded"
        ]
        self.assertEqual(len(client_events), 1)
        self.assertEqual(client_events[0]["actor_type"], "client")
        self.assertEqual(client_events[0]["actor_id"], "777")

    def test_cancel_all_releases_remaining_reservation_and_closes_supplier(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        self.service.decide_shortage(
            shortage["id"],
            "cancel_all",
            actor_type="client",
            actor_id=777,
            source="telegram_client",
        )
        self.assertEqual(self.order_status(), "cancelled")
        self.assertEqual(self.reservation()["status"], "released")
        self.assertTrue(
            all(x["status"]==fs.RF_CANCELLED for x in self.slices())
        )

    def test_zero_confirmation_releases_reservation_without_zero_qty_slice(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 0
        )
        self.assertEqual(shortage["confirmed_qty"], 0)
        rows = self.slices()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["quantity"], 2)
        self.assertEqual(rows[0]["issue"], fs.ISSUE_SHORTAGE)
        self.assertEqual(
            rows[0]["status"], fs.RF_SENT_TO_WAREHOUSE
        )
        self.assertEqual(self.reservation()["status"], "released")

        self.service.decide_shortage(
            shortage["id"], "partial_refund"
        )
        self.assertEqual(self.order_status(), "cancelled")
        self.assertEqual(
            self.slices()[0]["status"], fs.RF_CANCELLED
        )

    def test_zero_confirmation_cancel_all_is_idempotent_for_released_reservation(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 0
        )
        self.assertEqual(self.reservation()["status"], "released")
        self.service.decide_shortage(
            shortage["id"], "cancel_all"
        )
        self.assertEqual(self.order_status(), "cancelled")
        self.assertEqual(self.reservation()["status"], "released")

    def test_same_shortage_report_is_idempotent(self):
        self.reserve_and_send()
        first = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        slices_before = self.slices()
        events_before = self.events()
        second = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.slices()), len(slices_before))
        self.assertEqual(len(self.events()), len(events_before))
        self.assertEqual(self.reservation()["quantity"], 1)

    def test_different_second_shortage_quantity_is_blocked(self):
        self.reserve_and_send()
        self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        with self.assertRaisesRegex(ValueError, "another"):
            self.service.report_item_problem(
                self.sid, self.supplier_item_id, 0
            )

    def test_confirmation_is_atomic_with_fulfillment_history(self):
        self.reserve_and_send()
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_confirm_event
              BEFORE INSERT ON fulfillment_events
              WHEN NEW.event_type='warehouse_confirmed'
              BEGIN SELECT RAISE(ABORT,'history fail'); END;""")

        with self.assertRaises(sqlite3.DatabaseError):
            self.service.change_status(self.sid, "confirmed")
        self.assertEqual(self.order_status(), "sent")
        self.assertEqual(
            self.slices()[0]["status"],
            fs.RF_SENT_TO_WAREHOUSE,
        )

    def test_assembly_is_atomic_with_fulfillment_history(self):
        self.reserve_and_send()
        self.service.change_status(self.sid, "confirmed")
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_assemble_event
              BEFORE INSERT ON fulfillment_events
              WHEN NEW.event_type='warehouse_assembled'
              BEGIN SELECT RAISE(ABORT,'history fail'); END;""")

        with self.assertRaises(sqlite3.DatabaseError):
            self.service.change_status(self.sid, "assembled")
        self.assertEqual(self.order_status(), "confirmed")
        self.assertEqual(
            self.slices()[0]["status"],
            fs.RF_WAREHOUSE_CONFIRMED,
        )

    def test_usa_quote_accept_reroutes_only_missing_slice(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        self.service.decide_shortage(
            shortage["id"], "usa_quote"
        )
        supplier_shortages.set_usa_quote(
            self.db,
            shortage["id"],
            status="quoted",
            unit_price_rub=1000,
            total_price_rub=1000,
            dp_usd=10,
            source="test",
        )
        decided = self.service.decide_usa_shortage_quote(
            shortage["id"],
            True,
            actor_type="client",
            actor_id=777,
            source="telegram_client",
        )
        self.assertEqual(decided["status"], "resolved_usa")

        rows = self.slices()
        rf = [x for x in rows if x["route"]==fs.ROUTE_RF]
        usa = [x for x in rows if x["route"]==fs.ROUTE_USA]
        self.assertEqual(len(rf), 1)
        self.assertEqual(len(usa), 1)
        self.assertEqual(rf[0]["quantity"], 1)
        self.assertEqual(
            rf[0]["status"], fs.RF_WAREHOUSE_CONFIRMED
        )
        self.assertEqual(usa[0]["quantity"], 1)
        self.assertEqual(usa[0]["status"], fs.USA_ORDERED)
        self.assertIsNone(usa[0]["issue"])

        self.service.change_status(self.sid, "confirmed")
        self.service.change_status(self.sid, "assembled")
        rows = self.slices()
        rf = [x for x in rows if x["route"]==fs.ROUTE_RF]
        usa = [x for x in rows if x["route"]==fs.ROUTE_USA]
        self.assertEqual(rf[0]["status"], fs.RF_ASSEMBLED)
        self.assertEqual(usa[0]["status"], fs.USA_ORDERED)

    def test_repeated_customer_shortage_decision_is_idempotent(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        first = self.service.decide_shortage(
            shortage["id"],
            "partial_refund",
            actor_type="client",
            actor_id=777,
            source="telegram_client",
        )
        events_before = len(self.events())
        with sqlite3.connect(self.db) as c:
            obligations_before = c.execute(
                """SELECT COUNT(*) FROM supplier_financial_obligations
                   WHERE shortage_id=?""",
                (shortage["id"],),
            ).fetchone()[0]

        second = self.service.decide_shortage(
            shortage["id"],
            "partial_refund",
            actor_type="client",
            actor_id=777,
            source="telegram_client",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.events()), events_before)
        with sqlite3.connect(self.db) as c:
            obligations_after = c.execute(
                """SELECT COUNT(*) FROM supplier_financial_obligations
                   WHERE shortage_id=?""",
                (shortage["id"],),
            ).fetchone()[0]
        self.assertEqual(obligations_before, 1)
        self.assertEqual(obligations_after, 1)

    def test_repeated_usa_quote_decision_is_idempotent(self):
        self.reserve_and_send()
        shortage = self.service.report_item_problem(
            self.sid, self.supplier_item_id, 1
        )
        self.service.decide_shortage(
            shortage["id"], "usa_quote"
        )
        supplier_shortages.set_usa_quote(
            self.db,
            shortage["id"],
            status="quoted",
            unit_price_rub=1000,
            total_price_rub=1000,
            dp_usd=10,
            source="test",
        )
        first = self.service.decide_usa_shortage_quote(
            shortage["id"], True,
            actor_type="client", actor_id=777,
            source="telegram_client",
        )
        events_before = len(self.events())
        second = self.service.decide_usa_shortage_quote(
            shortage["id"], True,
            actor_type="client", actor_id=777,
            source="telegram_client",
        )
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.events()), events_before)
        usa = [
            x for x in self.slices()
            if x["route"]==fs.ROUTE_USA
        ]
        self.assertEqual(len(usa), 1)

    def test_fulfillment_get_payload_exposes_slices(self):
        self.reserve_and_send()
        payload = self.service.get(self.sid)
        self.assertIn("fulfillment_slices", payload)
        self.assertEqual(
            payload["fulfillment_slices"][0]["status"],
            fs.RF_SENT_TO_WAREHOUSE,
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
