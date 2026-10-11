"""Launcher lifecycle with intercepted processes and temporary SQLite only.

Schema bootstrap executes the actual orders initializer extracted from source;
it does not import bot modules or start Telegram/DCP/web/storage applications.
"""
import ast
import contextlib
from datetime import datetime
import hashlib
import io
import json
import logging
from pathlib import Path
import runpy
import sqlite3
import subprocess
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parent


class ExitedChild:
    pid = 12345

    def poll(self):
        return 0


class StartupSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="startup-safety-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "orders.db"

    def initialize_orders(self):
        source = (ROOT / "extremizer_bot.py").read_text(encoding="utf-8-sig")
        tree = ast.parse(source)
        initializer = next(n for n in tree.body
                           if isinstance(n, ast.FunctionDef) and n.name == "init_orders_db")
        scope = {
            "sqlite3": sqlite3, "json": json, "datetime": datetime,
            "log": logging.getLogger("startup-safety"),
            "ORDERS_DB_FILE": self.db,
            "PUBLIC_MSRP_CACHE_FILE": self.root / "absent-public-cache.json",
        }
        # Preserve every statement of the current initializer, including DDL,
        # migrations/defaults; omit module imports and runtime startup.
        exec(compile(ast.Module(body=[initializer], type_ignores=[]),
                     "extremizer_bot.py", "exec"), scope)
        scope["init_orders_db"]()

    def snapshot(self):
        with sqlite3.connect(self.db) as conn:
            schema = conn.execute(
                "SELECT type,name,sql FROM sqlite_master ORDER BY type,name").fetchall()
            tables = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
            rows = {t: conn.execute('SELECT * FROM "' + t + '" ORDER BY rowid').fetchall()
                    for t in tables}
        return hashlib.sha256(self.db.read_bytes()).hexdigest(), schema, rows

    def launch(self, *, token=True, spawn=None, probnik=False, web2=False):
        runtime = runpy.run_path(str(ROOT / "web1_runtime.py"), run_name="startup_test")
        env = {"EXTREMIZER_ORDERS_DB_FILE": str(self.db)}
        if token:
            env["EXTREMIZER_BOT_TOKEN"] = "synthetic-test-token"
        if probnik:
            env["PROBNIK_BOT_TOKEN"] = "synthetic-probnik-token"
        if web2:
            env["EXTREMIZER_WEB2_ENABLED"] = "1"
        with patch.dict("os.environ", env, clear=True), \
             patch.object(subprocess, "run", side_effect=AssertionError("seed/run forbidden")) as seed, \
             patch.object(subprocess, "Popen", side_effect=spawn or (lambda args: ExitedChild())) as children, \
             contextlib.redirect_stdout(io.StringIO()):
            result = runtime["main"]()
            seed.assert_not_called()
        return result, [call.args[0] for call in children.call_args_list]

    def test_source_and_railway_contract(self):
        source = (ROOT / "web1_runtime.py").read_text()
        self.assertNotIn("seed_probe14", source)
        config = json.loads((ROOT / "railway.json").read_text())
        self.assertEqual(config["deploy"]["startCommand"], "python -u web1_runtime.py")
        self.assertNotIn("preDeployCommand", config["deploy"])
        self.assertEqual(config["deploy"]["healthcheckPath"], "/health")

    def test_missing_token_preserves_initialized_database(self):
        self.initialize_orders()
        before = self.snapshot()
        result, commands = self.launch(token=False)
        self.assertEqual(result, 2)
        self.assertEqual(commands, [])
        self.assertEqual(self.snapshot(), before)

    def test_repeated_start_does_not_import_historical_snapshots(self):
        self.initialize_orders()
        before = self.snapshot()
        for _ in range(3):
            result, commands = self.launch(probnik=True)
            self.assertEqual(result, 1)  # Existing supervisor contract: exited child.
            self.assertEqual([args[2] for args in commands],
                             ["extremizer_bot.py", "probnik_app.py", "backup_snapshot_helper.py", "-m"])
            self.assertEqual(commands[-1][3:5], ["uvicorn", "web_app:app"])
            self.assertEqual(self.snapshot(), before)

    def test_existing_older_dp_value_is_preserved(self):
        self.initialize_orders()
        with sqlite3.connect(self.db) as conn:
            conn.execute("""INSERT INTO dealer_price_cache
                (manufacturer,oem,dealer_price_usd,source,first_seen_at,last_verified_at,use_count)
                VALUES ('Ski-Doo','417300571',999,'fixture','2026-09-01',
                        '2026-09-01T00:00:00+00:00',7)""")
        before = self.snapshot()
        for _ in range(3):
            self.launch()
            self.assertEqual(self.snapshot(), before)

    def test_explicit_web2_host_preserves_supervisor_and_database(self):
        self.initialize_orders()
        before = self.snapshot()
        result, commands = self.launch(probnik=True, web2=True)
        self.assertEqual(result, 1)
        self.assertEqual(commands[-1][3:5], ["uvicorn", "web2_runtime_app:app"])
        self.assertEqual([args[2] for args in commands],
                         ["extremizer_bot.py", "probnik_app.py", "backup_snapshot_helper.py", "-m"])
        self.assertEqual(self.snapshot(), before)

    def test_empty_first_boot_reaches_established_initializer(self):
        self.assertFalse(self.db.exists())

        def initialize_fake_child(args):
            if args[2] == "extremizer_bot.py":
                self.initialize_orders()
            return ExitedChild()

        result, commands = self.launch(spawn=initialize_fake_child)
        self.assertEqual(result, 1)
        self.assertEqual(len(commands), 3)
        with sqlite3.connect(self.db) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"orders", "order_items", "dealer_price_cache",
                             "oem_catalog_cache", "oem_catalog_aliases"}.issubset(tables))
            for table in ("dealer_price_cache", "oem_catalog_cache", "oem_catalog_aliases"):
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0], 0)

    def test_missing_configuration_does_not_create_database(self):
        result, commands = self.launch(token=False)
        self.assertEqual(result, 2)
        self.assertEqual(commands, [])
        self.assertFalse(self.db.exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_failure_before_first_child_preserves_database(self):
        self.initialize_orders()
        before = self.snapshot()
        with self.assertRaisesRegex(OSError, "injected spawn failure"):
            self.launch(spawn=lambda args: (_ for _ in ()).throw(OSError("injected spawn failure")))
        self.assertEqual(self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
