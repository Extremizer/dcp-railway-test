import json, os, sqlite3
from datetime import datetime, timezone
from pathlib import Path
DB=os.getenv("EXTREMIZER_ORDERS_DB_FILE","/data/extremizer_orders.db")
SEED=Path(__file__).with_name("dcp_snapshot_seed.json")
rows=json.loads(SEED.read_text(encoding="utf-8"))
os.makedirs(os.path.dirname(DB) or ".",exist_ok=True)
con=sqlite3.connect(DB)
con.execute("""CREATE TABLE IF NOT EXISTS dcp_catalog_snapshot(
 id INTEGER PRIMARY KEY,oem TEXT NOT NULL,name TEXT,brand TEXT,catalog_type TEXT NOT NULL,
 msrp_usd REAL,qoh INTEGER,qoh_is_current INTEGER NOT NULL DEFAULT 0,source TEXT NOT NULL,
 source_url TEXT,source_file TEXT,captured_at TEXT NOT NULL,imported_at TEXT NOT NULL,
 UNIQUE(oem,catalog_type,source_file,captured_at))""")
con.execute("CREATE INDEX IF NOT EXISTS idx_dcp_snapshot_oem ON dcp_catalog_snapshot(oem)")
now=datetime.now(timezone.utc).isoformat()
for r in rows:
 con.execute("""INSERT OR IGNORE INTO dcp_catalog_snapshot
 (oem,name,brand,catalog_type,msrp_usd,qoh,qoh_is_current,source,source_url,source_file,captured_at,imported_at)
 VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(r["oem"],r.get("name"),r.get("brand"),r["catalog_type"],r.get("msrp_usd"),r.get("qoh"),int(r.get("qoh_is_current",False)),r["source"],r.get("source_url"),r["source_file"],r["captured_at"],now))
con.commit()
print("DCP_SEED_ROWS",con.execute("select count(*) from dcp_catalog_snapshot").fetchone()[0])
print("CONTROL",con.execute("select oem,name,brand,msrp_usd,qoh,qoh_is_current,catalog_type from dcp_catalog_snapshot where oem='417224332'").fetchall())
con.close()
