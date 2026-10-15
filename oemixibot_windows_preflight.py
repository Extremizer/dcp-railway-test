"""Read-only preflight for the three live OEMixiBOT SQLite databases.

This tool never creates, migrates, vacuums, checkpoints, or modifies a source.
It opens each source with SQLite URI mode=ro and query_only=ON.
"""
from __future__ import annotations

import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


def _connect_ro(path: Path) -> sqlite3.Connection:
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _tables(conn: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            """SELECT name FROM sqlite_master
               WHERE type='table' AND name NOT LIKE 'sqlite_%'
               ORDER BY name"""
        ).fetchall()
    ]


def _counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {
        t: int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0])
        for t in tables
    }


def _ledger_digest(conn: sqlite3.Connection, tables: set[str]) -> str | None:
    if "finance_ledger" not in tables:
        return None
    cols = [str(r[1]) for r in conn.execute("PRAGMA table_info(finance_ledger)")]
    h = hashlib.sha256()
    for row in conn.execute("SELECT * FROM finance_ledger ORDER BY id"):
        h.update(
            json.dumps(
                [row[c] for c in cols],
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        h.update(b"\n")
    return h.hexdigest()


def _dealer_balances(conn: sqlite3.Connection, tables: set[str]) -> dict[str, str]:
    if "finance_ledger" not in tables:
        return {}
    out: dict[str, Decimal] = {}
    for row in conn.execute(
        """SELECT buyer_id,amount FROM finance_ledger
           WHERE buyer_type='dealer' AND wallet_currency='USD'
           ORDER BY id"""
    ):
        key = str(row["buyer_id"])
        out[key] = out.get(key, Decimal("0")) + Decimal(str(row["amount"]))
    return {k: format(v.quantize(Decimal("0.01")), "f") for k, v in sorted(out.items())}


def inspect(path: str | Path) -> dict[str, Any]:
    p = Path(path).resolve()
    with _connect_ro(p) as conn:
        tables = _tables(conn)
        integrity = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        result = {
            "path": str(p),
            "size_bytes": p.stat().st_size,
            "journal_mode": journal_mode,
            "wal_exists": Path(str(p) + "-wal").exists(),
            "shm_exists": Path(str(p) + "-shm").exists(),
            "integrity": integrity,
            "foreign_key_violations": len(fk),
            "tables": tables,
            "row_counts": _counts(conn, tables),
            "finance_ledger_sha256": _ledger_digest(conn, set(tables)),
            "dealer_balances_usd": _dealer_balances(conn, set(tables)),
        }
    if integrity.lower() != "ok":
        result["preflight_status"] = "BLOCKED_INTEGRITY"
    elif fk:
        result["preflight_status"] = "BLOCKED_FOREIGN_KEYS"
    else:
        result["preflight_status"] = "READ_ONLY_OK"
    return result


def inspect_bundle(*, main_db: str | Path, identity_db: str | Path, reference_db: str | Path):
    return {
        "main": inspect(main_db),
        "identity": inspect(identity_db),
        "reference": inspect(reference_db),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-db", required=True)
    parser.add_argument("--identity-db", required=True)
    parser.add_argument("--reference-db", required=True)
    args = parser.parse_args()
    result = inspect_bundle(
        main_db=args.main_db,
        identity_db=args.identity_db,
        reference_db=args.reference_db,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    blocked = any(
        item["preflight_status"] != "READ_ONLY_OK"
        for item in result.values()
    )
    return 2 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
