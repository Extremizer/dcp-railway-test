#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import asyncio
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import warehouse_store
import warehouse_stock_service
import stock_engine

DEFAULT_DB = warehouse_store.DEFAULT_DB
DEFAULT_LOOP_SECONDS = 60
RESERVED_REFRESH_MINUTES = 10
MAX_TARGETS_PER_CYCLE = 24
MAX_CONCURRENCY = 3


def _now() -> datetime:
    return datetime.now().astimezone()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _website_enabled(
    conn: sqlite3.Connection,
    warehouse_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT enabled
        FROM warehouse_sources
        WHERE warehouse_id = ?
          AND source_type = 'website'
        """,
        (warehouse_id,),
    ).fetchone()
    return bool(row and row[0])


def get_due_targets(
    db_file: Path | str = DEFAULT_DB,
    limit: int = MAX_TARGETS_PER_CYCLE,
) -> list[dict[str, Any]]:
    warehouse_store.init_warehouse_db(db_file)
    now = _now()
    reserved_cutoff = now - timedelta(
        minutes=RESERVED_REFRESH_MINUTES
    )

    targets: dict[tuple[int, str], dict[str, Any]] = {}

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row

        reservations = conn.execute(
            """
            SELECT
                r.warehouse_id,
                r.oem,
                r.status,
                MAX(c.observed_at) AS website_observed_at
            FROM warehouse_stock_reservations r
            LEFT JOIN warehouse_stock_current c
              ON c.warehouse_id = r.warehouse_id
             AND c.oem = r.oem
             AND c.source_type = 'website'
            WHERE r.status IN ('hold', 'reserved', 'committed')
            GROUP BY r.warehouse_id, r.oem, r.status
            ORDER BY
                CASE r.status
                    WHEN 'committed' THEN 0
                    WHEN 'reserved' THEN 1
                    ELSE 2
                END,
                r.created_at ASC
            """
        ).fetchall()

        for row in reservations:
            warehouse_id = int(row["warehouse_id"])
            oem = str(row["oem"] or "").strip()
            if not oem or not _website_enabled(conn, warehouse_id):
                continue

            observed = _parse_dt(row["website_observed_at"])
            if observed is not None and observed > reserved_cutoff:
                continue

            targets[(warehouse_id, oem)] = {
                "warehouse_id": warehouse_id,
                "oem": oem,
                "reason": f"reservation:{row['status']}",
                "priority": 0,
            }

        stale_rows = conn.execute(
            """
            SELECT
                c.warehouse_id,
                c.oem,
                c.expires_at,
                c.observed_at,
                c.quantity,
                c.price_rub,
                w.adapter_type,
                s.priority AS source_priority
            FROM warehouse_stock_current c
            JOIN warehouse_sources s
              ON s.warehouse_id = c.warehouse_id
             AND s.source_type = c.source_type
            JOIN warehouses w
              ON w.id = c.warehouse_id
            WHERE c.source_type = 'website'
              AND s.enabled = 1
              AND w.active = 1
              AND w.deleted_at IS NULL
            ORDER BY c.expires_at ASC, c.observed_at ASC
            """
        ).fetchall()

        for row in stale_rows:
            expires = _parse_dt(row["expires_at"])
            missing_published_price = (
                str(row["adapter_type"] or "") == "motoservice76"
                and row["quantity"] is not None
                and float(row["quantity"]) > 0
                and row["price_rub"] is None
            )
            if expires is not None and expires > now and not missing_published_price:
                continue

            warehouse_id = int(row["warehouse_id"])
            oem = str(row["oem"] or "").strip()
            if not oem:
                continue

            targets.setdefault(
                (warehouse_id, oem),
                {
                    "warehouse_id": warehouse_id,
                    "oem": oem,
                    "reason": (
                        "price_missing"
                        if missing_published_price
                        else "ttl_expired"
                    ),
                    "priority": 5 if missing_published_price else 10,
                },
            )

    ordered = sorted(
        targets.values(),
        key=lambda item: (item["priority"], item["warehouse_id"], item["oem"]),
    )
    return ordered[: max(1, int(limit))]


async def refresh_due_once(
    db_file: Path | str = DEFAULT_DB,
    log=None,
    limit: int = MAX_TARGETS_PER_CYCLE,
) -> dict[str, int]:
    expired_holds = stock_engine.cleanup_expired_holds(db_file)

    targets = get_due_targets(db_file, limit)
    if not targets:
        return {
            "selected": 0,
            "ok": 0,
            "failed": 0,
            "expired_holds": expired_holds,
        }

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def one(target: dict[str, Any]):
        async with semaphore:
            result = await asyncio.to_thread(
                warehouse_stock_service.refresh_warehouse_oem,
                int(target["warehouse_id"]),
                str(target["oem"]),
                db_file,
            )
            return target, result

    results = await asyncio.gather(
        *(one(target) for target in targets),
        return_exceptions=True,
    )

    ok = 0
    failed = 0
    for item in results:
        if isinstance(item, Exception):
            failed += 1
            if log:
                log.warning(
                    "Warehouse refresh task crashed: %s",
                    item,
                )
            continue

        target, result = item
        if result.status == "check_failed":
            failed += 1
            if log:
                log.info(
                    "Warehouse refresh failed: warehouse=%s oem=%s reason=%s",
                    target["warehouse_id"],
                    target["oem"],
                    target["reason"],
                )
        else:
            ok += 1

    return {
        "selected": len(targets),
        "ok": ok,
        "failed": failed,
        "expired_holds": expired_holds,
    }


async def run_refresh_loop(
    db_file: Path | str = DEFAULT_DB,
    log=None,
    interval_seconds: int = DEFAULT_LOOP_SECONDS,
) -> None:
    if log:
        log.info(
            "Warehouse refresh scheduler started: interval=%ss",
            interval_seconds,
        )

    try:
        while True:
            try:
                stats = await refresh_due_once(
                    db_file=db_file,
                    log=log,
                )
                if log and (
                    stats["selected"] or stats.get("expired_holds")
                ):
                    log.info(
                        "Warehouse refresh cycle: selected=%s ok=%s "
                        "failed=%s expired_holds=%s",
                        stats["selected"],
                        stats["ok"],
                        stats["failed"],
                        stats.get("expired_holds", 0),
                    )
            except Exception:
                if log:
                    log.exception(
                        "Warehouse refresh cycle failed"
                    )

            await asyncio.sleep(max(15, int(interval_seconds)))
    except asyncio.CancelledError:
        if log:
            log.info("Warehouse refresh scheduler stopped")
        raise
