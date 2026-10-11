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
import supplier_web_admin
import warehouse_store
from supplier_order_service import SupplierOrderService


class SupplierDeliveredPoint8Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_path=Path(tempfile.mkdtemp(prefix="supplier_point8_"))
        self.db=self.tmp_path/"orders.db"

        warehouse_store.init_warehouse_db(self.db)
        self.wid=warehouse_store.add_warehouse(
            "WH1","Moscow","W1","Склад 1",
            priority=10,db_file=self.db,
        )
        warehouse_store.ensure_source(
            self.wid,"website",enabled=True,priority=10,
            ttl_minutes=60,db_file=self.db,
        )
        warehouse_store.record_source_stock(
            self.wid,"OEM-1","in_stock",10,"website",
            price_rub=1000,db_file=self.db,
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
        supplier_channels.save(
            self.db,self.wid,"telegram","test-destination"
        )

    def reserve_send_ship(self):
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
        self.service.change_status(
            self.sid,"confirmed",
            actor_type="admin",actor_id=42,source="telegram_admin",
        )
        self.service.change_status(
            self.sid,"assembled",
            actor_type="admin",actor_id=42,source="telegram_admin",
        )
        self.service.add_tracking(
            self.sid,"CDEK","TRACK-1",
            client_sender=lambda *a:"tracking-msg",
            actor_type="admin",actor_id=42,source="telegram_admin",
        )

    def order_row(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return dict(c.execute(
                """SELECT status,carrier,tracking_number,
                          shipped_at,delivered_at
                   FROM supplier_orders WHERE id=?""",
                (self.sid,),
            ).fetchone())

    def reservation(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return dict(c.execute(
                """SELECT * FROM warehouse_stock_reservations
                   WHERE order_item_id=1 ORDER BY id DESC LIMIT 1"""
            ).fetchone())

    def slices(self):
        return fs.list_for_supplier_order(self.db,self.sid)

    def fulfillment_events(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [dict(x) for x in c.execute(
                "SELECT * FROM fulfillment_events ORDER BY id"
            )]

    def supplier_events(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [dict(x) for x in c.execute(
                """SELECT * FROM supplier_order_events
                   WHERE supplier_order_id=? ORDER BY id""",
                (self.sid,),
            )]

    def test_delivery_before_shipped_is_blocked(self):
        with self.assertRaisesRegex(ValueError,"not ready for delivery"):
            self.service.deliver_supplier_order(self.sid)
        self.assertEqual(self.order_row()["status"],"not_sent")
        self.assertEqual(
            self.slices()[0]["status"],fs.RF_PENDING_RESERVATION
        )

    def test_successful_delivery_closes_rf_and_reservation_without_overcount(self):
        self.reserve_send_ship()
        observed={}
        def delivered_sender(chat_id,message):
            observed["order"]=self.order_row()
            observed["reservation"]=self.reservation()
            observed["slice"]=self.slices()[0]
            return "delivered-msg-1"

        result=self.service.deliver_supplier_order(
            self.sid,
            actor_type="admin",
            actor_id=42,
            source="telegram_admin",
            client_sender=delivered_sender,
        )
        self.assertEqual(result["status"],"delivered")
        row=self.order_row()
        self.assertTrue(row["delivered_at"])
        self.assertEqual(row["status"],"delivered")

        reservation=self.reservation()
        self.assertEqual(reservation["status"],"fulfilled")
        self.assertTrue(reservation["fulfilled_at"])
        self.assertIsNone(reservation["absorbed_at"])

        # fulfilled is terminal, not an active reservation
        with sqlite3.connect(self.db) as c:
            active=c.execute(
                """SELECT COUNT(*) FROM warehouse_stock_reservations
                   WHERE order_item_id=1
                     AND status IN ('hold','reserved','committed')"""
            ).fetchone()[0]
        self.assertEqual(active,0)

        # But manual stock still subtracts fulfilled quantity so sold stock
        # cannot reappear just because delivery was confirmed.
        available=stock_engine.get_available_stock(
            self.wid,"OEM-1",self.db
        )
        self.assertEqual(available["raw_quantity"],10)
        self.assertEqual(available["blocked_quantity"],2)
        self.assertEqual(available["available_quantity"],8)

        self.assertEqual(
            self.slices()[0]["status"],fs.RF_DELIVERED
        )
        self.assertEqual(observed["order"]["status"],"delivered")
        self.assertEqual(
            observed["reservation"]["status"],"fulfilled"
        )
        self.assertEqual(
            observed["slice"]["status"],fs.RF_DELIVERED
        )

        delivered_events=[
            x for x in self.fulfillment_events()
            if x["event_type"]=="delivered_to_client"
        ]
        self.assertEqual(len(delivered_events),1)
        self.assertEqual(delivered_events[0]["actor_type"],"admin")
        self.assertEqual(delivered_events[0]["actor_id"],"42")
        self.assertEqual(delivered_events[0]["source"],"telegram_admin")

        notify=[
            x for x in self.supplier_events()
            if x["event_type"]=="client_delivered_notified"
        ]
        self.assertEqual(len(notify),1)

    def test_repeated_delivery_is_idempotent(self):
        self.reserve_send_ship()
        calls=[]
        first=self.service.deliver_supplier_order(
            self.sid,
            client_sender=lambda *a:calls.append(1) or "msg-1",
        )
        delivered_at=first["delivered_at"]
        f_count=len(self.fulfillment_events())
        s_count=len(self.supplier_events())

        second=self.service.deliver_supplier_order(
            self.sid,
            client_sender=lambda *a:calls.append(2) or "msg-2",
        )
        self.assertEqual(calls,[1])
        self.assertEqual(second["delivered_at"],delivered_at)
        self.assertEqual(len(self.fulfillment_events()),f_count)
        self.assertEqual(len(self.supplier_events()),s_count)
        self.assertEqual(self.reservation()["status"],"fulfilled")

    def test_client_notification_failure_does_not_rollback_delivery(self):
        self.reserve_send_ship()
        def failed(*args):
            raise RuntimeError("client telegram down")

        result=self.service.deliver_supplier_order(
            self.sid,client_sender=failed
        )
        self.assertEqual(result["status"],"delivered")
        self.assertEqual(
            self.slices()[0]["status"],fs.RF_DELIVERED
        )
        self.assertEqual(self.reservation()["status"],"fulfilled")
        failures=[
            x for x in self.supplier_events()
            if x["event_type"]=="client_delivered_notify_failed"
        ]
        self.assertEqual(len(failures),1)
        self.assertIn("client telegram down",failures[0]["details"])

    def test_fulfillment_history_failure_rolls_everything_back(self):
        self.reserve_send_ship()
        before=self.order_row()
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_delivered_fulfillment_event
              BEFORE INSERT ON fulfillment_events
              WHEN NEW.event_type='delivered_to_client'
              BEGIN SELECT RAISE(ABORT,'delivery history fail'); END;""")

        with self.assertRaises(sqlite3.DatabaseError):
            self.service.deliver_supplier_order(self.sid)
        self.assertEqual(self.order_row(),before)
        self.assertEqual(
            self.slices()[0]["status"],fs.RF_SHIPPED_TO_CLIENT
        )
        self.assertEqual(self.reservation()["status"],"committed")

    def test_supplier_history_failure_rolls_slice_and_stock_back(self):
        self.reserve_send_ship()
        before=self.order_row()
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_delivered_supplier_event
              BEFORE INSERT ON supplier_order_events
              WHEN NEW.event_type='status_changed'
               AND NEW.to_status='delivered'
              BEGIN SELECT RAISE(ABORT,'supplier delivery history fail'); END;""")

        with self.assertRaises(sqlite3.DatabaseError):
            self.service.deliver_supplier_order(self.sid)
        self.assertEqual(self.order_row(),before)
        self.assertEqual(
            self.slices()[0]["status"],fs.RF_SHIPPED_TO_CLIENT
        )
        self.assertEqual(self.reservation()["status"],"committed")
        self.assertIsNone(self.reservation()["fulfilled_at"])

    def test_released_reservation_is_integrity_conflict(self):
        self.reserve_send_ship()
        with sqlite3.connect(self.db) as c:
            c.execute(
                """UPDATE warehouse_stock_reservations
                   SET status='released',released_at='forced'
                   WHERE order_item_id=1"""
            )
        with self.assertRaisesRegex(ValueError,"not deliverable"):
            self.service.deliver_supplier_order(self.sid)
        self.assertEqual(self.order_row()["status"],"shipped")
        self.assertEqual(
            self.slices()[0]["status"],fs.RF_SHIPPED_TO_CLIENT
        )

    def test_already_absorbed_reservation_stays_absorbed(self):
        stock_engine.set_stock_sync_mode(
            self.wid,"snapshot_absorbs_committed",db_file=self.db
        )
        self.reserve_send_ship()
        # Authoritative snapshot after shipment absorbs the committed row.
        warehouse_store.record_source_stock(
            self.wid,"OEM-1","in_stock",8,"website",
            price_rub=1000,db_file=self.db,
        )
        self.assertEqual(self.reservation()["status"],"absorbed")

        result=self.service.deliver_supplier_order(
            self.sid,client_sender=lambda *a:"delivered"
        )
        self.assertEqual(result["status"],"delivered")
        self.assertEqual(self.reservation()["status"],"absorbed")

    def test_fulfilled_is_absorbed_by_later_authoritative_snapshot(self):
        stock_engine.set_stock_sync_mode(
            self.wid,"snapshot_absorbs_committed",db_file=self.db
        )
        self.reserve_send_ship()
        self.service.deliver_supplier_order(
            self.sid,client_sender=lambda *a:"delivered"
        )
        self.assertEqual(self.reservation()["status"],"fulfilled")

        before=stock_engine.get_available_stock(
            self.wid,"OEM-1",self.db
        )
        self.assertEqual(before["available_quantity"],8)

        warehouse_store.record_source_stock(
            self.wid,"OEM-1","in_stock",8,"website",
            price_rub=1000,db_file=self.db,
        )
        self.assertEqual(self.reservation()["status"],"absorbed")
        after=stock_engine.get_available_stock(
            self.wid,"OEM-1",self.db
        )
        self.assertEqual(after["raw_quantity"],8)
        self.assertEqual(after["blocked_quantity"],0)
        self.assertEqual(after["available_quantity"],8)

    def test_web_admin_delivered_tab_and_card_show_delivery_time(self):
        self.reserve_send_ship()
        result=self.service.deliver_supplier_order(
            self.sid,client_sender=lambda *a:"delivered"
        )
        card=supplier_web_admin.render_card(result)
        self.assertIn("✅ Доставлено",card)
        self.assertIn("Доставлено:",card)
        self.assertIn(
            supplier_web_admin._dt(result["delivered_at"]),card
        )

        queue=supplier_web_admin.render_queue(
            self.service,"delivered"
        )
        self.assertIn("ORD-1",queue)
        self.assertIn("✅ Доставлено",queue)

    def test_client_order_business_status_is_not_auto_completed(self):
        self.reserve_send_ship()
        self.service.deliver_supplier_order(
            self.sid,client_sender=lambda *a:"delivered"
        )
        with sqlite3.connect(self.db) as c:
            status=c.execute(
                "SELECT status FROM orders WHERE order_id='ORD-1'"
            ).fetchone()[0]
        self.assertEqual(status,"confirmed")

    def test_change_status_delivered_uses_atomic_delivery_path(self):
        self.reserve_send_ship()
        result=self.service.change_status(
            self.sid,
            "delivered",
            actor_type="admin",
            actor_id=99,
            source="web_admin",
        )
        self.assertEqual(result["status"],"delivered")
        self.assertEqual(self.reservation()["status"],"fulfilled")
        event=[
            x for x in self.fulfillment_events()
            if x["event_type"]=="delivered_to_client"
        ][0]
        self.assertEqual(event["actor_id"],"99")
        self.assertEqual(event["source"],"web_admin")


if __name__=="__main__":
    unittest.main(verbosity=2)
