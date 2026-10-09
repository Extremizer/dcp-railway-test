"""Isolated transaction regressions for WEB ADMIN 2 Apply (never uses production DB)."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import admin_order_apply_service as apply
import admin_order_service
import stock_engine


class ApplyRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "fixture.sqlite3"
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE orders(order_id TEXT PRIMARY KEY, status TEXT);
                CREATE TABLE order_items(
                  id INTEGER PRIMARY KEY, order_id TEXT, position INTEGER,
                  manufacturer TEXT, oem TEXT, quantity REAL,
                  warehouse_id INTEGER, price_snapshot_rub REAL,
                  offer_source TEXT);
                CREATE TABLE regression_reservations(
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  order_id TEXT, order_item_id INTEGER, quantity REAL);
                INSERT INTO orders VALUES ('SAFE-1','confirmed');
                INSERT INTO order_items VALUES
                  (1,'SAFE-1',1,'TEST','OEM-A',1,3,19700,'warehouse');
            """)
        self.stock = {"OEM-A": 5, "OEM-B": 5}
        self.prices = {"OEM-A": 19700, "OEM-B": 19700}
        def dry_run(order_id, db_file):
            with sqlite3.connect(db_file) as c:
                count = c.execute("SELECT COUNT(*) FROM regression_reservations").fetchone()[0]
            return {"ready_to_apply": True, "would_change_db": count == 0,
                    "blockers": [], "actions": [] if count else [{"action": "reserve_missing"}]}
        def active(conn, warehouse_id, oem, **kwargs):
            rows = conn.execute(
                "SELECT r.order_id,r.order_item_id,r.quantity FROM regression_reservations r "
                "JOIN order_items i ON i.id=r.order_item_id WHERE i.oem=?",
                (oem,)).fetchall()
            data = [{"order_id": r[0], "order_item_id": r[1], "quantity": r[2]} for r in rows]
            return sum(r["quantity"] for r in data), data
        def create(conn, **kw):
            cur = conn.execute("INSERT INTO regression_reservations(order_id,order_item_id,quantity) VALUES (?,?,?)",
                               (kw["order_id"], kw["order_item_id"], kw["quantity"]))
            return cur.lastrowid
        patches = [
            patch.object(admin_order_service, "prepare_order_dry_run", side_effect=dry_run),
            patch.object(stock_engine, "_warehouse_is_active", return_value=True),
            patch.object(stock_engine, "_best_snapshot",
                         side_effect=lambda conn, warehouse, oem, now: (
                             {"quantity": self.stock[oem], "is_fresh": True, "observed_at": None}
                             if oem in self.stock else None)),
            patch.object(stock_engine, "_best_price_snapshot",
                         side_effect=lambda conn, warehouse, oem, now: (
                             {"price_rub": self.prices[oem]} if oem in self.prices else None)),
            patch.object(stock_engine, "_blocking_quantity", side_effect=active),
            patch.object(stock_engine, "_create_reservation", side_effect=create),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def count(self):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM regression_reservations").fetchone()[0]

    def test_apply_and_second_apply_idempotent(self):
        first = apply.prepare_order_apply("SAFE-1", self.db)
        self.assertTrue(first["ok"], first)
        self.assertTrue(first["changed"])
        self.assertEqual(self.count(), 1)
        second = apply.prepare_order_apply("SAFE-1", self.db)
        self.assertTrue(second["ok"], second)
        self.assertFalse(second["changed"])
        self.assertEqual(self.count(), 1)

    def test_price_changed_rolls_back(self):
        self.prices["OEM-A"] = 20200
        result = apply.prepare_order_apply("SAFE-1", self.db)
        self.assertEqual(result["reason"], "price_changed")
        self.assertEqual(self.count(), 0)

    def test_insufficient_stock_rolls_back(self):
        self.stock["OEM-A"] = 0
        result = apply.prepare_order_apply("SAFE-1", self.db)
        self.assertEqual(result["reason"], "insufficient_stock")
        self.assertEqual(self.count(), 0)

    def test_late_blocker_rolls_back_first_reservation(self):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO order_items VALUES (2,'SAFE-1',2,'TEST','OEM-B',1,3,19700,'warehouse')")
        self.stock.pop("OEM-B")
        result = apply.prepare_order_apply("SAFE-1", self.db)
        self.assertEqual(result["reason"], "stock_unknown")
        self.assertEqual(self.count(), 0)

    def test_nonconfirmed_order_blocked(self):
        with sqlite3.connect(self.db) as c:
            c.execute("UPDATE orders SET status='new'")
        result = apply.prepare_order_apply("SAFE-1", self.db)
        self.assertEqual(result["reason"], "order_status")
        self.assertEqual(self.count(), 0)


if __name__ == "__main__":
    unittest.main()
