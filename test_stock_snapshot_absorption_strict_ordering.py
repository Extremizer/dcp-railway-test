#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Release Control regression: snapshot absorption must be strictly newer."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import stock_engine
import warehouse_store


EVENT_AT = "2026-10-11T12:00:01+00:00"
SNAPSHOTS = {
    "older": "2026-10-11T12:00:00+00:00",
    "equal": "2026-10-11T12:00:01+00:00",
    "newer": "2026-10-11T12:00:02+00:00",
}


class StrictSnapshotAbsorptionRegression(unittest.TestCase):
    def make_db(self, status: str):
        tmp = Path(tempfile.mkdtemp(prefix="strict_absorption_"))
        db = tmp / "stock.db"
        warehouse_store.init_warehouse_db(db)
        wid = warehouse_store.add_warehouse(
            "STRICT",
            "Moscow",
            f"STRICT-{status}",
            f"Strict {status}",
            priority=1,
            db_file=db,
        )
        self.assertTrue(
            stock_engine.set_stock_sync_mode(
                wid, "snapshot_absorbs_committed", db_file=db
            )
        )
        with sqlite3.connect(db) as c:
            cur = c.execute(
                """INSERT INTO warehouse_stock_reservations(
                     warehouse_id,order_id,order_item_id,manufacturer,oem,
                     quantity,status,created_at,committed_at,fulfilled_at
                   )
                   VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    wid,
                    f"ORDER-{status}",
                    100 if status == "committed" else 200,
                    "BRP",
                    "OEM-STRICT",
                    2.0,
                    status,
                    EVENT_AT,
                    EVENT_AT if status == "committed" else None,
                    EVENT_AT if status == "fulfilled" else None,
                ),
            )
            reservation_id = int(cur.lastrowid)
        return tmp, db, wid, reservation_id

    def state(self, db, reservation_id):
        with sqlite3.connect(db) as c:
            c.row_factory = sqlite3.Row
            row = c.execute(
                """SELECT * FROM warehouse_stock_reservations
                   WHERE id=?""",
                (reservation_id,),
            ).fetchone()
            events = c.execute(
                """SELECT event_type FROM warehouse_stock_reservation_events
                   WHERE reservation_id=?
                   ORDER BY id""",
                (reservation_id,),
            ).fetchall()
        return dict(row), [x[0] for x in events]

    def blocks(self, row, snapshot_at):
        snapshot_dt = datetime.fromisoformat(snapshot_at)
        now = datetime(2026, 10, 11, 12, 1, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory():
            # _reservation_blocks is the canonical availability predicate.
            return stock_engine._reservation_blocks(
                row,
                now=now,
                snapshot_observed_at=snapshot_dt,
                sync_mode="snapshot_absorbs_committed",
            )

    def run_case(self, *, path_name, status, relation):
        _tmp, db, wid, reservation_id = self.make_db(status)
        snapshot_at = SNAPSHOTS[relation]

        before, before_events = self.state(db, reservation_id)
        self.assertEqual(before["status"], status)
        self.assertEqual(before_events, [])
        self.assertEqual(
            self.blocks(before, snapshot_at),
            relation in {"older", "equal"},
            f"pre-absorption blocking mismatch: {path_name}/{status}/{relation}",
        )

        if path_name == "stock_engine.reconcile_snapshot":
            changed = stock_engine.reconcile_snapshot(
                wid,
                snapshot_at,
                oems=["OEM-STRICT"],
                db_file=db,
            )
        elif path_name == "warehouse_store._absorb_committed_after_snapshot":
            with sqlite3.connect(db) as c:
                changed = warehouse_store._absorb_committed_after_snapshot(
                    c,
                    wid,
                    snapshot_at,
                    oems=["OEM-STRICT"],
                )
                c.commit()
        else:
            raise AssertionError(path_name)

        after, events = self.state(db, reservation_id)
        should_absorb = relation == "newer"

        self.assertEqual(
            changed,
            1 if should_absorb else 0,
            f"absorption count mismatch: {path_name}/{status}/{relation}",
        )
        self.assertEqual(
            after["status"],
            "absorbed" if should_absorb else status,
            f"status mismatch: {path_name}/{status}/{relation}",
        )
        self.assertEqual(
            events,
            ["absorbed_by_snapshot"] if should_absorb else [],
            f"history mismatch: {path_name}/{status}/{relation}",
        )
        self.assertEqual(
            self.blocks(after, snapshot_at),
            False if should_absorb else True,
            f"post-absorption blocking mismatch: {path_name}/{status}/{relation}",
        )

    def test_stock_engine_reconcile_snapshot_strict_ordering(self):
        path_name = "stock_engine.reconcile_snapshot"
        for status in ("committed", "fulfilled"):
            for relation in ("older", "equal", "newer"):
                with self.subTest(
                    path=path_name, status=status, relation=relation
                ):
                    self.run_case(
                        path_name=path_name,
                        status=status,
                        relation=relation,
                    )

    def test_warehouse_store_absorption_strict_ordering(self):
        path_name = "warehouse_store._absorb_committed_after_snapshot"
        for status in ("committed", "fulfilled"):
            for relation in ("older", "equal", "newer"):
                with self.subTest(
                    path=path_name, status=status, relation=relation
                ):
                    self.run_case(
                        path_name=path_name,
                        status=status,
                        relation=relation,
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
