from __future__ import annotations
import json,sqlite3
from datetime import datetime,timezone
def _now(): return datetime.now(timezone.utc).isoformat()
def init(db):
 with sqlite3.connect(db) as c:
  c.executescript("""CREATE TABLE IF NOT EXISTS supplier_shortages(
 id INTEGER PRIMARY KEY AUTOINCREMENT,supplier_order_id INTEGER NOT NULL,supplier_order_item_id INTEGER NOT NULL,
 client_order_id TEXT NOT NULL,oem TEXT NOT NULL,source_warehouse_id INTEGER NOT NULL,
 ordered_qty REAL NOT NULL CHECK(ordered_qty>0),confirmed_qty REAL NOT NULL CHECK(confirmed_qty>=0),
 alternative_results_json TEXT NOT NULL DEFAULT '[]',customer_decision TEXT,
 usa_request_status TEXT NOT NULL DEFAULT 'not_requested',
 final_distribution_json TEXT NOT NULL DEFAULT '[]',status TEXT NOT NULL DEFAULT 'open',
 created_at TEXT NOT NULL,updated_at TEXT NOT NULL,
 UNIQUE(supplier_order_item_id),
 CHECK(confirmed_qty<=ordered_qty));
 CREATE TABLE IF NOT EXISTS supplier_shortage_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT,shortage_id INTEGER NOT NULL,event_type TEXT NOT NULL,
 details_json TEXT NOT NULL DEFAULT '{}',created_at TEXT NOT NULL);
 CREATE INDEX IF NOT EXISTS idx_shortage_order ON supplier_shortages(supplier_order_id,status);
 """)
def create(db,supplier_order_id,item,confirmed_qty):
 init(db); ordered=float(item["quantity"]); confirmed=float(confirmed_qty)
 if confirmed<0 or confirmed>=ordered: raise ValueError("confirmed_qty must be >=0 and < ordered_qty for a shortage")
 now=_now()
 with sqlite3.connect(db) as c:
  cur=c.execute("""INSERT INTO supplier_shortages(supplier_order_id,supplier_order_item_id,client_order_id,oem,source_warehouse_id,ordered_qty,confirmed_qty,created_at,updated_at)
 VALUES(?,?,?,?,?,?,?,?,?)""",(supplier_order_id,item["id"],item["client_order_id"],item["oem"],item["warehouse_id"],ordered,confirmed,now,now))
  sid=cur.lastrowid;c.execute("INSERT INTO supplier_shortage_events(shortage_id,event_type,details_json,created_at) VALUES(?,?,?,?)",(sid,"shortage_created",json.dumps({"ordered_qty":ordered,"confirmed_qty":confirmed},ensure_ascii=False),now))
 return get(db,sid)
def get(db,sid):
 init(db)
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row;r=c.execute("SELECT * FROM supplier_shortages WHERE id=?",(sid,)).fetchone()
 if not r: raise KeyError(sid)
 x=dict(r);x["shortage_qty"]=x["ordered_qty"]-x["confirmed_qty"]
 for k,out in (("alternative_results_json","alternatives"),("final_distribution_json","final_distribution")):x[out]=json.loads(x.pop(k) or "[]")
 return x
def admin_summary(x):
 lines=[f"⚠️ {x['oem']}","Заказано: %g шт."%x["ordered_qty"],"Подтверждено: %g шт."%x["confirmed_qty"],"Не хватает: %g шт."%x["shortage_qty"],"","🔎 Проверены остальные склады"]
 found=False
 for a in x.get("alternatives",[]):
  q=a.get("available_quantity"); status=a.get("status")
  if status=="in_stock" and q and float(q)>0:
   found=True; price=(" · %s ₽"%f"{float(a['price_rub']):,.0f}".replace(",", " ")) if a.get("price_rub") is not None else ""
   fresh=" · остаток свежий" if a.get("is_fresh") else " · остаток требует обновления"
   lines.append(f"{a.get('public_name')} — {float(q):g} шт.{price}{fresh}")
  else: lines.append(f"{a.get('public_name')} — нет")
 if not x.get("alternatives"): lines.append("Других активных складов нет")
 if not found: lines+=["","Других остатков в РФ не найдено."]
 return "\n".join(lines)

def update_context(db,sid,alternatives=None,customer_decision=None,usa_request_status=None,final_distribution=None,status=None):
 init(db); fields=[];vals=[]
 for col,val in (("alternative_results_json",None if alternatives is None else json.dumps(alternatives,ensure_ascii=False)),("customer_decision",customer_decision),("usa_request_status",usa_request_status),("final_distribution_json",None if final_distribution is None else json.dumps(final_distribution,ensure_ascii=False)),("status",status)):
  if val is not None:fields.append(col+"=?");vals.append(val)
 if not fields:return get(db,sid)
 fields.append("updated_at=?");vals.append(_now());vals.append(sid)
 with sqlite3.connect(db) as c:c.execute("UPDATE supplier_shortages SET "+",".join(fields)+" WHERE id=?",vals)
 return get(db,sid)

