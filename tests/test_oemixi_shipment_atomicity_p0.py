import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from oemixibot_store import OemixiStore

class AtomicityRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "dealer.sqlite")
        self.s = OemixiStore(self.path)
        self.s.init()
        self.s.add_dealer("dealer-red", 91000001, "Regression Dealer")
        self.s.create_order("order-red", "dealer-red")
        self.item = self.s.add_item("order-red", "TEST-OEM", 3, status="На складе США", unit_price_usd=10.0, line_total_usd=30.0)
        self.shipment = self.s.create_shipment("SHIP-RED", "КОМФОРТ")

    def rows(self, sql, args=()):
        with sqlite3.connect(self.path) as c:
            return c.execute(sql, args).fetchall()

    def test_split_failure_rolls_back_allocation(self):
        original = self.s._split_item_conn
        def after_sql(conn, *args):
            original(conn, *args)
            raise RuntimeError("injected after SQL split before commit")
        history_before = self.rows("SELECT * FROM item_status_history")
        with patch.object(self.s, "_split_item_conn", side_effect=after_sql):
            with self.assertRaises(RuntimeError):
                self.s.add_to_shipment(self.shipment, self.item, 1)
        self.assertEqual(self.rows("SELECT id,qty FROM item_slices WHERE order_id='order-red'"), [(self.item, 3)])
        self.assertEqual(self.rows("SELECT * FROM shipment_items"), [])
        self.assertEqual(self.rows("SELECT * FROM item_status_history"), history_before)

    def test_retry_after_failure_and_snapshot_conservation(self):
        original = self.s._split_item_conn
        def fail_after_sql(conn, *args):
            original(conn, *args)
            raise RuntimeError("interrupted")
        with patch.object(self.s, "_split_item_conn", side_effect=fail_after_sql):
            with self.assertRaises(RuntimeError):
                self.s.add_to_shipment(self.shipment, self.item, 1)
        child = self.s.add_to_shipment(self.shipment, self.item, 1)
        self.assertEqual(self.rows("SELECT qty,line_total_usd FROM item_slices WHERE order_id='order-red' ORDER BY id"), [(2,20.0),(1,10.0)])
        self.assertEqual(self.rows("SELECT parent_slice_id FROM item_slices WHERE id=?", (child,)),[(self.item,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM shipment_items"),[(1,)])

    def test_departure_failure_rolls_back_everything(self):
        other = self.s.add_item("order-red", "TEST-OTHER", 1, status="На складе США")
        self.s.add_to_shipment(self.shipment, self.item, 3)
        self.s.add_to_shipment(self.shipment, other, 1)
        original = self.s._set_status_conn
        count = 0
        def after_first_sql(conn, *args):
            nonlocal count
            original(conn, *args)
            count += 1
            if count == 1: raise RuntimeError("injected after item SQL before commit")
        history_before = self.rows("SELECT * FROM item_status_history")
        with patch.object(self.s, "_set_status_conn", side_effect=after_first_sql):
            with self.assertRaises(RuntimeError): self.s.depart_shipment(self.shipment)
        self.assertEqual(self.rows("SELECT status FROM usa_shipments WHERE id=?", (self.shipment,)), [("draft",)])
        self.assertEqual(self.rows("SELECT status FROM item_slices WHERE id IN (?,?) ORDER BY id", (self.item, other)), [("На складе США",), ("На складе США",)])
        self.assertEqual(self.rows("SELECT * FROM item_status_history"),history_before)

    def test_tracking_failure_rolls_back_assignment(self):
        tracking = self.s.create_tracking("TRACE-RED-ONE")
        original = self.s._set_status_conn
        def after_status_sql(conn, *args):
            original(conn, *args)
            raise RuntimeError("injected after history SQL")
        history_before = self.rows("SELECT * FROM item_status_history")
        with patch.object(self.s, "_set_status_conn", side_effect=after_status_sql):
            with self.assertRaises(RuntimeError): self.s.assign_tracking(self.item, tracking)
        self.assertEqual(self.rows("SELECT supplier_tracking_id FROM item_slices WHERE id=?", (self.item,)), [(None,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM item_status_history"), [(0,)])

    def test_concurrent_allocations_are_serialized(self):
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _: self._try_allocate(), range(2)))
        self.assertEqual(sum(x == "ok" for x in results), 1,results)
        self.assertEqual(self.rows("SELECT SUM(qty) FROM shipment_items"),[(3,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM shipment_items"),[(1,)])

    def _try_allocate(self):
        try:
            self.s.add_to_shipment(self.shipment,self.item,3)
            return "ok"
        except (ValueError,sqlite3.OperationalError):
            return "denied"

    def test_guard_and_quantity(self):
        allocated = self.s.add_to_shipment(self.shipment, self.item, 1)
        self.assertEqual(self.rows("SELECT SUM(qty) FROM item_slices WHERE order_id='order-red'"), [(3,)])
        self.assertEqual(self.rows("SELECT qty FROM shipment_items WHERE item_slice_id=?", (allocated,)), [(1,)])
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.s.add_to_shipment(self.shipment, allocated, 1)

if __name__ == "__main__":
    unittest.main()
