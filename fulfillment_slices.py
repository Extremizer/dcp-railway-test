#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Per-position fulfillment slices and append-only fulfillment history."""
from __future__ import annotations
import json
import sqlite3
from datetime import datetime

ROUTE_RF = "RF_WAREHOUSE"
ROUTE_USA = "USA"

RF_PENDING_RESERVATION = "RF_PENDING_RESERVATION"
RF_RESERVED = "RF_RESERVED"
RF_SENT_TO_WAREHOUSE = "RF_SENT_TO_WAREHOUSE"
RF_WAREHOUSE_CONFIRMED = "RF_WAREHOUSE_CONFIRMED"
RF_ASSEMBLED = "RF_ASSEMBLED"
RF_SHIPPED_TO_CLIENT = "RF_SHIPPED_TO_CLIENT"
RF_DELIVERED = "RF_DELIVERED"
RF_CANCELLED = "RF_CANCELLED"

USA_ORDERED = "USA_ORDERED"

ISSUE_SHORTAGE = "SHORTAGE"

STATUS_LABELS = {
    RF_PENDING_RESERVATION: "Оформляется резерв",
    RF_RESERVED: "🟡 Зарезервировано",
    RF_SENT_TO_WAREHOUSE: "📤 Заказ отправлен складу",
    RF_WAREHOUSE_CONFIRMED: "👍 Склад подтвердил",
    RF_ASSEMBLED: "📦 Собрано складом",
    RF_SHIPPED_TO_CLIENT: "🚚 Отправлено клиенту",
    RF_DELIVERED: "✅ Доставлено",
    RF_CANCELLED: "❌ Отменено",
    USA_ORDERED: "🟡 Заказано в США",
}

def _now():
    return datetime.now().astimezone().isoformat(timespec="seconds")