def decide(db,sid,decision):
 x=get(db,sid)
 allowed={"partial_refund","usa_quote","cancel_all"}
 if decision not in allowed: raise ValueError("invalid shortage decision")
 status={"partial_refund":"resolved_partial_refund","usa_quote":"usa_quote_requested","cancel_all":"resolved_cancel_all"}[decision]
 dist=([{"source":"warehouse","warehouse_id":x["source_warehouse_id"],"qty":x["confirmed_qty"]}] if decision!="cancel_all" else [])
 if decision=="usa_quote": dist.append({"source":"usa_pending_quote","qty":x["shortage_qty"]})
 now=_now()
 with sqlite3.connect(db) as c:
  c.execute("UPDATE supplier_shortages SET customer_decision=?,usa_request_status=?,final_distribution_json=?,status=?,updated_at=? WHERE id=?",
   (decision,"requested" if decision=="usa_quote" else "not_requested",json.dumps(dist,ensure_ascii=False),status,now,sid))
  c.execute("INSERT INTO supplier_shortage_events(shortage_id,event_type,details_json,created_at) VALUES(?,?,?,?)",(sid,"customer_decision",json.dumps({"decision":decision},ensure_ascii=False),now))
  c.execute("""CREATE TABLE IF NOT EXISTS supplier_financial_obligations(
   id INTEGER PRIMARY KEY AUTOINCREMENT,shortage_id INTEGER NOT NULL,client_order_id TEXT NOT NULL,oem TEXT NOT NULL,
   obligation_type TEXT NOT NULL,quantity REAL NOT NULL,status TEXT NOT NULL DEFAULT 'pending_finance_layer',created_at TEXT NOT NULL)""")
  if decision in ("partial_refund","cancel_all"):
   qty=x["shortage_qty"] if decision=="partial_refund" else x["ordered_qty"]
   c.execute("INSERT INTO supplier_financial_obligations(shortage_id,client_order_id,oem,obligation_type,quantity,created_at) VALUES(?,?,?,?,?,?)",
    (sid,x["client_order_id"],x["oem"],"refund",qty,now))
 return get(db,sid)

def set_usa_quote(db,sid,*,status,unit_price_rub=None,total_price_rub=None,dp_usd=None,source=None):
 init(db); now=_now()
 with sqlite3.connect(db) as c:
  cols={r[1] for r in c.execute("PRAGMA table_info(supplier_shortages)")}
  for name,typ in (("usa_unit_price_rub","INTEGER"),("usa_total_price_rub","INTEGER"),("usa_dp_usd","REAL"),("usa_price_source","TEXT")):
   if name not in cols:c.execute(f"ALTER TABLE supplier_shortages ADD COLUMN {name} {typ}")
  c.execute("""UPDATE supplier_shortages SET usa_request_status=?,usa_unit_price_rub=?,usa_total_price_rub=?,usa_dp_usd=?,usa_price_source=?,updated_at=? WHERE id=?""",
   (status,unit_price_rub,total_price_rub,dp_usd,source,now,sid))
  c.execute("INSERT INTO supplier_shortage_events(shortage_id,event_type,details_json,created_at) VALUES(?,?,?,?)",
   (sid,"usa_quote_"+status,json.dumps({"unit_price_rub":unit_price_rub,"total_price_rub":total_price_rub,"source":source},ensure_ascii=False),now))
 return get(db,sid)

def decide_usa_quote(db,sid,accept):
 x=get(db,sid)
 if x.get("usa_request_status")!="quoted": raise ValueError("USA quote is not ready")
 decision="accepted" if accept else "declined"; now=_now()
 dist=[{"source":"warehouse","warehouse_id":x["source_warehouse_id"],"qty":x["confirmed_qty"]}]
 if accept:dist.append({"source":"usa","qty":x["shortage_qty"],"unit_price_rub":x.get("usa_unit_price_rub"),"total_price_rub":x.get("usa_total_price_rub")})
 with sqlite3.connect(db) as c:
  c.execute("UPDATE supplier_shortages SET usa_request_status=?,final_distribution_json=?,status=?,updated_at=? WHERE id=?",
   (decision,json.dumps(dist,ensure_ascii=False),"resolved_usa" if accept else "resolved_usa_declined",now,sid))
  c.execute("INSERT INTO supplier_shortage_events(shortage_id,event_type,details_json,created_at) VALUES(?,?,?,?)",(sid,"usa_quote_decision",json.dumps({"accepted":bool(accept)}),now))
 return get(db,sid)

def unresolved_for_supplier(db,supplier_order_id):
 init(db)
 with sqlite3.connect(db) as c:
  c.row_factory=sqlite3.Row
  rows=c.execute("""SELECT * FROM supplier_shortages WHERE supplier_order_id=?
    AND status IN ('open','waiting_customer','usa_quote_requested','price_pending','quoted') ORDER BY id""",(supplier_order_id,)).fetchall()
 return [get(db,int(r["id"])) for r in rows]

def lifecycle_state(db,supplier_order_id):
 rows=unresolved_for_supplier(db,supplier_order_id)
 return {"blocked":bool(rows),"label":"⚠️ Есть недопоставка" if rows else None,"shortages":rows}
