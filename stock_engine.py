#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Stock availability, holds and reservations for Extremizer Pro."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

import warehouse_store

DEFAULT_DB = warehouse_store.DEFAULT_DB

ACTIVE_STATUSES = {"hold", "reserved", "committed"}
RELEASABLE_STATUSES = {"hold", "reserved", "committed", "absorbed"}
SYNC_MODES = {"manual", "snapshot_absorbs_committed"}


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _log_event(
    conn: sqlite3.Connection,
    reservation_id: int,
    event_type: str,
    quantity: float | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO warehouse_stock_reservation_events (
            reservation_id, event_type, quantity, created_at, details_json
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            reservation_id,
            event_type,
            quantity,
            _now(),
            json.dumps(details, ensure_ascii=False, sort_keys=True)
            if details else None,
        ),
    )


def _warehouse_is_active(
    conn: sqlite3.Connection,
    warehouse_id: int,
) -> bool:
    row = conn.execute(
        """
        SELECT active, deleted_at
        FROM warehouses
        WHERE id = ?
        """,
        (warehouse_id,),
    ).fetchone()
    return bool(
        row
        and int(row[0] or 0) == 1
        and row[1] is None
    )


def _warehouse_sync_mode(
    conn: sqlite3.Connection,
    warehouse_id: int,
) -> str:
    row = conn.execute(
        "SELECT stock_sync_mode FROM warehouses WHERE id = ?",
        (warehouse_id,),
    ).fetchone()
    if not row or row[0] not in SYNC_MODES:
        return "manual"
    return str(row[0])


def set_stock_sync_mode(
    warehouse_id: int,
    mode: str,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    if mode not in SYNC_MODES:
        return False
    warehouse_store.init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        cur = conn.execute(
            """
            UPDATE warehouses
            SET stock_sync_mode = ?, updated_at = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (mode, _now(), warehouse_id),
        )
        conn.commit()
        return cur.rowcount == 1


def _best_snapshot(
    conn: sqlite3.Connection,
    warehouse_id: int,
    oem: str,
    now: datetime,
) -> dict[str, Any] | None:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            c.*,
            s.priority AS source_priority,
            s.enabled AS source_enabled
        FROM warehouse_stock_current c
        JOIN warehouse_sources s
          ON s.warehouse_id = c.warehouse_id
         AND s.source_type = c.source_type
        WHERE c.warehouse_id = ?
          AND c.oem = ?
          AND s.enabled = 1
        ORDER BY s.priority ASC, c.observed_at DESC
        """,
        (warehouse_id, oem.strip()),
    ).fetchall()

    if not rows:
        return None

    fresh: list[sqlite3.Row] = []
    stale: list[sqlite3.Row] = []
    for row in rows:
        expires = _parse_dt(row["expires_at"])
        if expires is None or expires > now:
            fresh.append(row)
        else:
            stale.append(row)

    def rank(row: sqlite3.Row):
        status = str(row["status"] or "")
        quantity = row["quantity"]
        if quantity is not None and status in {"in_stock", "out_of_stock"}:
            info_rank = 0
        elif status == "quantity_unknown":
            info_rank = 1
        elif status == "not_found":
            info_rank = 2
        else:
            info_rank = 3

        observed = _parse_dt(row["observed_at"])
        observed_ts = observed.timestamp() if observed else 0.0
        return (
            info_rank,
            int(row["source_priority"] or 999),
            -observed_ts,
        )

    pool = fresh if fresh else stale
    chosen = min(pool, key=rank)
    result = dict(chosen)
    result["is_fresh"] = bool(fresh)
    return result



def _best_price_snapshot(
    conn: sqlite3.Connection,
    warehouse_id: int,
    oem: str,
    now: datetime,
) -> dict[str, Any] | None:
    """Choose price independently from stock quantity/status.

    A fresh higher-priority source with stock data but no price must not erase
    a fresh price supplied by another enabled source. Stale prices are never
    exposed to the customer.
    """
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            c.*,
            s.priority AS source_priority
        FROM warehouse_stock_current c
        JOIN warehouse_sources s
          ON s.warehouse_id = c.warehouse_id
         AND s.source_type = c.source_type
        WHERE c.warehouse_id = ?
          AND c.oem = ?
          AND s.enabled = 1
          AND c.price_rub IS NOT NULL
        ORDER BY s.priority ASC, c.observed_at DESC
        """,
        (warehouse_id, oem.strip()),
    ).fetchall()

    fresh = []
    for row in rows:
        expires = _parse_dt(row["expires_at"])
        if expires is None or expires > now:
            fresh.append(row)

    if not fresh:
        return None

    chosen = min(
        fresh,
        key=lambda row: (
            int(row["source_priority"] or 999),
            -((_parse_dt(row["observed_at"]) or datetime.min.replace(tzinfo=now.tzinfo)).timestamp()),
        ),
    )
    return dict(chosen)


def _reservation_blocks(
    row: sqlite3.Row,
    now: datetime,
    snapshot_observed_at: datetime | None,
    sync_mode: str,
) -> bool:
    status = row["status"]
    if status == "hold":
        expires = _parse_dt(row["expires_at"])
        return expires is None or expires > now

    if status == "reserved":
        return True

    if status != "committed":
        return False

    if sync_mode == "manual":
        return True

    committed_at = _parse_dt(row["committed_at"])
    if committed_at is None or snapshot_observed_at is None:
        return True

    return committed_at > snapshot_observed_at


def _blocking_quantity(
    conn: sqlite3.Connection,
    warehouse_id: int,
    oem: str,
    snapshot_observed_at: datetime | None,
    now: datetime,
) -> tuple[float, list[dict[str, Any]]]:
    sync_mode = _warehouse_sync_mode(conn, warehouse_id)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT *
        FROM warehouse_stock_reservations
        WHERE warehouse_id = ?
          AND oem = ?
          AND status IN ('hold', 'reserved', 'committed')
        ORDER BY created_at, id
        """,
        (warehouse_id, oem.strip()),
    ).fetchall()

    blocked = 0.0
    active: list[dict[str, Any]] = []
    for row in rows:
        if _reservation_blocks(
            row,
            now=now,
            snapshot_observed_at=snapshot_observed_at,
            sync_mode=sync_mode,
        ):
            blocked += float(row["quantity"])
            active.append(dict(row))

    return blocked, active


