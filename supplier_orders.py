#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supplier Orders business layer for Extremizer Pro warehouses.

This module is intentionally separate from warehouse_stock_reservations:
reservation statuses are technical; supplier order statuses are business state.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime
from pathlib import Path

STATUSES = ("not_sent","sent","confirmed","assembled","shipped","delivered")
STATUS_LABELS = {
    "not_sent":"🟡 Не отправлен",
    "sent":"📤 Отправлен складу",
    "confirmed":"👍 Подтверждён складом",
    "assembled":"📦 Собран",
    "shipped":"🚚 Отправлен клиенту",
    "delivered":"✅ Доставлен",
}
ALLOWED = {
    "not_sent":{"sent"},
    "sent":{"confirmed"},
    "confirmed":{"assembled"},
    "assembled":{"shipped"},
    "shipped":{"delivered"},
    "delivered":set(),
}
def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def init_supplier_orders_db(db_file):
    with sqlite3.connect(db_file) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS supplier_orders(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          client_order_id TEXT NOT NULL,
          warehouse_id INTEGER NOT NULL,
          status TEXT NOT NULL DEFAULT 'not_sent',
          recipient_name TEXT, recipient_phone TEXT, recipient_city TEXT,
          delivery_method TEXT, delivery_address TEXT, pickup_point TEXT,
          carrier TEXT, tracking_number TEXT,
          sent_at TEXT, confirmed_at TEXT, assembled_at TEXT,
          shipped_at TEXT, delivered_at TEXT,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          UNIQUE(client_order_id, warehouse_id),
          FOREIGN KEY(warehouse_id) REFERENCES warehouses(id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS supplier_order_items(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          supplier_order_id INTEGER NOT NULL,
          order_item_id INTEGER, manufacturer TEXT, oem TEXT NOT NULL,
          name TEXT, quantity REAL NOT NULL,
          FOREIGN KEY(supplier_order_id) REFERENCES supplier_orders(id),
          UNIQUE(supplier_order_id, order_item_id))""")
        c.execute("""CREATE TABLE IF NOT EXISTS supplier_order_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          supplier_order_id INTEGER NOT NULL,
          event_type TEXT NOT NULL, from_status TEXT, to_status TEXT,
          details TEXT, created_at TEXT NOT NULL,
          FOREIGN KEY(supplier_order_id) REFERENCES supplier_orders(id))""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplier_orders_status_wh ON supplier_orders(status,warehouse_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplier_orders_client ON supplier_orders(client_order_id)")

def ensure_supplier_order(db_file, client_order_id, warehouse_id, recipient=None):
    init_supplier_orders_db(db_file); recipient=recipient or {}; now=_now()
    with sqlite3.connect(db_file) as c:
        c.execute("""INSERT OR IGNORE INTO supplier_orders(
          client_order_id,warehouse_id,status,recipient_name,recipient_phone,
          recipient_city,delivery_method,delivery_address,pickup_point,created_at,updated_at)
          VALUES(?,?,'not_sent',?,?,?,?,?,?,?,?)""",
          (client_order_id,warehouse_id,recipient.get("name"),recipient.get("phone"),
           recipient.get("city"),recipient.get("method"),recipient.get("address"),
           recipient.get("pickup_point"),now,now))
        row=c.execute("SELECT id FROM supplier_orders WHERE client_order_id=? AND warehouse_id=?",
                      (client_order_id,warehouse_id)).fetchone()
        sid=int(row[0])
    return sid

def add_item(db_file, supplier_order_id, *, order_item_id, manufacturer, oem, name, quantity):
    with sqlite3.connect(db_file) as c:
        c.execute("""INSERT OR REPLACE INTO supplier_order_items(
          id,supplier_order_id,order_item_id,manufacturer,oem,name,quantity)
          VALUES((SELECT id FROM supplier_order_items WHERE supplier_order_id=? AND order_item_id=?),?,?,?,?,?,?)""",
          (supplier_order_id,order_item_id,supplier_order_id,order_item_id,manufacturer,oem,name,float(quantity)))

def transition(db_file, supplier_order_id, new_status, details=None):
    if new_status not in STATUSES: raise ValueError("unknown supplier status")
    now=_now()
    with sqlite3.connect(db_file) as c:
        row=c.execute("SELECT status FROM supplier_orders WHERE id=?",(supplier_order_id,)).fetchone()
        if not row: raise KeyError("supplier order not found")
        old=row[0]
        if new_status==old: return old
        if new_status not in ALLOWED.get(old,set()):
            raise ValueError(f"invalid supplier transition {old} -> {new_status}")
        col={"sent":"sent_at","confirmed":"confirmed_at","assembled":"assembled_at",
             "shipped":"shipped_at","delivered":"delivered_at"}[new_status]
        c.execute(f"UPDATE supplier_orders SET status=?, {col}=?, updated_at=? WHERE id=?",
                  (new_status,now,now,supplier_order_id))
        c.execute("""INSERT INTO supplier_order_events(
          supplier_order_id,event_type,from_status,to_status,details,created_at)
          VALUES(?,'status_changed',?,?,?,?)""",(supplier_order_id,old,new_status,details,now))
        return new_status

def add_event(db_file,supplier_order_id,event_type,details=None):
 with sqlite3.connect(db_file) as c:
  c.execute("""INSERT INTO supplier_order_events(supplier_order_id,event_type,details,created_at) VALUES(?,?,?,?)""",(supplier_order_id,event_type,details,_now()))

def set_tracking(db_file, supplier_order_id, carrier, tracking_number):
    carrier=(carrier or "").strip(); tracking_number=(tracking_number or "").strip()
    if not carrier or not tracking_number: raise ValueError("carrier and tracking number required")
    with sqlite3.connect(db_file) as c:
        row=c.execute("SELECT status FROM supplier_orders WHERE id=?",(supplier_order_id,)).fetchone()
        if not row: raise KeyError("supplier order not found")
        if row[0]!="assembled": raise ValueError("tracking may be set only for assembled supplier order")
        c.execute("UPDATE supplier_orders SET carrier=?,tracking_number=?,updated_at=? WHERE id=?",
                  (carrier,tracking_number,_now(),supplier_order_id))
    transition(db_file,supplier_order_id,"shipped",f"{carrier} {tracking_number}")

def list_summary(db_file, statuses=None):
    statuses=list(statuses or [])
    where=""
    params=[]
    if statuses:
        where="WHERE so.status IN (%s)" % ",".join("?" for _ in statuses)
        params=statuses
    with sqlite3.connect(db_file) as c:
        c.row_factory=sqlite3.Row
        rows=c.execute(f"""SELECT so.id,so.client_order_id,so.warehouse_id,so.status,
          so.recipient_name,so.recipient_city,so.carrier,so.tracking_number,
          w.public_name,COUNT(i.id) item_lines,COALESCE(SUM(i.quantity),0) units
          FROM supplier_orders so JOIN warehouses w ON w.id=so.warehouse_id
          LEFT JOIN supplier_order_items i ON i.supplier_order_id=so.id
          {where}
          GROUP BY so.id ORDER BY w.priority,w.public_name,so.id DESC""",params).fetchall()
        return [dict(x) for x in rows]

def pending_summary(db_file):
    return list_summary(db_file,("not_sent","sent","confirmed","assembled","shipped"))

def supplier_payload(db_file, supplier_order_id):
    with sqlite3.connect(db_file) as c:
        c.row_factory=sqlite3.Row
        order=c.execute("""SELECT so.*,w.public_name,w.internal_name FROM supplier_orders so
          JOIN warehouses w ON w.id=so.warehouse_id WHERE so.id=?""",(supplier_order_id,)).fetchone()
        if not order: raise KeyError("supplier order not found")
        items=c.execute("""SELECT id,order_item_id,manufacturer,oem,name,quantity
          FROM supplier_order_items WHERE supplier_order_id=? ORDER BY id""",(supplier_order_id,)).fetchall()
        result=dict(order); result["items"]=[dict(x) for x in items]; return result
