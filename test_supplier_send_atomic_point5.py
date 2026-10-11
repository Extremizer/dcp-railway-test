#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import tempfile
import unittest
from pathlib import Path

import supplier_channels
import supplier_delivery
import supplier_orders
import warehouse_store
import stock_engine
from supplier_order_service import SupplierOrderService


class SupplierSendAtomicPoint5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_path = Path(tempfile.mkdtemp(prefix="supplier_send_point5_"))
        self.db = self.tmp_path / "orders.db"
        warehouse_store.init_warehouse_db(self.db)
        self.wid = warehouse_store.add_warehouse(
            "WH1", "Moscow", "W1", "Склад 1", priority=10, db_file=self.db
        )
        warehouse_store.ensure_source(
            self.wid, "website", enabled=True, priority=10,
            ttl_minutes=60, db_file=self.db
        )
        warehouse_store.record_source_stock(
            self.wid, "OEM-1", "in_stock", 10, "website",
            price_rub=1000, db_file=self.db
        )
        with sqlite3.connect(self.db) as c:
            c.executescript("""
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,status TEXT,telegram_user_id INTEGER,
              created_at TEXT,updated_at TEXT
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY,order_id TEXT NOT NULL,position INTEGER NOT NULL,
              manufacturer TEXT,oem TEXT NOT NULL,name TEXT,quantity REAL NOT NULL,
              offer_source TEXT,warehouse_id INTEGER
            );
            """)
            c.execute(
                "INSERT INTO orders VALUES('ORD-1','confirmed',NULL,'now','now')"
            )
            c.execute(
                """INSERT INTO order_items
                VALUES(1,'ORD-1',1,'BRP','OEM-1','Part',2,'warehouse',?)""",
                (self.wid,),
            )
        self.service = SupplierOrderService(self.db)
        self.sid = self.service.create_from_client_order(
            "ORD-1",
            {
                "name": "Client", "phone": "+70000000000",
                "city": "Moscow", "method": "CDEK", "address": "X",
            },
        )[0]["id"]
        supplier_channels.save(self.db, self.wid, "telegram", "test-destination")

    def tearDown(self):
        # Existing project helpers use sqlite connection context managers that
        # commit/rollback but do not eagerly close file handles on Python 3.14
        # for Windows. Process exit releases them; test semantics do not depend
        # on deleting the temporary DB during tearDown.
        pass

    def reserve(self, qty=2):
        return stock_engine.reserve_stock(
            self.wid, "OEM-1", qty,
            order_id="ORD-1", order_item_id=1, manufacturer="BRP",
            db_file=self.db,
        )

    def order_status(self):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                "SELECT status FROM supplier_orders WHERE id=?", (self.sid,)
            ).fetchone()[0]

    def reservation(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                """SELECT * FROM warehouse_stock_reservations
                WHERE order_item_id=1 ORDER BY id DESC LIMIT 1"""
            ).fetchone()
            return dict(row) if row else None

    def attempts(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            return [
                dict(x) for x in c.execute(
                    """SELECT * FROM supplier_delivery_attempts
                    WHERE supplier_order_id=? ORDER BY id""", (self.sid,)
                )
            ]

    def events(self):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            return [
                dict(x) for x in c.execute(
                    """SELECT * FROM supplier_order_events
                    WHERE supplier_order_id=? ORDER BY id""", (self.sid,)
                )
            ]

    def test_direct_sent_bypass_is_blocked(self):
        self.reserve()
        with self.assertRaisesRegex(ValueError, "only through delivery"):
            self.service.change_status(self.sid, "sent", "bypass")
        self.assertEqual(self.order_status(), "not_sent")
        self.assertEqual(self.reservation()["status"], "reserved")

    def test_provider_failure_changes_neither_order_nor_reservation(self):
        self.reserve()
        calls = []
        def failed_sender(destination, text):
            calls.append((destination, text))
            raise RuntimeError("provider down")

        with self.assertRaisesRegex(RuntimeError, "provider down"):
            supplier_delivery.send_one(
                self.service, self.sid, {"telegram": failed_sender}
            )

        self.assertEqual(len(calls), 1)
        self.assertEqual(self.order_status(), "not_sent")
        self.assertEqual(self.reservation()["status"], "reserved")
        attempts = self.attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "failed")
        self.assertIn("provider down", attempts[0]["error"])

    def test_success_commits_reservation_and_marks_sent_atomically(self):
        self.reserve()
        result = supplier_delivery.send_one(
            self.service, self.sid,
            {"telegram": lambda destination, text: "telegram:receipt-1"},
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(self.order_status(), "sent")
        self.assertEqual(self.reservation()["status"], "committed")

        attempts = self.attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "sent")
        self.assertEqual(attempts[0]["provider_receipt"], "telegram:receipt-1")

        status_events = [x for x in self.events() if x["event_type"]=="status_changed"]
        self.assertEqual(len(status_events), 1)
        self.assertEqual(status_events[0]["from_status"], "not_sent")
        self.assertEqual(status_events[0]["to_status"], "sent")
        self.assertIn("receipt=telegram:receipt-1", status_events[0]["details"])

        available = stock_engine.get_available_stock(
            self.wid, "OEM-1", self.db
        )
        self.assertEqual(available["raw_quantity"], 10)
        self.assertEqual(available["blocked_quantity"], 2)
        self.assertEqual(available["available_quantity"], 8)

    def test_retry_after_success_never_calls_provider_again(self):
        self.reserve()
        supplier_delivery.send_one(
            self.service, self.sid,
            {"telegram": lambda destination, text: "telegram:first"},
        )
        calls = []
        with self.assertRaisesRegex(ValueError, "not ready to send"):
            supplier_delivery.send_one(
                self.service, self.sid,
                {"telegram": lambda destination, text: calls.append(1) or "dup"},
            )
        self.assertEqual(calls, [])
        self.assertEqual(len(self.attempts()), 1)
        self.assertEqual(self.reservation()["status"], "committed")

    def test_failed_attempt_can_retry_once_and_then_succeed(self):
        self.reserve()
        n = {"calls": 0}
        def sender(destination, text):
            n["calls"] += 1
            if n["calls"] == 1:
                raise RuntimeError("temporary")
            return "telegram:second-ok"

        with self.assertRaises(RuntimeError):
            supplier_delivery.send_one(
                self.service, self.sid, {"telegram": sender}
            )
        result = supplier_delivery.send_one(
            self.service, self.sid, {"telegram": sender}
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(n["calls"], 2)
        self.assertEqual(
            [x["state"] for x in self.attempts()], ["failed", "sent"]
        )
        self.assertEqual(self.reservation()["status"], "committed")

    def test_accepted_but_finalize_failed_blocks_blind_retry(self):
        self.reserve()
        with sqlite3.connect(self.db) as c:
            c.execute("""CREATE TRIGGER fail_sent_history
              BEFORE INSERT ON supplier_order_events
              WHEN NEW.event_type='status_changed' AND NEW.to_status='sent'
              BEGIN SELECT RAISE(ABORT,'history fail'); END;""")

        calls = []
        def sender(destination, text):
            calls.append(1)
            return "telegram:accepted-before-db-fail"

        with self.assertRaisesRegex(
            RuntimeError, "accepted but local finalization failed"
        ):
            supplier_delivery.send_one(
                self.service, self.sid, {"telegram": sender}
            )

        self.assertEqual(calls, [1])
        self.assertEqual(self.order_status(), "not_sent")
        self.assertEqual(self.reservation()["status"], "reserved")
        attempts = self.attempts()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["state"], "accepted")
        self.assertEqual(
            attempts[0]["provider_receipt"],
            "telegram:accepted-before-db-fail",
        )

        with self.assertRaisesRegex(ValueError, "unresolved"):
            supplier_delivery.send_one(
                self.service, self.sid,
                {"telegram": lambda destination, text: calls.append(2) or "dup"},
            )
        self.assertEqual(calls, [1])

    def test_missing_reservation_blocks_before_provider(self):
        calls = []
        with self.assertRaisesRegex(ValueError, "reservation mismatch"):
            supplier_delivery.send_one(
                self.service, self.sid,
                {"telegram": lambda destination, text: calls.append(1) or "x"},
            )
        self.assertEqual(calls, [])
        self.assertEqual(self.order_status(), "not_sent")
        self.assertEqual(self.attempts(), [])

    def test_already_committed_reservation_can_finalize_send(self):
        r = self.reserve()
        self.assertTrue(
            stock_engine.commit_reservation(r["reservation_id"], self.db)
        )
        result = supplier_delivery.send_one(
            self.service, self.sid,
            {"telegram": lambda destination, text: "telegram:existing-commit"},
        )
        self.assertEqual(result["status"], "sent")
        self.assertEqual(self.reservation()["status"], "committed")

    def test_multi_item_preflight_is_all_or_nothing_before_provider(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                """INSERT INTO order_items
                VALUES(2,'ORD-1',2,'BRP','OEM-2','Part2',1,'warehouse',?)""",
                (self.wid,),
            )
        warehouse_store.record_source_stock(
            self.wid, "OEM-2", "in_stock", 5, "website",
            price_rub=1200, db_file=self.db
        )
        self.service.create_from_client_order("ORD-1", {"name":"Client"})
        self.reserve()

        calls = []
        with self.assertRaisesRegex(ValueError, "reservation mismatch"):
            supplier_delivery.send_one(
                self.service, self.sid,
                {"telegram": lambda destination, text: calls.append(1) or "x"},
            )
        self.assertEqual(calls, [])
        self.assertEqual(self.reservation()["status"], "reserved")
        self.assertEqual(self.order_status(), "not_sent")

    def test_no_channel_blocks_without_attempt(self):
        self.reserve()
        with sqlite3.connect(self.db) as c:
            c.execute(
                "UPDATE supplier_channels SET enabled=0 WHERE warehouse_id=?",
                (self.wid,),
            )
        calls = []
        with self.assertRaisesRegex(ValueError, "не настроен"):
            supplier_delivery.send_one(
                self.service, self.sid,
                {"telegram": lambda destination, text: calls.append(1) or "x"},
            )
        self.assertEqual(calls, [])
        self.assertEqual(self.attempts(), [])
        self.assertEqual(self.reservation()["status"], "reserved")

    def test_legacy_service_batch_send_cannot_bypass_delivery(self):
        self.reserve()
        with self.assertRaisesRegex(ValueError, "supplier_delivery.send_batch"):
            self.service.batch_send([self.sid])
        self.assertEqual(self.order_status(), "not_sent")
        self.assertEqual(self.reservation()["status"], "reserved")


if __name__ == "__main__":
    unittest.main(verbosity=2)
