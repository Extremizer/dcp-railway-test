import json
import math
import os
import sqlite3
from pathlib import Path


def _load_json_env(name, default):
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    return json.loads(raw)


def seed_persistent_cache():
    db_path = Path(
        os.getenv(
            "EXTREMIZER_ORDERS_DB_FILE",
            "/data/extremizer_orders.db",
        )
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    preexisted = db_path.exists()

    oem_seed = _load_json_env("EXTREMIZER_OEM_SEED_JSON", [])
    dp_seed = _load_json_env("EXTREMIZER_DP_SEED_JSON", [])

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oem_catalog_cache (
                manufacturer TEXT NOT NULL,
                current_oem TEXT NOT NULL,
                name TEXT,
                catalog TEXT,
                msrp_usd REAL,
                msrp_verified INTEGER NOT NULL DEFAULT 0,
                previous_oems_json TEXT NOT NULL DEFAULT '[]',
                source_kind TEXT,
                source_ref TEXT,
                first_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                PRIMARY KEY (manufacturer, current_oem)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oem_catalog_aliases (
                manufacturer TEXT NOT NULL,
                alias_oem TEXT NOT NULL,
                current_oem TEXT NOT NULL,
                alias_kind TEXT NOT NULL,
                PRIMARY KEY (manufacturer, alias_oem)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS dealer_price_cache (
                manufacturer TEXT NOT NULL,
                oem TEXT NOT NULL,
                dealer_price_usd REAL NOT NULL,
                source TEXT,
                first_seen_at TEXT NOT NULL,
                last_verified_at TEXT NOT NULL,
                last_used_at TEXT,
                use_count INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (manufacturer, oem)
            )
            """
        )

        row = conn.execute(
            "SELECT value FROM cache_meta WHERE key='boot_count'"
        ).fetchone()
        boot_count = int(row[0]) + 1 if row else 1
        conn.execute(
            """
            INSERT INTO cache_meta(key, value)
            VALUES('boot_count', ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """,
            (str(boot_count),),
        )

        for entry in oem_seed:
            manufacturer = str(entry.get("manufacturer") or "").strip()
            current_oem = str(entry.get("current_oem") or "").strip()
            msrp = entry.get("msrp_usd")
            verified_at = str(entry.get("last_verified_at") or "").strip()
            if not manufacturer or not current_oem or not verified_at:
                continue
            if not isinstance(msrp, (int, float)) or float(msrp) <= 0:
                continue

            previous = [
                str(x).strip()
                for x in (entry.get("previous_oems") or [])
                if str(x).strip() and str(x).strip() != current_oem
            ]
            conn.execute(
                """
                INSERT INTO oem_catalog_cache (
                    manufacturer,current_oem,name,catalog,msrp_usd,
                    msrp_verified,previous_oems_json,source_kind,
                    source_ref,first_seen_at,last_verified_at
                )
                VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?)
                ON CONFLICT(manufacturer,current_oem) DO UPDATE SET
                    name=excluded.name,
                    catalog=excluded.catalog,
                    msrp_usd=excluded.msrp_usd,
                    msrp_verified=1,
                    previous_oems_json=excluded.previous_oems_json,
                    source_kind=excluded.source_kind,
                    source_ref=excluded.source_ref,
                    last_verified_at=excluded.last_verified_at
                """,
                (
                    manufacturer,
                    current_oem,
                    str(entry.get("name") or "") or None,
                    str(entry.get("catalog") or "Parts"),
                    float(msrp),
                    json.dumps(previous, ensure_ascii=False),
                    str(entry.get("source_kind") or "seed"),
                    str(entry.get("source_ref") or "") or None,
                    verified_at,
                    verified_at,
                ),
            )

            aliases = [(current_oem, "current")]
            aliases.extend((value, "previous") for value in previous)
            for alias_oem, alias_kind in aliases:
                conn.execute(
                    """
                    INSERT INTO oem_catalog_aliases(
                        manufacturer,alias_oem,current_oem,alias_kind
                    )
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(manufacturer,alias_oem) DO UPDATE SET
                        current_oem=excluded.current_oem,
                        alias_kind=excluded.alias_kind
                    """,
                    (
                        manufacturer,
                        alias_oem,
                        current_oem,
                        alias_kind,
                    ),
                )

        for entry in dp_seed:
            manufacturer = str(entry.get("manufacturer") or "").strip()
            oem = str(entry.get("oem") or "").strip()
            dp = entry.get("dealer_price_usd")
            verified_at = str(entry.get("last_verified_at") or "").strip()
            if not manufacturer or not oem or not verified_at:
                continue
            if not isinstance(dp, (int, float)) or float(dp) <= 0:
                continue
            conn.execute(
                """
                INSERT INTO dealer_price_cache(
                    manufacturer,oem,dealer_price_usd,source,
                    first_seen_at,last_verified_at,last_used_at,use_count
                )
                VALUES (?, ?, ?, ?, ?, ?, NULL, 0)
                ON CONFLICT(manufacturer,oem) DO UPDATE SET
                    dealer_price_usd=excluded.dealer_price_usd,
                    source=excluded.source,
                    last_verified_at=excluded.last_verified_at
                """,
                (
                    manufacturer,
                    oem,
                    float(dp),
                    str(entry.get("source") or "") or None,
                    verified_at,
                    verified_at,
                ),
            )

        conn.commit()

        oem_count = conn.execute(
            "SELECT COUNT(*) FROM oem_catalog_cache"
        ).fetchone()[0]
        dp_count = conn.execute(
            "SELECT COUNT(*) FROM dealer_price_cache"
        ).fetchone()[0]
        control = conn.execute(
            """
            SELECT o.msrp_usd, d.dealer_price_usd
            FROM oem_catalog_cache o
            JOIN dealer_price_cache d
              ON d.manufacturer=o.manufacturer
             AND d.oem=o.current_oem
            WHERE o.manufacturer='Ski-Doo'
              AND o.current_oem='417224332'
            """
        ).fetchone()

    rate = float(os.getenv("EXTREMIZER_USD_RUB_RATE", "105"))
    coefficient = float(
        os.getenv("EXTREMIZER_PRICE_COEFFICIENT", "1.34")
    )
    control_rub = None
    if control:
        control_rub = int(
            math.ceil(
                (float(control[1]) * coefficient * rate) / 50.0
            )
            * 50
        )

    print(
        "PERSISTENT_CACHE_READY",
        f"path={db_path}",
        f"preexisted={preexisted}",
        f"boot_count={boot_count}",
        f"oem_rows={oem_count}",
        f"dp_rows={dp_count}",
        f"control_rub={control_rub}",
        flush=True,
    )

    return {
        "path": str(db_path),
        "preexisted": preexisted,
        "boot_count": boot_count,
        "oem_rows": oem_count,
        "dp_rows": dp_count,
        "control_rub": control_rub,
    }
