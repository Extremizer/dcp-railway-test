from __future__ import annotations
import sqlite3
from datetime import datetime,timezone
CHANNELS=("telegram","whatsapp","email","custom")
def init(db):
 with sqlite3.connect(db) as c:c.execute("CREATE TABLE IF NOT EXISTS supplier_channels(warehouse_id INTEGER PRIMARY KEY,channel TEXT NOT NULL,recipient TEXT NOT NULL,custom_name TEXT,enabled INTEGER NOT NULL DEFAULT 1,updated_at TEXT NOT NULL)")
def save(db,wid,channel,recipient,custom_name=None):
 channel=str(channel).lower().strip(); recipient=str(recipient).strip()
 if channel not in CHANNELS: raise ValueError("unsupported channel")
 if not recipient: raise ValueError("recipient required")
 if channel=="custom" and not str(custom_name or "").strip(): raise ValueError("custom channel name required")
 init(db)
 with sqlite3.connect(db) as c:c.execute("INSERT INTO supplier_channels VALUES(?,?,?,?,1,?) ON CONFLICT(warehouse_id) DO UPDATE SET channel=excluded.channel,recipient=excluded.recipient,custom_name=excluded.custom_name,enabled=1,updated_at=excluded.updated_at",(wid,channel,recipient,(custom_name or "").strip() or None,datetime.now(timezone.utc).isoformat()))
 return get(db,wid)
def get(db,wid):
 init(db)
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row; r=c.execute("SELECT * FROM supplier_channels WHERE warehouse_id=? AND enabled=1",(wid,)).fetchone()
 return dict(r) if r else None
def list_warehouses(db):
 init(db)
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row
  return [dict(x) for x in c.execute("SELECT w.id,w.public_name,w.internal_name,s.channel,s.recipient,s.custom_name FROM warehouses w LEFT JOIN supplier_channels s ON s.warehouse_id=w.id AND s.enabled=1 ORDER BY w.priority,w.public_name")]
