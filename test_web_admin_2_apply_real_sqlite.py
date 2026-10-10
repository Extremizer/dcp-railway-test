"""Apply against the real warehouse schema and helpers, in disposable SQLite only."""
import sqlite3
import re
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import admin_order_apply_service as apply
import admin_order_service as service
import admin_order_web as web
import stock_engine
import warehouse_store as store


class RealSqliteApplyRegression(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="apply-real-regression-")
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "fixture.sqlite3"
        store.init_warehouse_db(self.db)
        self.warehouse = store.add_warehouse("TEST", "TEST", "TEST", "TEST", db_file=self.db)
        store.ensure_source(self.warehouse, "file", ttl_minutes=60, db_file=self.db)
        with sqlite3.connect(self.db) as c:
            c.executescript("""
                CREATE TABLE orders(order_id TEXT PRIMARY KEY, status TEXT);
                CREATE TABLE order_items(id INTEGER PRIMARY KEY, order_id TEXT,
                  position INTEGER, manufacturer TEXT, oem TEXT, quantity REAL,
                  warehouse_id INTEGER, price_snapshot_rub REAL, offer_source TEXT);
                INSERT INTO orders VALUES('SAFE-REAL','confirmed');
            """)
        self.add_item(1, "TEST-A")

    def add_item(self, item_id, oem, quantity=2):
        with sqlite3.connect(self.db) as c:
            c.execute("INSERT INTO order_items VALUES(?, 'SAFE-REAL', ?, 'TEST', ?, ?, ?, 100, 'warehouse')",
                      (item_id, item_id, oem, quantity, self.warehouse))
        store.record_source_stock(self.warehouse, oem, "in_stock", 10, "file",
                                  price_rub=100, db_file=self.db)

    def rows(self, table="warehouse_stock_reservations"):
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute("SELECT * FROM " + table)]

    def test_real_apply_idempotence_postcheck_and_event(self):
        first = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertTrue(first["ok"], first)
        self.assertEqual(first["state"], "prepared")
        self.assertEqual(self.rows()[0]["quantity"], 2)
        self.assertEqual(len(self.rows("warehouse_stock_reservation_events")), 1)
        second = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertTrue(second["ok"], second)
        self.assertFalse(second["changed"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(service.get_order("SAFE-REAL", self.db)["order"]["status"], "confirmed")

    def test_partial_reservation_tops_up_without_duplicate(self):
        prior = stock_engine.reserve_stock(self.warehouse, "TEST-A", 1,
                    order_id="SAFE-REAL", order_item_id=1, db_file=self.db)
        self.assertTrue(prior["ok"], prior)
        result = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["changed"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["quantity"], 2)
        self.assertFalse(apply.prepare_order_apply("SAFE-REAL", self.db)["changed"])

    def test_committed_partial_reservation_is_not_mutated(self):
        prior = stock_engine.reserve_stock(self.warehouse, "TEST-A", 1,
                    order_id="SAFE-REAL", order_item_id=1, db_file=self.db)
        stock_engine.commit_reservation(prior["reservation_id"], self.db)
        result = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertEqual(result["reason"], "reservation_not_mutable")
        self.assertEqual(self.rows()[0]["quantity"], 1)

    def test_concurrent_apply_creates_one_reservation(self):
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: apply.prepare_order_apply("SAFE-REAL", self.db), range(2)))
        self.assertTrue(all(r["ok"] for r in results), results)
        self.assertEqual(sum(r["changed"] for r in results), 1)
        self.assertEqual(len(self.rows()), 1)

    def test_sqlite_failure_on_second_item_rolls_back_rows_and_events(self):
        self.add_item(2, "TEST-B")
        original = stock_engine._create_reservation
        def fail_second(conn, **kw):
            if kw["order_item_id"] == 2:
                raise sqlite3.IntegrityError("forced second insert failure")
            return original(conn, **kw)
        with patch.object(stock_engine, "_create_reservation", side_effect=fail_second):
            result = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertEqual(result["reason"], "sqlite_error")
        self.assertFalse(result["changed"])
        self.assertEqual(self.rows(), [])
        self.assertEqual(self.rows("warehouse_stock_reservation_events"), [])

    def test_real_precheck_blockers_make_no_writes(self):
        for sql in (
            "UPDATE orders SET status='new'",
            "UPDATE warehouse_stock_current SET price_rub=101",
            "UPDATE warehouse_stock_current SET quantity=0",
            "UPDATE warehouse_stock_current SET expires_at='2000-01-01T00:00:00+00:00'",
            "UPDATE warehouses SET active=0",
        ):
            with self.subTest(sql=sql):
                with sqlite3.connect(self.db) as c:
                    c.execute(sql)
                result = apply.prepare_order_apply("SAFE-REAL", self.db)
                self.assertFalse(result["ok"], result)
                self.assertFalse(result["changed"])
                self.assertEqual(self.rows(), [])
                with sqlite3.connect(self.db) as c:
                    c.execute("UPDATE orders SET status='confirmed'")
                    c.execute("UPDATE warehouses SET active=1")
                store.record_source_stock(self.warehouse, "TEST-A", "in_stock", 10, "file",
                                          price_rub=100, db_file=self.db)

    def test_committed_postcheck_blocked_is_not_reported_as_rollback(self):
        original = service.prepare_order_dry_run
        calls = 0
        def post_blocked(*args):
            nonlocal calls
            calls += 1
            if calls == 1:
                return original(*args)
            return {"ready_to_apply": False, "would_change_db": False,
                    "blockers": [{"kind": "forced"}], "actions": []}
        with patch.object(service, "prepare_order_dry_run", side_effect=post_blocked):
            result = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertEqual(result["state"], "committed_postcheck_blocked")
        self.assertTrue(result["changed"])
        self.assertTrue(result["details"]["manual_review_required"])
        self.assertEqual(len(self.rows()), 1)
        rendered = web.render_order_card(service.get_order("SAFE-REAL", self.db), apply_result=result)
        self.assertIn("изменения не откатывались", rendered)
        self.assertNotIn("Apply BLOCK", rendered)

    def test_real_http_dry_run_apply_and_repeat(self):
        from test_web_admin_2_apply_http import HttpApplyRegression
        import supplier_admin_auth as auth
        http = HttpApplyRegression()
        http.setUp()
        self.addCleanup(http.doCleanups)
        http.runtime["core"].ORDERS_DB_FILE = self.db
        http.runtime["admin_order_service"] = service
        http.runtime["admin_order_web"] = web
        http.runtime["admin_order_apply_service"] = apply
        http.client.cookies.set(auth.WEB_ADMIN_COOKIE, http.cookie)
        page = http.client.get("/admin/orders/SAFE-REAL/prepare-dry-run")
        self.assertEqual(page.status_code, 200)
        token = re.search(r'name="csrf_token" value="([^"]+)"', page.text).group(1)
        for _ in range(2):
            response = http.client.post("/admin/orders/SAFE-REAL/prepare-apply", data={"csrf_token": token})
            self.assertEqual(response.status_code, 200)
            self.assertIn("Apply PASS", response.text)
        self.assertEqual(len(self.rows()), 1)

    def test_transaction_rechecks_changed_status_stock_and_price(self):
        original = service.prepare_order_dry_run
        for sql, reason in (
            ("UPDATE orders SET status='new'", "order_status"),
            ("UPDATE warehouse_stock_current SET quantity=0", "insufficient_stock"),
            ("UPDATE warehouse_stock_current SET price_rub=101", "price_changed"),
        ):
            with self.subTest(reason=reason):
                def change_after_precheck(*args):
                    result = original(*args)
                    with sqlite3.connect(self.db) as c:
                        c.execute(sql)
                    return result
                with patch.object(service, "prepare_order_dry_run", side_effect=change_after_precheck):
                    result = apply.prepare_order_apply("SAFE-REAL", self.db)
                self.assertEqual(result["reason"], reason, result)
                self.assertEqual(self.rows(), [])
                with sqlite3.connect(self.db) as c:
                    c.execute("UPDATE orders SET status='confirmed'")
                store.record_source_stock(self.warehouse, "TEST-A", "in_stock", 10, "file",
                                          price_rub=100, db_file=self.db)

    def test_partial_top_up_is_rolled_back_on_later_failure(self):
        stock_engine.reserve_stock(self.warehouse, "TEST-A", 1,
                    order_id="SAFE-REAL", order_item_id=1, db_file=self.db)
        self.add_item(2, "TEST-B")
        with patch.object(stock_engine, "_create_reservation", side_effect=sqlite3.IntegrityError("forced")):
            result = apply.prepare_order_apply("SAFE-REAL", self.db)
        self.assertFalse(result["changed"])
        self.assertEqual(len(self.rows()), 1)
        self.assertEqual(self.rows()[0]["quantity"], 1)
        self.assertEqual(len(self.rows("warehouse_stock_reservation_events")), 1)


if __name__ == "__main__":
    unittest.main()