def init(db_file):
    with sqlite3.connect(db_file) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS fulfillment_slices(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          order_id TEXT NOT NULL,
          order_item_id INTEGER NOT NULL,
          parent_slice_id INTEGER,
          route TEXT NOT NULL,
          warehouse_id INTEGER,
          supplier_order_id INTEGER,
          supplier_order_item_id INTEGER,
          quantity REAL NOT NULL CHECK(quantity > 0),
          status TEXT NOT NULL,
          issue TEXT,
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          FOREIGN KEY(parent_slice_id) REFERENCES fulfillment_slices(id)
        );
        CREATE TABLE IF NOT EXISTS fulfillment_events(
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          slice_id INTEGER NOT NULL,
          event_type TEXT NOT NULL,
          from_status TEXT,
          to_status TEXT,
          issue_before TEXT,
          issue_after TEXT,
          actor_type TEXT NOT NULL,
          actor_id TEXT,
          source TEXT,
          details_json TEXT,
          created_at TEXT NOT NULL,
          FOREIGN KEY(slice_id) REFERENCES fulfillment_slices(id)
        );
        CREATE INDEX IF NOT EXISTS idx_fulfillment_order
          ON fulfillment_slices(order_id, order_item_id);
        CREATE INDEX IF NOT EXISTS idx_fulfillment_supplier
          ON fulfillment_slices(supplier_order_id, supplier_order_item_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_fulfillment_root_supplier_item
          ON fulfillment_slices(supplier_order_item_id)
          WHERE supplier_order_item_id IS NOT NULL AND parent_slice_id IS NULL;
        CREATE INDEX IF NOT EXISTS idx_fulfillment_events_slice
          ON fulfillment_events(slice_id, created_at);
        """)

def _event(conn, slice_id, event_type, *, from_status=None, to_status=None,
           issue_before=None, issue_after=None, actor_type="system",
           actor_id=None, source=None, details=None):
    conn.execute("""INSERT INTO fulfillment_events(
      slice_id,event_type,from_status,to_status,issue_before,issue_after,
      actor_type,actor_id,source,details_json,created_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
      (slice_id,event_type,from_status,to_status,issue_before,issue_after,
       str(actor_type or "system"),None if actor_id is None else str(actor_id),
       source,json.dumps(details,ensure_ascii=False,sort_keys=True) if details else None,_now()))

def _insert_slice(conn, *, order_id, order_item_id, parent_slice_id, route,
                  warehouse_id, supplier_order_id, supplier_order_item_id,
                  quantity, status, issue, actor_type, actor_id, source, details=None):
    now=_now()
    cur=conn.execute("""INSERT INTO fulfillment_slices(
      order_id,order_item_id,parent_slice_id,route,warehouse_id,
      supplier_order_id,supplier_order_item_id,quantity,status,issue,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
      (order_id,order_item_id,parent_slice_id,route,warehouse_id,supplier_order_id,
       supplier_order_item_id,float(quantity),status,issue,now,now))
    sid=int(cur.lastrowid)
    _event(conn,sid,"slice_created",to_status=status,issue_after=issue,
           actor_type=actor_type,actor_id=actor_id,source=source,details=details)
    return sid

def ensure_supplier_order_roots_conn(conn, supplier_order_id, *,
                                     actor_type="system",actor_id=None,
                                     source="supplier_order"):
    conn.row_factory=sqlite3.Row
    order=conn.execute("""SELECT client_order_id,warehouse_id FROM supplier_orders
      WHERE id=?""",(supplier_order_id,)).fetchone()
    if not order:
        raise KeyError("supplier order not found")
    items=conn.execute("""SELECT id,order_item_id,quantity FROM supplier_order_items
      WHERE supplier_order_id=? ORDER BY id""",(supplier_order_id,)).fetchall()
    out=[]
    for item in items:
        if item["order_item_id"] is None:
            raise ValueError("supplier order item is not linked to client order item")
        row=conn.execute("""SELECT * FROM fulfillment_slices
          WHERE supplier_order_item_id=? AND parent_slice_id IS NULL""",
          (int(item["id"]),)).fetchone()
        if row:
            out.append(dict(row)); continue
        sid=_insert_slice(
            conn,order_id=order["client_order_id"],order_item_id=int(item["order_item_id"]),
            parent_slice_id=None,route=ROUTE_RF,warehouse_id=int(order["warehouse_id"]),
            supplier_order_id=int(supplier_order_id),supplier_order_item_id=int(item["id"]),
            quantity=float(item["quantity"]),status=RF_PENDING_RESERVATION,issue=None,
            actor_type=actor_type,actor_id=actor_id,source=source,
            details={"reason":"supplier_order_item_created"},
        )
        row=conn.execute("SELECT * FROM fulfillment_slices WHERE id=?",(sid,)).fetchone()
        out.append(dict(row))
    return out

def ensure_supplier_order_roots(db_file,supplier_order_id,**actor):
    init(db_file)
    with sqlite3.connect(db_file) as c:
        c.row_factory=sqlite3.Row
        c.execute("BEGIN IMMEDIATE")
        result=ensure_supplier_order_roots_conn(c,supplier_order_id,**actor)
        c.commit()
        return result

def list_for_supplier_order(db_file,supplier_order_id):
    init(db_file)
    with sqlite3.connect(db_file) as c:
        c.row_factory=sqlite3.Row
        return [dict(x) for x in c.execute("""SELECT * FROM fulfillment_slices
          WHERE supplier_order_id=? ORDER BY order_item_id,parent_slice_id,id""",
          (supplier_order_id,)).fetchall()]

def _change(conn,row,*,status=None,issue_marker=False,issue=None,event_type,
            actor_type="system",actor_id=None,source=None,details=None):
    old_status=row["status"]; old_issue=row["issue"]
    new_status=old_status if status is None else status
    new_issue=old_issue if not issue_marker else issue
    if new_status==old_status and new_issue==old_issue:
        return False
    conn.execute("""UPDATE fulfillment_slices
      SET status=?,issue=?,updated_at=? WHERE id=?""",
      (new_status,new_issue,_now(),int(row["id"])))
    _event(conn,int(row["id"]),event_type,from_status=old_status,to_status=new_status,
           issue_before=old_issue,issue_after=new_issue,actor_type=actor_type,
           actor_id=actor_id,source=source,details=details)
    return True

def mark_supplier_sent_conn(conn,supplier_order_id,*,actor_type="system",
                            actor_id=None,source="supplier_delivery",details=None):
    ensure_supplier_order_roots_conn(conn,supplier_order_id,actor_type=actor_type,
                                     actor_id=actor_id,source=source)
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_id=? AND route=? AND status!=?""",
      (supplier_order_id,ROUTE_RF,RF_CANCELLED)).fetchall()
    if not rows:
        raise ValueError("supplier order has no active RF fulfillment slices")
    for row in rows:
        if row["parent_slice_id"] is not None:
            continue
        if row["status"] not in {RF_PENDING_RESERVATION,RF_RESERVED,RF_SENT_TO_WAREHOUSE}:
            raise ValueError(f"invalid RF slice state for send: {row['status']}")
        _change(conn,row,status=RF_SENT_TO_WAREHOUSE,event_type="supplier_sent",
                actor_type=actor_type,actor_id=actor_id,source=source,details=details)

def confirm_supplier_order_conn(conn,supplier_order_id,*,actor_type="admin",
                                actor_id=None,source="supplier_admin"):
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_id=? AND route=? AND status!=?
      ORDER BY id""",(supplier_order_id,ROUTE_RF,RF_CANCELLED)).fetchall()
    active=[r for r in rows]
    if not active:
        return 0
    for row in active:
        if row["issue"]:
            raise ValueError("RF fulfillment slice has unresolved issue")
        if row["status"] not in {RF_SENT_TO_WAREHOUSE,RF_WAREHOUSE_CONFIRMED}:
            raise ValueError(f"RF slice is not ready for confirmation: {row['status']}")
    changed=0
    for row in active:
        if _change(conn,row,status=RF_WAREHOUSE_CONFIRMED,event_type="warehouse_confirmed",
                   actor_type=actor_type,actor_id=actor_id,source=source):
            changed+=1
    return changed

def assemble_supplier_order_conn(conn,supplier_order_id,*,actor_type="admin",
                                 actor_id=None,source="supplier_admin"):
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_id=? AND route=? AND status!=?
      ORDER BY id""",(supplier_order_id,ROUTE_RF,RF_CANCELLED)).fetchall()
    active=[r for r in rows]
    if not active:
        return 0
    for row in active:
        if row["issue"]:
            raise ValueError("RF fulfillment slice has unresolved issue")
        if row["status"] not in {RF_WAREHOUSE_CONFIRMED,RF_ASSEMBLED}:
            raise ValueError(f"RF slice is not ready for assembly: {row['status']}")
    changed=0
    for row in active:
        if _change(conn,row,status=RF_ASSEMBLED,event_type="warehouse_assembled",
                   actor_type=actor_type,actor_id=actor_id,source=source):
            changed+=1
    return changed

