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
        original = self.s.split_item
        def after_split(*args):
            original(*args)
            raise RuntimeError("injected after committed split")
        with patch.object(self.s, "split_item", side_effect=after_split):
            with self.assertRaises(RuntimeError):
                self.s.add_to_shipment(self.shipment, self.item, 1)
        slices = self.rows("SELECT id,qty FROM item_slices WHERE order_id='order-red'")
        self.assertEqual(slices, [(self.item, 3)], f"orphan split: {slices}")
        self.assertEqual(self.rows("SELECT * FROM shipment_items"), [])

    def test_departure_failure_rolls_back_everything(self):
        other = self.s.add_item("order-red", "TEST-OTHER", 1, status="На складе США")
        self.s.add_to_shipment(self.shipment, self.item, 3)
        self.s.add_to_shipment(self.shipment, other, 1)
        original = self.s.set_status
        count = 0
        def flaky(*args, **kwargs):
            nonlocal count
            count += 1
            if count == 2:
                raise RuntimeError("injected after first status change")
            return original(*args, **kwargs)
        with patch.object(self.s, "set_status", side_effect=flaky):
            with self.assertRaises(RuntimeError):
                self.s.depart_shipment(self.shipment)
        self.assertEqual(self.rows("SELECT status FROM usa_shipments WHERE id=?", (self.shipment,)), [("draft",)])
        self.assertEqual(self.rows("SELECT status FROM item_slices WHERE id IN (?,?) ORDER BY id", (self.item, other)), [("На складе США",), ("На складе США",)])

    def test_tracking_failure_rolls_back_assignment(self):
        tracking = self.s.create_tracking("TRACE-RED-ONE")
        with patch.object(self.s, "set_status", side_effect=RuntimeError("injected before history")):
            with self.assertRaises(RuntimeError):
                self.s.assign_tracking(self.item, tracking)
        self.assertEqual(self.rows("SELECT supplier_tracking_id FROM item_slices WHERE id=?", (self.item,)), [(None,)])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM item_status_history WHERE item_slice_id=?", (self.item,)), [(0,)])

    def test_guard_and_quantity(self):
        allocated = self.s.add_to_shipment(self.shipment, self.item, 1)
        self.assertEqual(self.rows("SELECT SUM(qty) FROM item_slices WHERE order_id='order-red'"), [(3,)])
        self.assertEqual(self.rows("SELECT qty FROM shipment_items WHERE item_slice_id=?", (allocated,)), [(1,)])
        with self.assertRaises((ValueError, sqlite3.IntegrityError)):
            self.s.add_to_shipment(self.shipment, allocated, 1)

if __name__ == "__main__":
    unittest.main()