def get_available_stock(
    warehouse_id: int,
    oem: str,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any]:
    warehouse_store.init_warehouse_db(db_file)
    now = _now_dt()

    with sqlite3.connect(db_file) as conn:
        snapshot = _best_snapshot(conn, warehouse_id, oem, now)
        if snapshot is None:
            return {
                "warehouse_id": warehouse_id,
                "oem": oem.strip(),
                "status": "unknown",
                "raw_quantity": None,
                "blocked_quantity": 0.0,
                "available_quantity": None,
                "source_type": None,
                "is_fresh": False,
                "observed_at": None,
            }

        price_snapshot = _best_price_snapshot(
            conn,
            warehouse_id,
            oem,
            now,
        )

        observed_at = _parse_dt(snapshot.get("observed_at"))
        blocked, reservations = _blocking_quantity(
            conn,
            warehouse_id,
            oem,
            snapshot_observed_at=observed_at,
            now=now,
        )

        raw = (
            float(snapshot["quantity"])
            if snapshot.get("quantity") is not None
            else None
        )
        if raw is None:
            available = None
            status = "unknown"
            deficit = 0.0
        else:
            calculated = raw - blocked
            available = max(calculated, 0.0)
            deficit = max(-calculated, 0.0)
            status = "in_stock" if available > 0 else "out_of_stock"

        return {
            "warehouse_id": warehouse_id,
            "oem": oem.strip(),
            "status": status,
            "raw_quantity": raw,
            "blocked_quantity": blocked,
            "available_quantity": available,
            "deficit_quantity": deficit,
            "price_rub": (
                float(price_snapshot["price_rub"])
                if price_snapshot is not None
                and price_snapshot.get("price_rub") is not None
                else None
            ),
            "price_source_type": (
                price_snapshot.get("source_type")
                if price_snapshot is not None
                else None
            ),
            "source_type": snapshot.get("source_type"),
            "is_fresh": bool(snapshot.get("is_fresh")),
            "observed_at": snapshot.get("observed_at"),
            "expires_at": snapshot.get("expires_at"),
            "reservations": reservations,
            "sync_mode": _warehouse_sync_mode(conn, warehouse_id),
        }