def split_shortage_conn(conn,supplier_order_id,supplier_order_item_id,confirmed_qty,*,
                        actor_type="admin",actor_id=None,source="supplier_admin"):
    conn.row_factory=sqlite3.Row
    item=conn.execute("""SELECT soi.id,so.client_order_id,so.warehouse_id,
      soi.order_item_id,soi.quantity
      FROM supplier_order_items soi JOIN supplier_orders so ON so.id=soi.supplier_order_id
      WHERE soi.id=? AND soi.supplier_order_id=?""",
      (supplier_order_item_id,supplier_order_id)).fetchone()
    if not item:
        raise ValueError("position does not belong to supplier order")
    ordered=float(item["quantity"]); confirmed=float(confirmed_qty)
    if confirmed<0 or confirmed>=ordered:
        raise ValueError("confirmed_qty must be >=0 and < ordered_qty for a shortage")
    ensure_supplier_order_roots_conn(conn,supplier_order_id,actor_type=actor_type,
                                     actor_id=actor_id,source=source)
    root=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_item_id=? AND parent_slice_id IS NULL""",
      (supplier_order_item_id,)).fetchone()
    if not root:
        raise ValueError("RF fulfillment root is missing")
    child=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE parent_slice_id=? AND issue=? ORDER BY id LIMIT 1""",
      (int(root["id"]),ISSUE_SHORTAGE)).fetchone()
    if child or root["issue"]==ISSUE_SHORTAGE:
        existing_confirmed=0.0 if root["issue"]==ISSUE_SHORTAGE else float(root["quantity"])
        if abs(existing_confirmed-confirmed)<1e-9:
            return {
                "confirmed_slice_id": None if confirmed==0 else int(root["id"]),
                "shortage_slice_id": int(root["id"]) if confirmed==0 else int(child["id"]),
                "idempotent": True,
            }
        raise ValueError("shortage already recorded with another confirmed quantity")
    if root["status"]!=RF_SENT_TO_WAREHOUSE:
        raise ValueError("shortage may be recorded only after supplier order was sent")

    shortage=ordered-confirmed
    if confirmed>0:
        old_qty=float(root["quantity"])
        conn.execute("""UPDATE fulfillment_slices
          SET quantity=?,status=?,issue=NULL,updated_at=? WHERE id=?""",
          (confirmed,RF_WAREHOUSE_CONFIRMED,_now(),int(root["id"])))
        _event(conn,int(root["id"]),"warehouse_partial_confirmed",
               from_status=root["status"],to_status=RF_WAREHOUSE_CONFIRMED,
               issue_before=root["issue"],issue_after=None,actor_type=actor_type,
               actor_id=actor_id,source=source,
               details={"ordered_qty":ordered,"confirmed_qty":confirmed,
                        "previous_slice_qty":old_qty})
        child_id=_insert_slice(
            conn,order_id=item["client_order_id"],order_item_id=int(item["order_item_id"]),
            parent_slice_id=int(root["id"]),route=ROUTE_RF,warehouse_id=int(item["warehouse_id"]),
            supplier_order_id=int(supplier_order_id),supplier_order_item_id=int(supplier_order_item_id),
            quantity=shortage,status=RF_SENT_TO_WAREHOUSE,issue=ISSUE_SHORTAGE,
            actor_type=actor_type,actor_id=actor_id,source=source,
            details={"ordered_qty":ordered,"confirmed_qty":confirmed,"shortage_qty":shortage},
        )
        return {"confirmed_slice_id":int(root["id"]),"shortage_slice_id":child_id,"idempotent":False}

    _change(conn,root,issue_marker=True,issue=ISSUE_SHORTAGE,
            event_type="warehouse_zero_confirmed",actor_type=actor_type,
            actor_id=actor_id,source=source,
            details={"ordered_qty":ordered,"confirmed_qty":0.0,"shortage_qty":shortage})
    return {"confirmed_slice_id":None,"shortage_slice_id":int(root["id"]),"idempotent":False}

