"""Safe OEMixiBOT SQLite migration rehearsal helpers.

Never mutates the source DB. Uses SQLite Online Backup for a transactionally
consistent snapshot, including committed WAL content visible to SQLite.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Any


@dataclass(frozen=True)
class MigrationAudit:
    source: str
    destination: str
    source_journal_mode: str
    source_wal_exists: bool
    source_shm_exists: bool
    tables: tuple[str, ...]
    row_counts: dict[str, int]
    finance_ledger_sha256: str
    dealer_balances: dict[str, str]


def _connect_ro(path: Path | str) -> sqlite3.Connection:
    p = Path(path).resolve()
    conn = sqlite3.connect(p.as_uri() + "?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
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


def _row_counts(conn: sqlite3.Connection, tables: list[str]) -> dict[str, int]:
    return {
        table: int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        for table in tables
    }


def _ledger_digest(conn: sqlite3.Connection) -> str:
    tables = set(_tables(conn))
    if "finance_ledger" not in tables:
        return hashlib.sha256(b"").hexdigest()
    cols = [
        str(r[1])
        for r in conn.execute("PRAGMA table_info(finance_ledger)").fetchall()
    ]
    if not cols:
        return hashlib.sha256(b"").hexdigest()
    order = "id" if "id" in cols else "rowid"
    h = hashlib.sha256()
    for row in conn.execute(f"SELECT * FROM finance_ledger ORDER BY {order}"):
        payload = json.dumps(
            [row[c] for c in cols],
            ensure_ascii=False,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
        h.update(payload)
        h.update(b"\n")
    return h.hexdigest()


def _dealer_balances(conn: sqlite3.Connection) -> dict[str, str]:
    tables = set(_tables(conn))
    if "finance_ledger" not in tables:
        return {}
    rows = conn.execute(
        """SELECT buyer_id, amount FROM finance_ledger
           WHERE buyer_type='dealer' AND wallet_currency='USD'
           ORDER BY id"""
    ).fetchall()
    sums: dict[str, Decimal] = {}
    for row in rows:
        key = str(row["buyer_id"])
        sums[key] = sums.get(key, Decimal("0")) + Decimal(str(row["amount"]))
    return {k: format(v.quantize(Decimal("0.01")), "f") for k, v in sorted(sums.items())}


def audit_database(path: Path | str) -> MigrationAudit:
    p = Path(path).resolve()
    with _connect_ro(p) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        if str(integrity).lower() != "ok":
            raise ValueError(f"integrity_check_failed:{integrity}")
        fk = conn.execute("PRAGMA foreign_key_check").fetchall()
        if fk:
            raise ValueError(f"foreign_key_check_failed:{len(fk)}")
        tables = _tables(conn)
        journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0])
        return MigrationAudit(
            source=str(p),
            destination=str(p),
            source_journal_mode=journal_mode,
            source_wal_exists=Path(str(p) + "-wal").exists(),
            source_shm_exists=Path(str(p) + "-shm").exists(),
            tables=tuple(tables),
            row_counts=_row_counts(conn, tables),
            finance_ledger_sha256=_ledger_digest(conn),
            dealer_balances=_dealer_balances(conn),
        )


def backup_database(source: Path | str, destination: Path | str) -> MigrationAudit:
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    if src == dst:
        raise ValueError("source_and_destination_must_differ")
    if not src.exists():
        raise FileNotFoundError(src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        raise FileExistsError(dst)

    source_audit = audit_database(src)

    source_uri = src.as_uri() + "?mode=ro"
    with sqlite3.connect(source_uri, uri=True, timeout=10) as source_conn:
        with sqlite3.connect(dst, timeout=10) as dest_conn:
            source_conn.backup(dest_conn)
            dest_conn.execute("PRAGMA foreign_keys=ON")
            dest_conn.commit()

    dest_audit = audit_database(dst)
    if source_audit.tables != dest_audit.tables:
        raise ValueError("migration_table_set_mismatch")
    if source_audit.row_counts != dest_audit.row_counts:
        raise ValueError("migration_row_count_mismatch")
    if source_audit.finance_ledger_sha256 != dest_audit.finance_ledger_sha256:
        raise ValueError("migration_finance_ledger_mismatch")
    if source_audit.dealer_balances != dest_audit.dealer_balances:
        raise ValueError("migration_dealer_balance_mismatch")

    return MigrationAudit(
        source=str(src),
        destination=str(dst),
        source_journal_mode=source_audit.source_journal_mode,
        source_wal_exists=source_audit.source_wal_exists,
        source_shm_exists=source_audit.source_shm_exists,
        tables=source_audit.tables,
        row_counts=source_audit.row_counts,
        finance_ledger_sha256=source_audit.finance_ledger_sha256,
        dealer_balances=source_audit.dealer_balances,
    )


def backup_bundle(
    *,
    oemixibot_db: Path | str,
    identity_db: Path | str,
    reference_db: Path | str,
    destination_dir: Path | str,
) -> dict[str, MigrationAudit]:
    """Create a verified three-database Railway volume candidate.

    All sources are opened read-only. Existing destination files are refused.
    """
    dest = Path(destination_dir).resolve()
    dest.mkdir(parents=True, exist_ok=True)
    mapping = {
        "oemixibot.db": Path(oemixibot_db),
        "oemixibot_identity.db": Path(identity_db),
        "oem_reference.db": Path(reference_db),
    }
    missing = [name for name, src in mapping.items() if not src.exists()]
    if missing:
        raise FileNotFoundError("missing_bundle_sources:" + ",".join(sorted(missing)))
    return {
        name: backup_database(src, dest / name)
        for name, src in mapping.items()
    }


__all__ = ["MigrationAudit", "audit_database", "backup_database", "backup_bundle"]
