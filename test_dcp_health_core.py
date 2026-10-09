# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

import dp_live_bridge
import dp_live_health


class DCPHealthCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db = self.root / "orders.db"
        self.old_enforce = os.environ.get("EXTREMIZER_DP_HEALTH_ENFORCE")

    def tearDown(self) -> None:
        if self.old_enforce is None:
            os.environ.pop("EXTREMIZER_DP_HEALTH_ENFORCE", None)
        else:
            os.environ["EXTREMIZER_DP_HEALTH_ENFORCE"] = self.old_enforce
        self.tmp.cleanup()

    def test_unknown_is_fail_safe_blocked(self) -> None:
        health = dp_live_health.get_health(self.db)
        self.assertEqual(health["status"], "UNKNOWN")
        self.assertEqual(health["effective_status"], "TECHNICAL_ERROR")
        self.assertFalse(health["live_allowed"])

    def test_ready_allows_live(self) -> None:
        transition = dp_live_health.set_health(
            self.db,
            "READY",
            detail="authorized_session_ready",
        )
        self.assertTrue(transition["changed"])
        health = dp_live_health.get_health(self.db)
        self.assertEqual(health["effective_status"], "READY")
        self.assertTrue(health["live_allowed"])

    def test_problem_status_blocks_live(self) -> None:
        for status in (
            "CLOUDFLARE",
            "AUTH_REQUIRED",
            "BROWSER_DOWN",
            "TECHNICAL_ERROR",
        ):
            dp_live_health.set_health(self.db, status, detail=status.lower())
            health = dp_live_health.get_health(self.db)
            self.assertEqual(health["effective_status"], status)
            self.assertFalse(health["live_allowed"])

    def test_same_status_is_not_a_transition(self) -> None:
        first = dp_live_health.set_health(self.db, "CLOUDFLARE")
        second = dp_live_health.set_health(self.db, "CLOUDFLARE")
        self.assertTrue(first["changed"])
        self.assertFalse(second["changed"])
        with sqlite3.connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM dp_live_health_events"
            ).fetchone()[0]
        self.assertEqual(count, 1)

    def test_legacy_local_status_mapping(self) -> None:
        path = self.root / "dp_sync_agent_status.json"

        path.write_text(
            json.dumps({
                "status": "online",
                "updated_at": dp_live_health._now(),
                "detail": "idle",
            }),
            encoding="utf-8",
        )
        self.assertEqual(
            dp_live_health.read_local_health(path)["effective_status"],
            "READY",
        )

        path.write_text(
            json.dumps({
                "status": "human_required",
                "updated_at": dp_live_health._now(),
                "detail": "AUTH_REQUIRED",
            }),
            encoding="utf-8",
        )
        self.assertEqual(
            dp_live_health.read_local_health(path)["effective_status"],
            "AUTH_REQUIRED",
        )

        path.write_text(
            json.dumps({
                "status": "offline",
                "updated_at": dp_live_health._now(),
                "detail": "http_500",
            }),
            encoding="utf-8",
        )
        self.assertEqual(
            dp_live_health.read_local_health(path)["effective_status"],
            "TECHNICAL_ERROR",
        )

    def test_bridge_guard_blocks_before_queue_creation(self) -> None:
        os.environ["EXTREMIZER_DP_HEALTH_ENFORCE"] = "1"
        dp_live_health.set_health(self.db, "CLOUDFLARE")

        result = dp_live_bridge.request_live_dp(
            self.db,
            "Ski-Doo",
            "420956123",
            wait_seconds=0,
        )
        self.assertEqual(result["status"], "CLOUDFLARE")

        with sqlite3.connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM dp_live_requests"
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_bridge_guard_ready_keeps_existing_queue_contract(self) -> None:
        os.environ["EXTREMIZER_DP_HEALTH_ENFORCE"] = "1"
        dp_live_health.set_health(self.db, "READY")

        result = dp_live_bridge.request_live_dp(
            self.db,
            "Ski-Doo",
            "420956123",
            wait_seconds=0,
        )
        self.assertEqual(result["status"], "TIMEOUT")

        with sqlite3.connect(self.db) as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM dp_live_requests"
            ).fetchone()[0]
        self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()