def _create_reservation(
    conn: sqlite3.Connection,
    warehouse_id: int,
    oem: str,
    quantity: float,
    status: str,
    order_id: str | None,
    order_item_id: int | None,
    manufacturer: str | None,
    expires_at: str | None,
    notes: str | None,
) -> int:
    now = _now()
    confirmed_at = now if status == "reserved" else None
    cur = conn.execute(
        """
        INSERT INTO warehouse_stock_reservations (
            warehouse_id, order_id, order_item_id, manufacturer, oem,
            quantity, status, created_at, expires_at, confirmed_at, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            warehouse_id,
            order_id,
            order_item_id,
            manufacturer,
            oem.strip(),
            float(quantity),
            status,
            now,
            expires_at,
            confirmed_at,
            notes,
        ),
    )
    reservation_id = int(cur.lastrowid)
    _log_event(
        conn,
        reservation_id,
        "created_" + status,
        quantity=float(quantity),
    )
    return reservation_id


def reserve_stock(
    warehouse_id: int,
    oem: str,
    quantity: float,
    order_id: str | None = None,
    order_item_id: int | None = None,
    manufacturer: str | None = None,
    notes: str | None = None,
    require_fresh: bool = True,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any]:
    if quantity <= 0:
        raise ValueError("Reservation quantity must be greater than zero.")

    warehouse_store.init_warehouse_db(db_file)
    now = _now_dt()

    with sqlite3.connect(db_file, timeout=30) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _warehouse_is_active(conn, warehouse_id):
            conn.rollback()
            return {
                "ok": False,
                "reason": "warehouse_inactive",
            }

        snapshot = _best_snapshot(conn, warehouse_id, oem, now)
        if snapshot is None:
            conn.rollback()
            return {"ok": False, "reason": "stock_unknown"}

        if require_fresh and not snapshot.get("is_fresh"):
            conn.rollback()
            return {"ok": False, "reason": "stock_stale"}

        blocked, _ = _blocking_quantity(
            conn,
            warehouse_id,
            oem,
            snapshot_observed_at=_parse_dt(snapshot.get("observed_at")),
            now=now,
        )
        raw = snapshot.get("quantity")
        if raw is None:
            conn.rollback()
            return {"ok": False, "reason": "stock_unknown"}

        available = max(float(raw) - blocked, 0.0)
        if available < float(quantity):
            conn.rollback()
            return {
                "ok": False,
                "reason": "insufficient_stock",
                "available_quantity": available,
            }

        reservation_id = _create_reservation(
            conn,
            warehouse_id=warehouse_id,
            oem=oem,
            quantity=quantity,
            status="reserved",
            order_id=order_id,
            order_item_id=order_item_id,
            manufacturer=manufacturer,
            expires_at=None,
            notes=notes,
        )
        conn.commit()

    result = get_available_stock(warehouse_id, oem, db_file)
    return {
        "ok": True,
        "reservation_id": reservation_id,
        "available_quantity": result["available_quantity"],
    }


def create_hold(
    warehouse_id: int,
    oem: str,
    quantity: float,
    hold_minutes: int = 30,
    order_id: str | None = None,
    order_item_id: int | None = None,
    manufacturer: str | None = None,
    notes: str | None = None,
    require_fresh: bool = True,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any]:
    if quantity <= 0:
        raise ValueError("Hold quantity must be greater than zero.")
    if hold_minutes <= 0:
        raise ValueError("Hold duration must be greater than zero.")

    warehouse_store.init_warehouse_db(db_file)
    now = _now_dt()
    expires_at = (
        now + timedelta(minutes=int(hold_minutes))
    ).isoformat(timespec="seconds")

    with sqlite3.connect(db_file, timeout=30) as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not _warehouse_is_active(conn, warehouse_id):
            conn.rollback()
            return {
                "ok": False,
                "reason": "warehouse_inactive",
            }

        snapshot = _best_snapshot(conn, warehouse_id, oem, now)
        if snapshot is None:
            conn.rollback()
            return {"ok": False, "reason": "stock_unknown"}

        if require_fresh and not snapshot.get("is_fresh"):
            conn.rollback()
            return {"ok": False, "reason": "stock_stale"}

        blocked, _ = _blocking_quantity(
            conn,
            warehouse_id,
            oem,
            snapshot_observed_at=_parse_dt(snapshot.get("observed_at")),
            now=now,
        )
        raw = snapshot.get("quantity")
        if raw is None:
            conn.rollback()
            return {"ok": False, "reason": "stock_unknown"}

        available = max(float(raw) - blocked, 0.0)
        if available < float(quantity):
            conn.rollback()
            return {
                "ok": False,
                "reason": "insufficient_stock",
                "available_quantity": available,
            }

        reservation_id = _create_reservation(
            conn,
            warehouse_id=warehouse_id,
            oem=oem,
            quantity=quantity,
            status="hold",
            order_id=order_id,
            order_item_id=order_item_id,
            manufacturer=manufacturer,
            expires_at=expires_at,
            notes=notes,
        )
        conn.commit()

    result = get_available_stock(warehouse_id, oem, db_file)
    return {
        "ok": True,
        "reservation_id": reservation_id,
        "expires_at": expires_at,
        "available_quantity": result["available_quantity"],
    }


def confirm_hold(
    reservation_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    warehouse_store.init_warehouse_db(db_file)
    now = _now()
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            """
            SELECT status, expires_at, quantity
            FROM warehouse_stock_reservations
            WHERE id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if not row or row[0] != "hold":
            return False

        expires = _parse_dt(row[1])
        if expires is not None and expires <= _now_dt():
            conn.execute(
                """
                UPDATE warehouse_stock_reservations
                SET status = 'released', released_at = ?
                WHERE id = ?
                """,
                (now, reservation_id),
            )
            _log_event(conn, reservation_id, "hold_expired", float(row[2]))
            conn.commit()
            return False

        conn.execute(
            """
            UPDATE warehouse_stock_reservations
            SET status = 'reserved',
                expires_at = NULL,
                confirmed_at = ?
            WHERE id = ?
            """,
            (now, reservation_id),
        )
        _log_event(conn, reservation_id, "hold_confirmed", float(row[2]))
        conn.commit()
        return True


def commit_reservation(
    reservation_id: int,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    warehouse_store.init_warehouse_db(db_file)
    now = _now()
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            """
            SELECT status, quantity, expires_at
            FROM warehouse_stock_reservations
            WHERE id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if not row or row[0] not in {"reserved", "hold"}:
            return False

        if row[0] == "hold":
            expires = _parse_dt(row[2])
            if expires is not None and expires <= _now_dt():
                conn.execute(
                    """
                    UPDATE warehouse_stock_reservations
                    SET status = 'released', released_at = ?
                    WHERE id = ?
                    """,
                    (now, reservation_id),
                )
                _log_event(
                    conn,
                    reservation_id,
                    "hold_expired",
                    float(row[1]),
                )
                conn.commit()
                return False

        conn.execute(
            """
            UPDATE warehouse_stock_reservations
            SET status = 'committed',
                expires_at = NULL,
                committed_at = ?
            WHERE id = ?
            """,
            (now, reservation_id),
        )
        _log_event(conn, reservation_id, "committed", float(row[1]))
        conn.commit()
        return True


def release_reservation(
    reservation_id: int,
    reason: str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> bool:
    warehouse_store.init_warehouse_db(db_file)
    now = _now()
    with sqlite3.connect(db_file) as conn:
        row = conn.execute(
            """
            SELECT status, quantity
            FROM warehouse_stock_reservations
            WHERE id = ?
            """,
            (reservation_id,),
        ).fetchone()
        if not row or row[0] not in RELEASABLE_STATUSES:
            return False

        conn.execute(
            """
            UPDATE warehouse_stock_reservations
            SET status = 'released', released_at = ?
            WHERE id = ?
            """,
            (now, reservation_id),
        )
        _log_event(
            conn,
            reservation_id,
            "released",
            float(row[1]),
            {"reason": reason} if reason else None,
        )
        conn.commit()
        return True


def reconcile_snapshot(
    warehouse_id: int,
    observed_at: str,
    oems: Iterable[str] | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int:
    warehouse_store.init_warehouse_db(db_file)
    snapshot_dt = _parse_dt(observed_at)
    if snapshot_dt is None:
        return 0

    with sqlite3.connect(db_file) as conn:
        if _warehouse_sync_mode(conn, warehouse_id) != "snapshot_absorbs_committed":
            return 0

        params: list[Any] = [warehouse_id, observed_at]
        oem_clause = ""
        normalized = [str(x).strip() for x in (oems or []) if str(x).strip()]
        if normalized:
            placeholders = ",".join("?" for _ in normalized)
            oem_clause = f" AND oem IN ({placeholders})"
            params.extend(normalized)

        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            SELECT id, quantity
            FROM warehouse_stock_reservations
            WHERE warehouse_id = ?
              AND status = 'committed'
              AND committed_at IS NOT NULL
              AND committed_at <= ?
              {oem_clause}
            """,
            params,
        ).fetchall()

        now = _now()
        for row in rows:
            conn.execute(
                """
                UPDATE warehouse_stock_reservations
                SET status = 'absorbed', absorbed_at = ?
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            _log_event(
                conn,
                int(row["id"]),
                "absorbed_by_snapshot",
                float(row["quantity"]),
                {"snapshot_observed_at": observed_at},
            )

        conn.commit()
        return len(rows)


def cleanup_expired_holds(
    db_file: Path | str = DEFAULT_DB,
) -> int:
    warehouse_store.init_warehouse_db(db_file)
    now = _now()
    now_dt = _now_dt()

    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, quantity, expires_at
            FROM warehouse_stock_reservations
            WHERE status = 'hold'
              AND expires_at IS NOT NULL
            """
        ).fetchall()

        expired = [
            row for row in rows
            if (_parse_dt(row["expires_at"]) or now_dt) <= now_dt
        ]
        for row in expired:
            conn.execute(
                """
                UPDATE warehouse_stock_reservations
                SET status = 'released', released_at = ?
                WHERE id = ?
                """,
                (now, row["id"]),
            )
            _log_event(
                conn,
                int(row["id"]),
                "hold_expired",
                float(row["quantity"]),
            )

        conn.commit()
        return len(expired)


def release_order_reservations(
    order_id: str,
    reason: str | None = None,
    db_file: Path | str = DEFAULT_DB,
) -> int:
    reservations = list_order_reservations(order_id, db_file)
    released = 0
    for reservation in reservations:
        if reservation.get("status") not in RELEASABLE_STATUSES:
            continue
        if release_reservation(
            int(reservation["id"]),
            reason=reason,
            db_file=db_file,
        ):
            released += 1
    return released


def sync_order_reservations_for_status(
    order_id: str,
    order_status: str,
    db_file: Path | str = DEFAULT_DB,
) -> dict[str, Any]:
    """Synchronize warehouse reservations with an order lifecycle status.

    confirmed:
        No automatic warehouse choice is made. Expired holds are cleaned.
    executing:
        Existing hold/reserved rows are committed (sent to warehouse).
    completed:
        Same as executing, so no hold/reserved row is left dangling.
    cancelled:
        All releasable rows are released. Absorbed rows are marked released
        without changing supplier stock, so stock is never artificially added.
    """
    warehouse_store.init_warehouse_db(db_file)
    status = str(order_status or "").strip().lower()
    result: dict[str, Any] = {
        "order_id": order_id,
        "order_status": status,
        "expired_holds": cleanup_expired_holds(db_file),
        "committed": 0,
        "released": 0,
        "failed": 0,
        "active_before": 0,
        "active_after": 0,
        "statuses": {},
    }

    before = list_order_reservations(order_id, db_file)
    result["active_before"] = sum(
        1 for row in before
        if row.get("status") in ACTIVE_STATUSES
    )

    if status in {"executing", "completed"}:
        for row in before:
            if row.get("status") not in {"hold", "reserved"}:
                continue
            if commit_reservation(int(row["id"]), db_file):
                result["committed"] += 1
            else:
                result["failed"] += 1
    elif status == "cancelled":
        result["released"] = release_order_reservations(
            order_id,
            reason="Order status changed to cancelled",
            db_file=db_file,
        )

    after = list_order_reservations(order_id, db_file)
    result["active_after"] = sum(
        1 for row in after
        if row.get("status") in ACTIVE_STATUSES
    )
    counts: dict[str, int] = {}
    for row in after:
        key = str(row.get("status") or "unknown")
        counts[key] = counts.get(key, 0) + 1
    result["statuses"] = counts
    return result


def list_order_reservations(
    order_id: str,
    db_file: Path | str = DEFAULT_DB,
) -> list[dict[str, Any]]:
    warehouse_store.init_warehouse_db(db_file)
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT *
            FROM warehouse_stock_reservations
            WHERE order_id = ?
            ORDER BY created_at, id
            """,
            (order_id,),
        ).fetchall()
    return [dict(row) for row in rows]
