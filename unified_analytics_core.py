"""Isolated unified analytics candidate; same canonical orders SQLite DB, no sidecar DB."""
from __future__ import annotations
import hashlib,json,sqlite3,time
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime,timezone

SOURCES=frozenset(("probnik","pricing","oemixibot"))
@contextmanager
def _conn(db):
    # Never initialize or create another analytics database.
    if not Path(db).is_file():raise FileNotFoundError("canonical_orders_db_missing")
    c=sqlite3.connect(str(db),timeout=2)
    c.row_factory=sqlite3.Row
    try:
        with c:
            yield c
    finally:
        c.close()

def initialize(db):
    with _conn(db) as c:
        c.execute("CREATE TABLE IF NOT EXISTS analytics_delivery_keys (source_bot TEXT NOT NULL, delivery_key TEXT NOT NULL, request_id INTEGER NOT NULL REFERENCES pricing_crm_requests(id), PRIMARY KEY(source_bot,delivery_key))")
        c.execute("CREATE TABLE IF NOT EXISTS analytics_pending (source_bot TEXT NOT NULL, delivery_key TEXT NOT NULL, payload_json TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, error_code TEXT, next_attempt_at TEXT, PRIMARY KEY(source_bot,delivery_key))")

def begin(db,*,source_bot,source_kind,oem,delivery_key,**identity):
    if source_bot not in SOURCES or not delivery_key or not oem:raise ValueError("invalid_event_identity")
    now=datetime.now(timezone.utc).isoformat()
    try:
        with _conn(db) as c:
            c.execute("BEGIN IMMEDIATE")
            row=c.execute("SELECT request_id FROM analytics_delivery_keys WHERE source_bot=? AND delivery_key=?",(source_bot,delivery_key)).fetchone()
            if row:return int(row[0])
            cur=c.execute("""INSERT INTO pricing_crm_requests
               (created_at,updated_at,source_bot,source_kind,oem,manufacturer,telegram_user_id,source_chat_id,telegram_message_id)
               VALUES(?,?,?,?,?,?,?,?,?)""",(now,now,source_bot,source_kind,oem,identity.get('manufacturer'),identity.get('telegram_user_id'),identity.get('source_chat_id'),identity.get('telegram_message_id')))
            rid=cur.lastrowid
            c.execute("INSERT INTO analytics_delivery_keys VALUES(?,?,?)",(source_bot,delivery_key,rid))
            return rid
    except sqlite3.Error:
        return None

def complete(db,request_id,*,result_status,price_status,price=None,currency=None,stock_status=None,stock=None):
    if request_id is None:return False
    if price is not None and (currency not in ('RUB','USD') or price<0):return False
    if price is None and currency is not None:return False
    try:
        with _conn(db) as c:
            result=c.execute("""UPDATE pricing_crm_requests SET updated_at=?,
              result_status=?,price_status=?,display_price_amount=?,display_price_currency=?,
              stock_rf_status=?,stock_rf_json=?
              WHERE id=? AND result_status IS NULL""",(datetime.now(timezone.utc).isoformat(),result_status,price_status,price,currency,stock_status,json.dumps(stock,ensure_ascii=False) if stock is not None else None,request_id))
            if result.rowcount==1:return True
            old=c.execute("SELECT result_status,price_status,display_price_amount,display_price_currency,stock_rf_status,stock_rf_json FROM pricing_crm_requests WHERE id=?",(request_id,)).fetchone()
            return bool(old and old[0]==result_status and old[1]==price_status and old[2]==price and old[3]==currency and old[4]==stock_status and (json.loads(old[5]) if old[5] is not None else None)==stock)
    except (sqlite3.Error,ValueError):return False

def link(db,request_id,*,cart=False,order_id=None):
    if not request_id or (not cart and not order_id):return False
    with _conn(db) as c:
        row=c.execute("SELECT cart_added,order_id FROM pricing_crm_requests WHERE id=?",(request_id,)).fetchone()
        if not row or (order_id and row['order_id'] not in (None,str(order_id))):return False
        cur=c.execute("""UPDATE pricing_crm_requests SET cart_added=CASE WHEN ? THEN 1 ELSE cart_added END,
          cart_added_at=CASE WHEN ? AND cart_added_at IS NULL THEN ? ELSE cart_added_at END,
          order_id=COALESCE(order_id,?),ordered_at=CASE WHEN ? IS NOT NULL AND ordered_at IS NULL THEN ? ELSE ordered_at END,
          updated_at=? WHERE id=?""",(int(cart),int(cart),datetime.now(timezone.utc).isoformat(),order_id,order_id,datetime.now(timezone.utc).isoformat(),datetime.now(timezone.utc).isoformat(),request_id))
        return cur.rowcount==1

def query(db,*,filters,limit=50,offset=0):
    if not 1<=limit<=100 or not 0<=offset<=100000:raise ValueError("pagination")
    allowed={'source_bot':'source_bot','oem':'oem','manufacturer':'manufacturer','result_status':'result_status',
      'telegram_user_id':'telegram_user_id','source_chat_id':'source_chat_id'}
    where=[];args=[]
    for key,column in allowed.items():
        if filters.get(key) is not None:
            where.append(column+'=?');args.append(filters[key])
    if filters.get('from_date'):where.append('created_at>=?');args.append(filters['from_date'])
    if filters.get('to_date'):where.append('created_at<?');args.append(filters['to_date'])
    condition=' WHERE '+' AND '.join(where) if where else ''
    with _conn(db) as c:
        total=c.execute('SELECT COUNT(*) FROM pricing_crm_requests'+condition,args).fetchone()[0]
        rows=c.execute("""SELECT id,created_at,updated_at,source_bot,source_kind,oem,manufacturer,
           result_status,price_status,display_price_amount,display_price_currency,stock_rf_status,
           cart_added,order_id,telegram_user_id,source_chat_id FROM pricing_crm_requests"""
           +condition+' ORDER BY id DESC LIMIT ? OFFSET ?',(*args,limit,offset)).fetchall()
        out=[]
        for r in rows:
            x=dict(r)
            for key in ('telegram_user_id','source_chat_id'):
                x[key]=('***'+hashlib.sha256(str(x[key]).encode()).hexdigest()[:8]) if x[key] is not None else None
            out.append(x)
        return {'total':total,'items':out,'limit':limit,'offset':offset}
