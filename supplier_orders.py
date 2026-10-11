#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supplier Orders business layer for Extremizer Pro warehouses.

Supplier Order statuses describe RF warehouse execution. Warehouse reservation
statuses stay technical and are synchronized by the service layer.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime

STATUSES = ("not_sent","sent","confirmed","assembled","shipped","delivered","cancelled")
STATUS_LABELS = {
    "not_sent":"🟡 Не отправлен",
    "sent":"📤 Отправлен складу",
    "confirmed":"👍 Подтверждён складом",
    "assembled":"📦 Собран",
    "shipped":"🚚 Отправлен клиенту",
    "delivered":"✅ Доставлено",
    "cancelled":"❌ Закрыт без отгрузки",
}
ALLOWED = {
    "not_sent":{"sent","cancelled"},
    "sent":{"confirmed","cancelled"},
    "confirmed":{"assembled","cancelled"},
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
          shipped_at TEXT, delivered_at TEXT, cancelled_at TEXT,
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
        c.execute("""CREATE TABLE IF NOT EXISTS supplier_delivery_attempts(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          supplier_order_id INTEGER NOT NULL,
          channel TEXT NOT NULL,
          state TEXT NOT NULL,
          provider_receipt TEXT,
          error TEXT,
          started_at TEXT NOT NULL,
          accepted_at TEXT,
          finished_at TEXT,
          FOREIGN KEY(supplier_order_id) REFERENCES supplier_orders(id))""")
        columns={row[1] for row in c.execute("PRAGMA table_info(supplier_orders)")}
        if "cancelled_at" not in columns:
            c.execute("ALTER TABLE supplier_orders ADD COLUMN cancelled_at TEXT")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplier_orders_status_wh ON supplier_orders(status,warehouse_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_supplier_orders_client ON supplier_orders(client_order_id)")
        c.execute("""CREATE UNIQUE INDEX IF NOT EXISTS idx_supplier_delivery_attempt_active
          ON supplier_delivery_attempts(supplier_order_id)
          WHERE state IN ('sending','accepted')""")

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

def _transition_conn(c, supplier_order_id, new_status, details=None):
    if new_status not in STATUSES:
        raise ValueError("unknown supplier status")
    now=_now()
    row=c.execute("SELECT status FROM supplier_orders WHERE id=?",(supplier_order_id,)).fetchone()
    if not row:
        raise KeyError("supplier order not found")
    old=row[0]
    if new_status==old:
        return old
    if new_status not in ALLOWED.get(old,set()):
        raise ValueError(f"invalid supplier transition {old} -> {new_status}")
    col={"sent":"sent_at","confirmed":"confirmed_at","assembled":"assembled_at",
         "shipped":"shipped_at","delivered":"delivered_at",
         "cancelled":"cancelled_at"}[new_status]
    c.execute(f"UPDATE supplier_orders SET status=?, {col}=?, updated_at=? WHERE id=?",
              (new_status,now,now,supplier_order_id))
    c.execute("""INSERT INTO supplier_order_events(
      supplier_order_id,event_type,from_status,to_status,details,created_at)
      VALUES(?,'status_changed',?,?,?,?)""",(supplier_order_id,old,new_status,details,now))
    return new_status

def transition(db_file, supplier_order_id, new_status, details=None, conn=None):
    if conn is not None:
        return _transition_conn(conn,supplier_order_id,new_status,details)
    with sqlite3.connect(db_file) as c:
        return _transition_conn(c,supplier_order_id,new_status,details)

def _begin_delivery_attempt_conn(c,supplier_order_id,channel):
    row=c.execute("SELECT status FROM supplier_orders WHERE id=?",(supplier_order_id,)).fetchone()
    if not row:
        raise KeyError("supplier order not found")
    if row[0]!="not_sent":
        raise ValueError("supplier order is not ready to send")
    unresolved=c.execute("""SELECT id,state FROM supplier_delivery_attempts
      WHERE supplier_order_id=? AND state IN ('sending','accepted')
      ORDER BY id DESC LIMIT 1""",(supplier_order_id,)).fetchone()
    if unresolved:
        raise ValueError(
            f"supplier delivery attempt {unresolved[0]} is unresolved ({unresolved[1]})"
        )
    cur=c.execute("""INSERT INTO supplier_delivery_attempts(
      supplier_order_id,channel,state,started_at)
      VALUES(?,?,'sending',?)""",(supplier_order_id,str(channel),_now()))
    return int(cur.lastrowid)

def begin_delivery_attempt(db_file,supplier_order_id,channel,conn=None):
    if conn is not None:
        return _begin_delivery_attempt_conn(conn,supplier_order_id,channel)
    with sqlite3.connect(db_file) as c:
        c.execute("BEGIN IMMEDIATE")
        return _begin_delivery_attempt_conn(c,supplier_order_id,channel)

def mark_delivery_attempt_failed(db_file,attempt_id,error):
    with sqlite3.connect(db_file) as c:
        row=c.execute("SELECT state FROM supplier_delivery_attempts WHERE id=?",(attempt_id,)).fetchone()
        if not row:
            raise KeyError("supplier delivery attempt not found")
        if row[0]!="sending":
            raise ValueError("supplier delivery attempt is not sending")
        c.execute("""UPDATE supplier_delivery_attempts
          SET state='failed',error=?,finished_at=?
          WHERE id=?""",(str(error)[:2000],_now(),attempt_id))

def mark_delivery_attempt_accepted(db_file,attempt_id,receipt):
    with sqlite3.connect(db_file) as c:
        row=c.execute("SELECT state FROM supplier_delivery_attempts WHERE id=?",(attempt_id,)).fetchone()
        if not row:
            raise KeyError("supplier delivery attempt not found")
        if row[0]!="sending":
            raise ValueError("supplier delivery attempt is not sending")
        c.execute("""UPDATE supplier_delivery_attempts
          SET state='accepted',provider_receipt=?,accepted_at=?
          WHERE id=?""",(str(receipt),_now(),attempt_id))

def mark_delivery_attempt_sent_conn(c,attempt_id):
    row=c.execute("""SELECT state,provider_receipt FROM supplier_delivery_attempts
      WHERE id=?""",(attempt_id,)).fetchone()
    if not row:
        raise KeyError("supplier delivery attempt not found")
    if row[0]!="accepted" or not str(row[1] or "").strip():
        raise ValueError("supplier delivery attempt has no accepted provider receipt")
    c.execute("""UPDATE supplier_delivery_attempts
      SET state='sent',finished_at=? WHERE id=?""",(_now(),attempt_id))

def get_delivery_attempt(db_file,attempt_id):
    with sqlite3.connect(db_file) as c:
        c.row_factory=sqlite3.Row
        row=c.execute("SELECT * FROM supplier_delivery_attempts WHERE id=?",(attempt_id,)).fetchone()
        return dict(row) if row else None

def add_event(db_file,supplier_order_id,event_type,details=None):
    with sqlite3.connect(db_file) as c:
        c.execute("""INSERT INTO supplier_order_events(supplier_order_id,event_type,details,created_at)
          VALUES(?,?,?,?)""",(supplier_order_id,event_type,details,_now()))

def set_tracking_and_ship_conn(
    c,
    supplier_order_id,
    carrier,
    tracking_number,
    *,
    details=None,
):
    """Persist carrier/tracking and mark shipped in the caller transaction."""
    carrier=(carrier or "").strip()
    tracking_number=(tracking_number or "").strip()
    if not carrier or not tracking_number:
        raise ValueError("carrier and tracking number required")

    row=c.execute(
        """SELECT status,carrier,tracking_number,shipped_at
           FROM supplier_orders WHERE id=?""",
        (supplier_order_id,),
    ).fetchone()
    if not row:
        raise KeyError("supplier order not found")
    status=str(row[0])
    existing_carrier=str(row[1] or "").strip()
    existing_tracking=str(row[2] or "").strip()

    if status in {"shipped","delivered"}:
        if existing_carrier==carrier and existing_tracking==tracking_number:
            return {
                "changed":False,
                "status":status,
                "carrier":existing_carrier,
                "tracking_number":existing_tracking,
                "shipped_at":row[3],
            }
        raise ValueError("supplier order already has another shipment tracking")

    if status!="assembled":
        raise ValueError("tracking may be set only for assembled supplier order")

    # Recover safely from any historical partial tracking write only if the
    # operator supplies exactly the same values. Never silently overwrite them.
    if existing_carrier or existing_tracking:
        if existing_carrier!=carrier or existing_tracking!=tracking_number:
            raise ValueError("assembled supplier order already has another tracking")

    now=_now()
    c.execute(
        """UPDATE supplier_orders
           SET carrier=?,tracking_number=?,updated_at=?
           WHERE id=?""",
        (carrier,tracking_number,now,supplier_order_id),
    )
    transition(
        None,
        supplier_order_id,
        "shipped",
        details or f"{carrier} {tracking_number}",
        conn=c,
    )
    row=c.execute(
        """SELECT status,carrier,tracking_number,shipped_at
           FROM supplier_orders WHERE id=?""",
        (supplier_order_id,),
    ).fetchone()
    return {
        "changed":True,
        "status":row[0],
        "carrier":row[1],
        "tracking_number":row[2],
        "shipped_at":row[3],
    }


def set_tracking(db_file, supplier_order_id, carrier, tracking_number):
    raise ValueError(
        "use SupplierOrderService.add_tracking so fulfillment stays atomic"
    )

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
        if not order:
            raise KeyError("supplier order not found")
        items=c.execute("""SELECT id,order_item_id,manufacturer,oem,name,quantity
          FROM supplier_order_items WHERE supplier_order_id=? ORDER BY id""",(supplier_order_id,)).fetchall()
        result=dict(order); result["items"]=[dict(x) for x in items]; return result
