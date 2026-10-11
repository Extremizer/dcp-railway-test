#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import tempfile
import unittest
from pathlib import Path

import fulfillment_slices as fs
import stock_engine
import supplier_channels
import supplier_delivery
import supplier_orders
import supplier_shortages
import supplier_web_admin
import warehouse_store
from supplier_order_service import SupplierOrderService


class SupplierShippingPoint7Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_path=Path(tempfile.mkdtemp(prefix="supplier_point7_"))
        self.db=self.tmp_path/"orders.db"

        warehouse_store.init_warehouse_db(self.db)
        self.wid=warehouse_store.add_warehouse(
            "WH1","Moscow","W1","Склад 1",priority=10,db_file=self.db
        )
        warehouse_store.ensure_source(
            self.wid,"website",enabled=True,priority=10,
            ttl_minutes=60,db_file=self.db
        )
        warehouse_store.record_source_stock(
            self.wid,"OEM-1","in_stock",10,"website",
            price_rub=1000,db_file=self.db
        )
        with sqlite3.connect(self.db) as c:
            c.executescript("""
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,status TEXT,
              telegram_user_id INTEGER,created_at TEXT,updated_at TEXT
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY,order_id TEXT NOT NULL,
              position INTEGER NOT NULL,manufacturer TEXT,
              oem TEXT NOT NULL,name TEXT,quantity REAL NOT NULL,
              offer_source TEXT,warehouse_id INTEGER
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

        self.service=SupplierOrderService(self.db)
        order=self.service.create_from_client_order(
            "ORD-1",
            {
                "name":"Client","phone":"+70000000000",
                "city":"Moscow","method":"CDEK","address":"X",
            },
        )[0]
        self.sid=int(order["id"])
        self.supplier_item_id=int(order["items"][0]["id"])
        supplier_channels.save(
            self.db,self.wid,"telegram","test-destination"
        )

    def reserve_send_assemble(self):
        r=stock_engine.reserve_stock(
            self.wid,"OEM-1",2,
            order_id="ORD-1",order_item_id=1,
            manufacturer="BRP",db_file=self.db,
        )
        self.assertTrue(r["ok"])
        supplier_delivery.send_one(
            self.service,
            self.sid,
            {"telegram":lambda destination,text:"telegram:accepted"},
        )
        self.service.change_status(
            self.sid,"confirmed",
            actor_type="admin",actor_id=42,source="telegram_admin",
        )
        self.service.change_status(
            self.sid,"assembled",
            actor_type="admin",actor_id=42,source="telegram_admin",
        )

    def order_row(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            row=c.execute(
                """SELECT status,carrier,tracking_number,shipped_at
                   FROM supplier_orders WHERE id=?""",
                (self.sid,),
            ).fetchone()
            return dict(row)

    def slices(self):
        return fs.list_for_supplier_order(self.db,self.sid)

    def fulfillment_events(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [
                dict(x) for x in c.execute(
                    "SELECT * FROM fulfillment_events ORDER BY id"
                )
            ]

    def supplier_events(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [
                dict(x) for x in c.execute(
                    """SELECT * FROM supplier_order_events
                       WHERE supplier_order_id=? ORDER BY id""",
                    (self.sid,),
                )
            ]

    def test_tracking_required_and_only_after_assembled(self):
        with self.assertRaisesRegex(ValueError,"assembled"):
            self.service.add_tracking(
                self.sid,"CDEK","T1",client_sender=lambda *a:"x"
            )
        self.assertEqual(self.order_row()["status"],"not_sent")

        self.reserve_send_assemble()
        for carrier,tracking in (("","T1"),("CDEK","")):
            with self.assertRaisesRegex(ValueError,"required"):
                self.service.add_tracking(
                    self.sid,carrier,tracking,client_sender=lambda *a:"x"
                )
        row=self.order_row()
        self.assertEqual(row["status"],"assembled")
        self.assertIsNone(row["carrier"])
        self.assertIsNone(row["tracking_number"])

    def test_direct_shipped_status_bypass_is_blocked(self):
        self.reserve_send_assemble()
        with self.assertRaisesRegex(ValueError,"only through tracking"):
            self.service.change_status(self.sid,"shipped")
        self.assertEqual(self.order_row()["status"],"assembled")
        self.assertEqual(self.slices()[0]["status"],fs.RF_ASSEMBLED)

    def test_legacy_repo_tracking_bypass_is_blocked(self):
        self.reserve_send_assemble()
        with self.assertRaisesRegex(ValueError,"SupplierOrderService"):
            supplier_orders.set_tracking(
                self.db,self.sid,"CDEK","T1"
            )
        self.assertEqual(self.order_row()["status"],"assembled")

    def test_successful_tracking_is_atomic_and_notifies_after_commit(self):
        self.reserve_send_assemble()
        observed={}
        def sender(chat_id,message):
            row=self.order_row()
            observed.update(row)
            return "client-msg-1"

        result=self.service.add_tracking(
            self.sid,
            "CDEK",
            "TRACK-1",
            client_sender=sender,
            actor_type="admin",
            actor_id=42,
            source="telegram_admin",
        )
        self.assertEqual(result["status"],"shipped")
        row=self.order_row()
        self.assertEqual(row["carrier"],"CDEK")
        self.assertEqual(row["tracking_number"],"TRACK-1")
        self.assertTrue(row["shipped_at"])
        self.assertEqual(observed["status"],"shipped")
        self.assertEqual(observed["tracking_number"],"TRACK-1")
        self.assertTrue(observed["shipped_at"])

        rf=[x for x in self.slices() if x["route"]==fs.ROUTE_RF]
        self.assertEqual(len(rf),1)
        self.assertEqual(rf[0]["status"],fs.RF_SHIPPED_TO_CLIENT)

        events=[
            x for x in self.fulfillment_events()
            if x["event_type"]=="shipped_to_client"
        ]
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]["actor_type"],"admin")
        self.assertEqual(events[0]["actor_id"],"42")
        self.assertEqual(events[0]["source"],"telegram_admin")

        notify=[
            x for x in self.supplier_events()
            if x["event_type"]=="client_tracking_notified"
        ]
        self.assertEqual(len(notify),1)

    def test_same_tracking_retry_is_idempotent_and_no_duplicate_notification(self):
        self.reserve_send_assemble()
        calls=[]
        first=self.service.add_tracking(
            self.sid,"CDEK","TRACK-1",
            client_sender=lambda *a:calls.append(1) or "msg-1",
        )
        shipped_at=first["shipped_at"]
        f_events=len(self.fulfillment_events())
        s_events=len(self.supplier_events())

        second=self.service.add_tracking(
            self.sid,"CDEK","TRACK-1",
            client_sender=lambda *a:calls.append(2) or "msg-2",
        )
        self.assertEqual(calls,[1])
        self.assertEqual(second["shipped_at"],shipped_at)
        self.assertEqual(len(self.fulfillment_events()),f_events)
        self.assertEqual(len(self.supplier_events()),s_events)

    def test_different_tracking_after_shipment_is_blocked(self):
        self.reserve_send_assemble()
        self.service.add_tracking(
            self.sid,"CDEK","TRACK-1",client_sender=lambda *a:"x"
        )
        before=self.order_row()
        with self.assertRaisesRegex(ValueError,"another shipment tracking"):
            self.service.add_tracking(
                self.sid,"CDEK","TRACK-2",client_sender=lambda *a:"y"
            )
        self.assertEqual(self.order_row(),before)

    def test_fulfillment_history_failure_rolls_back_tracking_and_status(self):
        self.reserve_send_assemble()
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_ship_fulfillment_event
              BEFORE INSERT ON fulfillment_events
              WHEN NEW.event_type='shipped_to_client'
              BEGIN SELECT RAISE(ABORT,'fulfillment history fail'); END;""")

        with self.assertRaises(sqlite3.DatabaseError):
            self.service.add_tracking(
                self.sid,"CDEK","TRACK-1",client_sender=lambda *a:"x"
            )
        row=self.order_row()
        self.assertEqual(row["status"],"assembled")
        self.assertIsNone(row["carrier"])
        self.assertIsNone(row["tracking_number"])
        self.assertIsNone(row["shipped_at"])
        self.assertEqual(self.slices()[0]["status"],fs.RF_ASSEMBLED)

    def test_supplier_history_failure_rolls_back_tracking_and_slice(self):
        self.reserve_send_assemble()
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_ship_supplier_event
              BEFORE INSERT ON supplier_order_events
              WHEN NEW.event_type='status_changed' AND NEW.to_status='shipped'
              BEGIN SELECT RAISE(ABORT,'supplier history fail'); END;""")

        with self.assertRaises(sqlite3.DatabaseError):
            self.service.add_tracking(
                self.sid,"CDEK","TRACK-1",client_sender=lambda *a:"x"
            )
        row=self.order_row()
        self.assertEqual(row["status"],"assembled")
        self.assertIsNone(row["carrier"])
        self.assertIsNone(row["tracking_number"])
        self.assertIsNone(row["shipped_at"])
        self.assertEqual(self.slices()[0]["status"],fs.RF_ASSEMBLED)

    def test_client_notification_failure_does_not_rollback_shipment(self):
        self.reserve_send_assemble()
        def failed_sender(*args):
            raise RuntimeError("client telegram down")

        result=self.service.add_tracking(
            self.sid,"CDEK","TRACK-1",client_sender=failed_sender
        )
        self.assertEqual(result["status"],"shipped")
        self.assertEqual(
            self.slices()[0]["status"],fs.RF_SHIPPED_TO_CLIENT
        )
        failures=[
            x for x in self.supplier_events()
            if x["event_type"]=="client_tracking_notify_failed"
        ]
        self.assertEqual(len(failures),1)
        self.assertIn("client telegram down",failures[0]["details"])

    def test_historical_partial_tracking_can_only_recover_with_same_values(self):
        self.reserve_send_assemble()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE supplier_orders
                   SET carrier='CDEK',tracking_number='OLD-PARTIAL'
                   WHERE id=?""",
                (self.sid,),
            )
        with self.assertRaisesRegex(ValueError,"another tracking"):
            self.service.add_tracking(
                self.sid,"CDEK","DIFFERENT",client_sender=lambda *a:"x"
            )
        self.assertEqual(self.order_row()["status"],"assembled")

        recovered=self.service.add_tracking(
            self.sid,"CDEK","OLD-PARTIAL",client_sender=lambda *a:"x"
        )
        self.assertEqual(recovered["status"],"shipped")
        self.assertEqual(recovered["tracking_number"],"OLD-PARTIAL")

    def test_web_admin_card_shows_carrier_tracking_and_ship_date(self):
        self.reserve_send_assemble()
        result=self.service.add_tracking(
            self.sid,"CDEK","TRACK-WEB",client_sender=lambda *a:"x"
        )
        html=supplier_web_admin.render_card(result)
        self.assertIn("CDEK",html)
        self.assertIn("TRACK-WEB",html)
        self.assertIn("Отправлено:",html)
        expected=supplier_web_admin._dt(result["shipped_at"])
        self.assertIn(expected,html)

    def test_only_rf_slice_ships_when_shortage_part_was_rerouted_to_usa(self):
        self.reserve_send_assemble = None  # prevent accidental use of full path

        # Recreate from sent state and split one unit to USA before RF assembly.
        r=stock_engine.reserve_stock(
            self.wid,"OEM-1",2,
            order_id="ORD-1",order_item_id=1,
            manufacturer="BRP",db_file=self.db,
        )
        self.assertTrue(r["ok"])
        supplier_delivery.send_one(
            self.service,self.sid,
            {"telegram":lambda destination,text:"telegram:accepted"},
        )

        old_notify=supplier_customer_notify.notify_shortage if False else None
        # Avoid network: client chat exists but shortage notification can fail;
        # the shortage remains valid regardless of notification outcome.
        shortage=self.service.report_item_problem(
            self.sid,self.supplier_item_id,1
        )
        self.service.decide_shortage(
            shortage["id"],"usa_quote"
        )
        supplier_shortages.set_usa_quote(
            self.db,shortage["id"],status="quoted",
            unit_price_rub=1000,total_price_rub=1000,
            dp_usd=10,source="test",
        )
        self.service.decide_usa_shortage_quote(
            shortage["id"],True
        )
        self.service.change_status(self.sid,"confirmed")
        self.service.change_status(self.sid,"assembled")

        self.service.add_tracking(
            self.sid,"CDEK","TRACK-MIX",client_sender=lambda *a:"x"
        )
        rows=self.slices()
        rf=[x for x in rows if x["route"]==fs.ROUTE_RF]
        usa=[x for x in rows if x["route"]==fs.ROUTE_USA]
        self.assertEqual(len(rf),1)
        self.assertEqual(len(usa),1)
        self.assertEqual(rf[0]["status"],fs.RF_SHIPPED_TO_CLIENT)
        self.assertEqual(usa[0]["status"],fs.USA_ORDERED)


if __name__=="__main__":
    unittest.main(verbosity=2)