def resolve_partial_refund_conn(conn,supplier_order_item_id,*,actor_type="client",
                                actor_id=None,source="customer_decision"):
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_item_id=? ORDER BY id""",(supplier_order_item_id,)).fetchall()
    shortage=[r for r in rows if r["issue"]==ISSUE_SHORTAGE]
    if not shortage:
        raise ValueError("shortage fulfillment slice not found")
    for row in shortage:
        _change(conn,row,status=RF_CANCELLED,issue_marker=True,issue=None,
                event_type="shortage_refunded",actor_type=actor_type,
                actor_id=actor_id,source=source)
    return len(shortage)

def resolve_cancel_all_conn(conn,supplier_order_item_id,*,actor_type="client",
                            actor_id=None,source="customer_decision"):
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_item_id=? AND status!=? ORDER BY id""",
      (supplier_order_item_id,RF_CANCELLED)).fetchall()
    for row in rows:
        _change(conn,row,status=RF_CANCELLED,issue_marker=True,issue=None,
                event_type="position_cancelled",actor_type=actor_type,
                actor_id=actor_id,source=source)
    return len(rows)

def resolve_usa_quote_conn(conn,supplier_order_item_id,accept,*,actor_type="client",
                           actor_id=None,source="customer_decision"):
    conn.row_factory=sqlite3.Row
    rows=conn.execute("""SELECT * FROM fulfillment_slices
      WHERE supplier_order_item_id=? AND issue=? ORDER BY id""",
      (supplier_order_item_id,ISSUE_SHORTAGE)).fetchall()
    if not rows:
        raise ValueError("shortage fulfillment slice not found")
    for row in rows:
        if accept:
            old_status=row["status"]; old_issue=row["issue"]
            conn.execute("""UPDATE fulfillment_slices
              SET route=?,warehouse_id=NULL,status=?,issue=NULL,updated_at=? WHERE id=?""",
              (ROUTE_USA,USA_ORDERED,_now(),int(row["id"])))
            _event(conn,int(row["id"]),"shortage_rerouted_to_usa",
                   from_status=old_status,to_status=USA_ORDERED,
                   issue_before=old_issue,issue_after=None,actor_type=actor_type,
                   actor_id=actor_id,source=source)
        else:
            _change(conn,row,status=RF_CANCELLED,issue_marker=True,issue=None,
                    event_type="usa_quote_declined",actor_type=actor_type,
                    actor_id=actor_id,source=source)
    return len(rows)

def active_rf_count_conn(conn,supplier_order_id):
    row=conn.execute("""SELECT COUNT(*) FROM fulfillment_slices
      WHERE supplier_order_id=? AND route=? AND status!=?""",
      (supplier_order_id,ROUTE_RF,RF_CANCELLED)).fetchone()
    return int(row[0] or 0)
