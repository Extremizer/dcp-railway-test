"""Persistent OEMixiBOT v1 store: items, status history, tracking, shipments, arrivals."""
import os, sqlite3
from oemixibot_finance import FinanceEngine, money
from client_finance_schema import ensure_client_finance_schema
from contextlib import contextmanager
from datetime import datetime, timezone

DEFAULT_DB = os.getenv("OEMIXIBOT_DB_PATH", "/data/oemixibot.db")

def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

class OemixiStore:
    def __init__(self, path=DEFAULT_DB):
        self.path = path

    @contextmanager
    def db(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
    def init(self):
        ensure_client_finance_schema(self.path)
        with self.db() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS dealers(
 id TEXT PRIMARY KEY, telegram_id INTEGER UNIQUE, name TEXT NOT NULL,
 price_coefficient REAL NOT NULL DEFAULT 1.24, active INTEGER NOT NULL DEFAULT 1,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dealer_orders(
 id TEXT PRIMARY KEY, dealer_id TEXT NOT NULL REFERENCES dealers(id),
 created_at TEXT NOT NULL, confirmed_at TEXT, status TEXT NOT NULL DEFAULT 'Принята',
 total_usd REAL NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS item_slices(
 id INTEGER PRIMARY KEY AUTOINCREMENT, order_id TEXT NOT NULL REFERENCES dealer_orders(id),
 parent_slice_id INTEGER REFERENCES item_slices(id), oem TEXT NOT NULL, brand TEXT,
 item_type TEXT, qty INTEGER NOT NULL CHECK(qty>0), status TEXT NOT NULL,
 exception_status TEXT, supplier_tracking_id INTEGER, usa_shipment_id INTEGER,
 created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
""")
            # Immutable commercial snapshot fields for each order line.
            for col,typ in (("dl_usd","REAL"),("coefficient","REAL"),("unit_price_usd","REAL"),("line_total_usd","REAL")):
                if not self._has_column(c,"item_slices",col):
                    c.execute(f"ALTER TABLE item_slices ADD COLUMN {col} {typ}")
            c.executescript("""
CREATE TABLE IF NOT EXISTS item_status_history(
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_slice_id INTEGER NOT NULL REFERENCES item_slices(id),
 from_status TEXT, to_status TEXT NOT NULL, reason TEXT, actor TEXT,
 created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS supplier_trackings(
 id INTEGER PRIMARY KEY AUTOINCREMENT, carrier TEXT, tracking_number TEXT NOT NULL UNIQUE,
 received_us_at TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usa_shipments(
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
 delivery_method TEXT NOT NULL, departed_at TEXT, arrived_moscow_at TEXT,
 status TEXT NOT NULL DEFAULT 'draft', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS shipment_items(
 shipment_id INTEGER NOT NULL REFERENCES usa_shipments(id),
 item_slice_id INTEGER NOT NULL REFERENCES item_slices(id), qty INTEGER NOT NULL CHECK(qty>0),
 PRIMARY KEY(shipment_id,item_slice_id));
""")
            c.executescript("""
CREATE TABLE IF NOT EXISTS dealer_arrivals(
 id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL REFERENCES usa_shipments(id),
 dealer_id TEXT NOT NULL REFERENCES dealers(id), state TEXT NOT NULL DEFAULT 'unbilled',
 actual_weight_kg REAL, volume_weight_kg REAL, delivery_usd REAL,
 calculated_at TEXT, created_at TEXT NOT NULL,
 UNIQUE(shipment_id,dealer_id));
CREATE TABLE IF NOT EXISTS arrival_items(
 arrival_id INTEGER NOT NULL REFERENCES dealer_arrivals(id),
 item_slice_id INTEGER NOT NULL REFERENCES item_slices(id), received_qty INTEGER NOT NULL,
 PRIMARY KEY(arrival_id,item_slice_id));
CREATE TABLE IF NOT EXISTS oem_physical(
 oem TEXT PRIMARY KEY, actual_weight_kg REAL, length_cm REAL, width_cm REAL, height_cm REAL,
 volume_calculated_kg REAL, volume_effective_kg REAL, updated_at TEXT NOT NULL);
""")
    def add_dealer(self, dealer_id, telegram_id, name, coefficient=1.24):
        with self.db() as c:
            c.execute("INSERT INTO dealers(id,telegram_id,name,price_coefficient,active,created_at) VALUES(?,?,?,?,1,?)",
                      (dealer_id, telegram_id, name, coefficient, now_iso()))

    def create_order(self, order_id, dealer_id, total_usd=0, conn=None):
        if conn is not None:
            conn.execute("INSERT INTO dealer_orders(id,dealer_id,created_at,total_usd) VALUES(?,?,?,?)",
                         (order_id,dealer_id,now_iso(),total_usd)); return
        with self.db() as c:
            self.create_order(order_id,dealer_id,total_usd,conn=c)

    def add_item(self, order_id, oem, qty, brand=None, item_type=None, status="Принята", dl_usd=None, coefficient=None, unit_price_usd=None, line_total_usd=None, conn=None):
        ts=now_iso()
        def add(c):
            cur=c.execute("""INSERT INTO item_slices(order_id,oem,brand,item_type,qty,status,created_at,updated_at,dl_usd,coefficient,unit_price_usd,line_total_usd)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(order_id,oem,brand,item_type,qty,status,ts,ts,dl_usd,coefficient,unit_price_usd,line_total_usd))
            item_id=cur.lastrowid
            c.execute("INSERT INTO item_status_history(item_slice_id,to_status,reason,actor,created_at) VALUES(?,?,?,?,?)",
                      (item_id,status,"created","system",ts))
            return item_id
        if conn is not None: return add(conn)
        with self.db() as c: return add(c)
    def set_status(self, item_id, new_status, reason=None, actor="admin"):
        ts=now_iso()
        with self.db() as c:
            row=c.execute("SELECT status FROM item_slices WHERE id=?",(item_id,)).fetchone()
            if not row: raise KeyError(item_id)
            old=row["status"]
            c.execute("UPDATE item_slices SET status=?,updated_at=? WHERE id=?",(new_status,ts,item_id))
            c.execute("""INSERT INTO item_status_history(item_slice_id,from_status,to_status,reason,actor,created_at)
                         VALUES(?,?,?,?,?,?)""",(item_id,old,new_status,reason,actor,ts))

    def create_tracking(self, number, carrier=None):
        with self.db() as c:
            cur=c.execute("INSERT INTO supplier_trackings(carrier,tracking_number,created_at) VALUES(?,?,?)",
                          (carrier,number,now_iso()))
            return cur.lastrowid

    def mark_tracking_received_us(self, tracking_id, actor="admin"):
        with self.db() as c:
            ts=now_iso(); c.execute("UPDATE supplier_trackings SET received_us_at=? WHERE id=?",(ts,tracking_id))
            ids=[r[0] for r in c.execute("SELECT id FROM item_slices WHERE supplier_tracking_id=?",(tracking_id,))]
        for item_id in ids: self.set_status(item_id,"На складе США","tracking received US",actor)
    def create_shipment(self, code, method):
        with self.db() as c:
            cur=c.execute("INSERT INTO usa_shipments(code,delivery_method,created_at) VALUES(?,?,?)",
                          (code,method,now_iso()))
            return cur.lastrowid

    def add_to_shipment(self, shipment_id, item_id, qty):
        with self.db() as c:
            shipment=c.execute("SELECT id,status FROM usa_shipments WHERE id=?",(shipment_id,)).fetchone()
            if not shipment: raise KeyError(shipment_id)
            if shipment["status"]!="draft": raise ValueError("shipment is not draft")
            row=c.execute("SELECT qty,status,usa_shipment_id FROM item_slices WHERE id=?",(item_id,)).fetchone()
            if not row: raise KeyError(item_id)
            if row["status"]!="На складе США": raise ValueError("item is not in US warehouse")
            if row["usa_shipment_id"] is not None: raise ValueError("item already assigned to USA shipment")
            if qty<=0 or qty>row["qty"]: raise ValueError("invalid qty")
        use_id=item_id if qty==row["qty"] else self.split_item(item_id,qty)
        with self.db() as c:
            c.execute("INSERT INTO shipment_items VALUES(?,?,?)",(shipment_id,use_id,qty))
            c.execute("UPDATE item_slices SET usa_shipment_id=?,updated_at=? WHERE id=?",
                      (shipment_id,now_iso(),use_id))
        return use_id

    def depart_shipment(self, shipment_id, actor="admin"):
        with self.db() as c:
            shipment=c.execute("SELECT status FROM usa_shipments WHERE id=?",(shipment_id,)).fetchone()
            if not shipment: raise KeyError(shipment_id)
            if shipment["status"]!="draft": raise ValueError("shipment already departed")
            ids=[r[0] for r in c.execute("SELECT item_slice_id FROM shipment_items WHERE shipment_id=?",(shipment_id,))]
            if not ids: raise ValueError("empty USA shipment")
            ts=now_iso(); c.execute("UPDATE usa_shipments SET status='in_transit',departed_at=? WHERE id=?",(ts,shipment_id))
        for item_id in ids: self.set_status(item_id,"Едет в Москву","shipment departed",actor)
    def save_physical(self,oem,actual=None,length=None,width=None,height=None,volume_effective=None,actor="admin",reason=None):
        dims=(length,width,height)
        if any(v is not None for v in dims) and not all(v is not None for v in dims):
            raise ValueError("length, width and height must be supplied together")
        if actual is not None and actual<=0: raise ValueError("actual weight must be positive")
        if all(v is not None for v in dims) and any(v<=0 for v in dims):
            raise ValueError("dimensions must be positive")
        self.ensure_physical_audit_schema()
        ts=now_iso()
        with self.db() as c:
            old=c.execute("SELECT * FROM oem_physical WHERE oem=?",(oem,)).fetchone()
            oldd=dict(old) if old else {}
            new_actual=actual if actual is not None else oldd.get("actual_weight_kg")
            if all(v is not None for v in dims):
                new_length,new_width,new_height=dims
                calculated=new_length*new_width*new_height/6000.0
                effective=volume_effective if volume_effective is not None else calculated
            else:
                new_length=oldd.get("length_cm"); new_width=oldd.get("width_cm"); new_height=oldd.get("height_cm")
                calculated=oldd.get("volume_calculated_kg"); effective=oldd.get("volume_effective_kg")
            c.execute("""INSERT INTO oem_physical(oem,actual_weight_kg,length_cm,width_cm,height_cm,volume_calculated_kg,volume_effective_kg,updated_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(oem) DO UPDATE SET actual_weight_kg=excluded.actual_weight_kg,
              length_cm=excluded.length_cm,width_cm=excluded.width_cm,height_cm=excluded.height_cm,
              volume_calculated_kg=excluded.volume_calculated_kg,volume_effective_kg=excluded.volume_effective_kg,
              updated_at=excluded.updated_at""",(oem,new_actual,new_length,new_width,new_height,calculated,effective,ts))
            c.execute("""INSERT INTO oem_physical_history(oem,old_actual_weight_kg,new_actual_weight_kg,
              old_length_cm,new_length_cm,old_width_cm,new_width_cm,old_height_cm,new_height_cm,
              old_volume_kg,new_volume_kg,actor,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (oem,oldd.get("actual_weight_kg"),new_actual,oldd.get("length_cm"),new_length,
               oldd.get("width_cm"),new_width,oldd.get("height_cm"),new_height,
               oldd.get("volume_effective_kg"),effective,actor,reason,ts))
        return {"oem":oem,"actual_weight_kg":new_actual,"length_cm":new_length,"width_cm":new_width,
                "height_cm":new_height,"volume_calculated_kg":calculated,"volume_effective_kg":effective}

    def ensure_physical_audit_schema(self):
        with self.db() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS oem_physical_history(
              id INTEGER PRIMARY KEY AUTOINCREMENT,oem TEXT NOT NULL,
              old_actual_weight_kg REAL,new_actual_weight_kg REAL,
              old_length_cm REAL,new_length_cm REAL,old_width_cm REAL,new_width_cm REAL,
              old_height_cm REAL,new_height_cm REAL,old_volume_kg REAL,new_volume_kg REAL,
              actor TEXT,reason TEXT,created_at TEXT NOT NULL)""")

    def assign_tracking(self,item_id,tracking_id,actor="admin"):
        with self.db() as c:
            if not c.execute("SELECT 1 FROM supplier_trackings WHERE id=?",(tracking_id,)).fetchone():
                raise KeyError(tracking_id)
            if not c.execute("SELECT 1 FROM item_slices WHERE id=?",(item_id,)).fetchone():
                raise KeyError(item_id)
            c.execute("UPDATE item_slices SET supplier_tracking_id=?,updated_at=? WHERE id=?",
                      (tracking_id,now_iso(),item_id))
        self.set_status(item_id,"Едет на склад США","supplier tracking assigned",actor)

    def receive_moscow_item(self,shipment_id,item_id,actual_weight=None,length=None,width=None,height=None,actor="admin"):
        dims=(length,width,height)
        if actual_weight is None and not all(v is not None for v in dims):
            raise ValueError("physical data required")
        if any(v is not None for v in dims) and not all(v is not None for v in dims):
            raise ValueError("length, width and height must be supplied together")
        if actual_weight is not None and actual_weight<=0: raise ValueError("actual weight must be positive")
        if all(v is not None for v in dims) and any(v<=0 for v in dims):
            raise ValueError("dimensions must be positive")
        self.ensure_physical_audit_schema()
        ts=now_iso()
        with self.db() as c:
            row=c.execute("""SELECT i.*,o.dealer_id,us.status shipment_status,si.qty shipment_qty
              FROM item_slices i JOIN dealer_orders o ON o.id=i.order_id
              JOIN shipment_items si ON si.item_slice_id=i.id AND si.shipment_id=?
              JOIN usa_shipments us ON us.id=si.shipment_id WHERE i.id=?""",(shipment_id,item_id)).fetchone()
            if not row: raise ValueError("item is not in this USA shipment")
            if row["shipment_status"]!="in_transit": raise ValueError("USA shipment is not in transit")
            if row["status"]!="Едет в Москву": raise ValueError("item is not in transit to Moscow")
            if row["shipment_qty"]!=row["qty"]: raise ValueError("partial shipment item requires discrepancy flow")
            if c.execute("""SELECT 1 FROM arrival_items ai JOIN dealer_arrivals a ON a.id=ai.arrival_id
                            WHERE a.shipment_id=? AND ai.item_slice_id=?""",(shipment_id,item_id)).fetchone():
                raise ValueError("item already received in Moscow")
            old=c.execute("SELECT * FROM oem_physical WHERE oem=?",(row["oem"],)).fetchone()
            oldd=dict(old) if old else {}
            new_actual=actual_weight if actual_weight is not None else oldd.get("actual_weight_kg")
            if all(v is not None for v in dims):
                new_length,new_width,new_height=dims
                calculated=new_length*new_width*new_height/6000.0
                effective=calculated
            else:
                new_length=oldd.get("length_cm");new_width=oldd.get("width_cm");new_height=oldd.get("height_cm")
                calculated=oldd.get("volume_calculated_kg");effective=oldd.get("volume_effective_kg")
            c.execute("""INSERT INTO oem_physical(oem,actual_weight_kg,length_cm,width_cm,height_cm,volume_calculated_kg,volume_effective_kg,updated_at)
              VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(oem) DO UPDATE SET actual_weight_kg=excluded.actual_weight_kg,
              length_cm=excluded.length_cm,width_cm=excluded.width_cm,height_cm=excluded.height_cm,
              volume_calculated_kg=excluded.volume_calculated_kg,volume_effective_kg=excluded.volume_effective_kg,
              updated_at=excluded.updated_at""",(row["oem"],new_actual,new_length,new_width,new_height,calculated,effective,ts))
            c.execute("""INSERT INTO oem_physical_history(oem,old_actual_weight_kg,new_actual_weight_kg,
              old_length_cm,new_length_cm,old_width_cm,new_width_cm,old_height_cm,new_height_cm,
              old_volume_kg,new_volume_kg,actor,reason,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (row["oem"],oldd.get("actual_weight_kg"),new_actual,oldd.get("length_cm"),new_length,
               oldd.get("width_cm"),new_width,oldd.get("height_cm"),new_height,
               oldd.get("volume_effective_kg"),effective,actor,"Moscow receiving",ts))
            c.execute("""INSERT INTO dealer_arrivals(shipment_id,dealer_id,created_at) VALUES(?,?,?)
                         ON CONFLICT(shipment_id,dealer_id) DO NOTHING""",(shipment_id,row["dealer_id"],ts))
            arrival=c.execute("SELECT id FROM dealer_arrivals WHERE shipment_id=? AND dealer_id=?",
                              (shipment_id,row["dealer_id"])).fetchone()[0]
            c.execute("INSERT INTO arrival_items(arrival_id,item_slice_id,received_qty) VALUES(?,?,?)",
                      (arrival,item_id,row["qty"]))
            old_status=row["status"]
            c.execute("UPDATE item_slices SET status=?,updated_at=? WHERE id=?",
                      ("На складе в Москве",ts,item_id))
            c.execute("""INSERT INTO item_status_history(item_slice_id,from_status,to_status,reason,actor,created_at)
                         VALUES(?,?,?,?,?,?)""",(item_id,old_status,"На складе в Москве","physically received Moscow",actor,ts))
        return {"arrival_id":arrival,"item_id":item_id,"received_qty":row["qty"],"oem":row["oem"],
                "actual_weight_kg":new_actual,"volume_weight_kg":effective}

    def confirm_moscow_item(self,shipment_id,item_id,received_qty):
        with self.db() as c:
            row=c.execute("""SELECT o.dealer_id,i.qty FROM item_slices i JOIN dealer_orders o ON o.id=i.order_id
                             WHERE i.id=?""",(item_id,)).fetchone()
            if not row: raise KeyError(item_id)
            if received_qty < 0 or received_qty > row["qty"]: raise ValueError("invalid received qty")
            dealer_id=row["dealer_id"]; ts=now_iso()
            c.execute("""INSERT INTO dealer_arrivals(shipment_id,dealer_id,created_at) VALUES(?,?,?)
                         ON CONFLICT(shipment_id,dealer_id) DO NOTHING""",(shipment_id,dealer_id,ts))
            arrival=c.execute("SELECT id FROM dealer_arrivals WHERE shipment_id=? AND dealer_id=?",
                              (shipment_id,dealer_id)).fetchone()[0]
            c.execute("INSERT OR REPLACE INTO arrival_items VALUES(?,?,?)",(arrival,item_id,received_qty))
        if received_qty == row["qty"]:
            self.set_status(item_id,"На складе в Москве","physically received Moscow","admin")
        return arrival

    def bill_arrival(self,arrival_id,actual_weight,volume_weight,delivery_usd):
        amount=money(delivery_usd)
        if amount<=0: raise ValueError('delivery amount must be positive')
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute("SELECT dealer_id,shipment_id FROM dealer_arrivals WHERE id=?",(arrival_id,)).fetchone()
            if not row: raise KeyError(arrival_id)
            code=c.execute("SELECT code FROM usa_shipments WHERE id=?",(row["shipment_id"],)).fetchone()[0]
            FinanceEngine(self.path).post_event(buyer_type='dealer',buyer_id=row['dealer_id'],
                currency='USD',event_type='DELIVERY_CHARGE',amount=-amount,
                description=f"Доставка {code}; факт {actual_weight:.2f} кг; Volume {volume_weight:.2f} кг",
                reference_type='dealer_arrival',reference_id=str(arrival_id),arrival_id=arrival_id,
                actor='system',idempotency_key=f'DELIVERY_CHARGE:arrival:{arrival_id}',conn=c)
            c.execute("""UPDATE dealer_arrivals SET state='billed',actual_weight_kg=?,volume_weight_kg=?,
                         delivery_usd=?,calculated_at=? WHERE id=?""",
                      (actual_weight,volume_weight,str(amount),now_iso(),arrival_id))

    def split_item(self,item_id,move_qty):
        with self.db() as c:
            row=c.execute("SELECT * FROM item_slices WHERE id=?",(item_id,)).fetchone()
            if not row: raise KeyError(item_id)
            if move_qty<=0 or move_qty>=row["qty"]: raise ValueError("invalid split qty")
            remain_qty=row["qty"]-move_qty
            unit=row["unit_price_usd"]
            remain_total=round(unit*remain_qty,2) if unit is not None else None
            moved_total=round(unit*move_qty,2) if unit is not None else None
            ts=now_iso()
            c.execute("UPDATE item_slices SET qty=?,line_total_usd=?,updated_at=? WHERE id=?",
                      (remain_qty,remain_total,ts,item_id))
            cur=c.execute("""INSERT INTO item_slices(order_id,parent_slice_id,oem,brand,item_type,qty,status,
                exception_status,supplier_tracking_id,usa_shipment_id,created_at,updated_at,
                dl_usd,coefficient,unit_price_usd,line_total_usd)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",(row["order_id"],item_id,row["oem"],row["brand"],row["item_type"],
                move_qty,row["status"],row["exception_status"],row["supplier_tracking_id"],row["usa_shipment_id"],ts,ts,
                row["dl_usd"],row["coefficient"],unit,moved_total))
            new_id=cur.lastrowid
            c.execute("INSERT INTO item_status_history(item_slice_id,to_status,reason,actor,created_at) VALUES(?,?,?,?,?)",
                      (new_id,row["status"],f"split from {item_id}","system",ts))
            return new_id

    def ensure_receiving_schema(self):
        with self.db() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS receiving_discrepancies(
 id INTEGER PRIMARY KEY AUTOINCREMENT, shipment_id INTEGER NOT NULL,
 item_slice_id INTEGER NOT NULL, dealer_id TEXT NOT NULL,
 expected_qty INTEGER NOT NULL, received_qty INTEGER NOT NULL,
 missing_qty INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT 'shortage',
 state TEXT NOT NULL DEFAULT 'open', note TEXT, created_at TEXT NOT NULL,
 resolved_at TEXT);
CREATE TABLE IF NOT EXISTS dealer_shipments(
 id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT NOT NULL UNIQUE,
 dealer_id TEXT NOT NULL REFERENCES dealers(id), carrier TEXT,
 tracking_number TEXT, places INTEGER, note TEXT, status TEXT NOT NULL DEFAULT 'draft',
 created_at TEXT NOT NULL, shipped_at TEXT, received_at TEXT);
CREATE TABLE IF NOT EXISTS dealer_shipment_items(
 dealer_shipment_id INTEGER NOT NULL REFERENCES dealer_shipments(id),
 item_slice_id INTEGER NOT NULL REFERENCES item_slices(id), qty INTEGER NOT NULL CHECK(qty>0),
 PRIMARY KEY(dealer_shipment_id,item_slice_id));
""")
    def receive_moscow_partial(self,shipment_id,item_id,received_qty,actor="admin"):
        self.ensure_receiving_schema()
        with self.db() as c:
            row=c.execute("""SELECT i.*,o.dealer_id FROM item_slices i
                JOIN dealer_orders o ON o.id=i.order_id WHERE i.id=?""",(item_id,)).fetchone()
            if not row: raise KeyError(item_id)
            expected=row["qty"]
            if received_qty<0 or received_qty>expected: raise ValueError("invalid received qty")
            dealer=row["dealer_id"]; ts=now_iso()
        received_id=None; missing_id=None
        if received_qty==expected:
            received_id=item_id
            self.set_status(item_id,"На складе в Москве","physically received Moscow",actor)
        elif received_qty>0:
            received_id=self.split_item(item_id,received_qty)
            self.set_status(received_id,"На складе в Москве","partial physically received Moscow",actor)
            missing_id=item_id
        else:
            missing_id=item_id
        with self.db() as c:
            c.execute("""INSERT INTO dealer_arrivals(shipment_id,dealer_id,created_at)
                         VALUES(?,?,?) ON CONFLICT(shipment_id,dealer_id) DO NOTHING""",(shipment_id,dealer,ts))
            arrival=c.execute("SELECT id FROM dealer_arrivals WHERE shipment_id=? AND dealer_id=?",
                              (shipment_id,dealer)).fetchone()[0]
            if received_id:
                c.execute("INSERT OR REPLACE INTO arrival_items VALUES(?,?,?)",(arrival,received_id,received_qty))
            if received_qty<expected:
                c.execute("""INSERT INTO receiving_discrepancies(shipment_id,item_slice_id,dealer_id,
                    expected_qty,received_qty,missing_qty,created_at) VALUES(?,?,?,?,?,?,?)""",
                    (shipment_id,missing_id,dealer,expected,received_qty,expected-received_qty,ts))
        return {"arrival_id":arrival,"received_item_id":received_id,"missing_item_id":missing_id,
                "expected":expected,"received":received_qty,"missing":expected-received_qty}
    def shipment_receiving_summary(self,shipment_id):
        self.ensure_receiving_schema()
        with self.db() as c:
            rows=c.execute("""SELECT a.id arrival_id,a.dealer_id,d.name dealer_name,a.state,
              COALESCE(SUM(ai.received_qty),0) received_qty,a.actual_weight_kg,a.volume_weight_kg,a.delivery_usd
              FROM dealer_arrivals a JOIN dealers d ON d.id=a.dealer_id
              LEFT JOIN arrival_items ai ON ai.arrival_id=a.id
              WHERE a.shipment_id=? GROUP BY a.id ORDER BY d.name""",(shipment_id,)).fetchall()
            problems=c.execute("""SELECT dealer_id,SUM(missing_qty) missing_qty,COUNT(*) issues
              FROM receiving_discrepancies WHERE shipment_id=? AND state='open' GROUP BY dealer_id""",(shipment_id,)).fetchall()
        return {"dealers":[dict(x) for x in rows],"discrepancies":[dict(x) for x in problems]}

    def create_dealer_shipment(self,code,dealer_id,carrier=None,tracking=None,places=None,note=None):
        self.ensure_receiving_schema()
        with self.db() as c:
            cur=c.execute("""INSERT INTO dealer_shipments(code,dealer_id,carrier,tracking_number,places,note,created_at)
              VALUES(?,?,?,?,?,?,?)""",(code,dealer_id,carrier,tracking,places,note,now_iso()))
            return cur.lastrowid
    def add_to_dealer_shipment(self,ds_id,item_id,qty):
        self.ensure_receiving_schema()
        with self.db() as c:
            row=c.execute("""SELECT i.qty,i.status,o.dealer_id,ds.dealer_id target_dealer
              FROM item_slices i JOIN dealer_orders o ON o.id=i.order_id
              JOIN dealer_shipments ds ON ds.id=? WHERE i.id=?""",(ds_id,item_id)).fetchone()
            if not row: raise KeyError(item_id)
            ds=c.execute("SELECT status FROM dealer_shipments WHERE id=?",(ds_id,)).fetchone()
            if not ds or ds["status"]!="draft": raise ValueError("dealer shipment is not draft")
            if row["dealer_id"]!=row["target_dealer"]: raise ValueError("wrong dealer")
            if row["status"]!="На складе в Москве": raise ValueError("item is not in Moscow warehouse")
            if qty<=0 or qty>row["qty"]: raise ValueError("invalid qty")
        use_id=item_id if qty==row["qty"] else self.split_item(item_id,qty)
        with self.db() as c:
            c.execute("INSERT INTO dealer_shipment_items VALUES(?,?,?)",(ds_id,use_id,qty))
        return use_id

    def ship_to_dealer(self,ds_id,actor="admin"):
        self.ensure_receiving_schema()
        with self.db() as c:
            ds=c.execute("SELECT status FROM dealer_shipments WHERE id=?",(ds_id,)).fetchone()
            if not ds: raise KeyError(ds_id)
            if ds["status"]!="draft": raise ValueError("dealer shipment already dispatched")
            ids=[r[0] for r in c.execute("SELECT item_slice_id FROM dealer_shipment_items WHERE dealer_shipment_id=?",(ds_id,))]
            if not ids: raise ValueError("empty dealer shipment")
            c.execute("UPDATE dealer_shipments SET status='shipped',shipped_at=? WHERE id=?",(now_iso(),ds_id))
        for item_id in ids: self.set_status(item_id,"\u041e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d \u0434\u0438\u043b\u0435\u0440\u0443","dealer shipment dispatched",actor)

    def confirm_dealer_receipt(self,ds_id,received_by_item,actor="dealer"):
        self.ensure_receiving_schema()
        results=[]
        with self.db() as c:
            ds=c.execute("SELECT status FROM dealer_shipments WHERE id=?",(ds_id,)).fetchone()
            if not ds: raise KeyError(ds_id)
            if ds["status"]=="received": raise ValueError("dealer shipment already received")
            if ds["status"]!="shipped": raise ValueError("dealer shipment is not shipped")
            rows=c.execute("""SELECT dsi.item_slice_id,dsi.qty FROM dealer_shipment_items dsi
                              WHERE dsi.dealer_shipment_id=?""",(ds_id,)).fetchall()
            valid_ids=[r["item_slice_id"] for r in rows]
            for supplied_id in received_by_item:
                if int(supplied_id) not in valid_ids: raise ValueError("item is not in dealer shipment")
        for row in rows:
            item_id=row["item_slice_id"]; sent=row["qty"]; got=int(received_by_item.get(item_id,0))
            if got<0 or got>sent: raise ValueError("invalid dealer received qty")
            if got==sent:
                self.set_status(item_id,"Получен дилером","dealer physically confirmed",actor)
                results.append((item_id,got,0))
            elif got>0:
                received_id=self.split_item(item_id,got)
                self.set_status(received_id,"Получен дилером","dealer partial physical confirmation",actor)
                results.append((received_id,got,sent-got))
            else:
                results.append((item_id,0,sent))
        if all(missing==0 for _,_,missing in results):
            with self.db() as c:
                c.execute("UPDATE dealer_shipments SET status='received',received_at=? WHERE id=?",(now_iso(),ds_id))
        return results

    def ensure_commercial_schema(self):
        with self.db() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS item_delays(
 item_slice_id INTEGER PRIMARY KEY REFERENCES item_slices(id),
 delay_since TEXT NOT NULL, expected_date TEXT, open_date INTEGER NOT NULL DEFAULT 0,
 dealer_decision TEXT, decision_at TEXT, cancel_request_state TEXT,
 note TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS item_financial_events(
 id INTEGER PRIMARY KEY AUTOINCREMENT, item_slice_id INTEGER NOT NULL REFERENCES item_slices(id),
 event_type TEXT NOT NULL, amount_usd REAL, state TEXT NOT NULL DEFAULT 'pending',
 note TEXT, created_at TEXT NOT NULL);
""")

    def set_delayed(self,item_id,expected_date=None,note=None,actor="admin"):
        self.ensure_commercial_schema()
        ts=now_iso(); open_date=0 if expected_date else 1
        self.set_status(item_id,"Задержка","supplier backorder",actor)
        with self.db() as c:
            c.execute("""INSERT INTO item_delays(item_slice_id,delay_since,expected_date,open_date,note,updated_at)
              VALUES(?,?,?,?,?,?) ON CONFLICT(item_slice_id) DO UPDATE SET expected_date=excluded.expected_date,
              open_date=excluded.open_date,note=excluded.note,updated_at=excluded.updated_at""",
              (item_id,ts,expected_date,open_date,note,ts))
        return {"item_id":item_id,"expected_date":expected_date,"open_date":bool(open_date)}

    def dealer_delay_decision(self,item_id,decision):
        self.ensure_commercial_schema()
        if decision not in ("wait","request_cancel"): raise ValueError("invalid decision")
        with self.db() as c:
            row=c.execute("SELECT item_slice_id FROM item_delays WHERE item_slice_id=?",(item_id,)).fetchone()
            if not row: raise ValueError("item is not delayed")
            ts=now_iso(); cancel_state="requested" if decision=="request_cancel" else None
            c.execute("""UPDATE item_delays SET dealer_decision=?,decision_at=?,cancel_request_state=?,updated_at=?
                         WHERE item_slice_id=?""",(decision,ts,cancel_state,ts,item_id))
    def _atomic_status_and_refund(self,c,item_id,to_status,reason_code,status_reason,actor):
        row=c.execute("""SELECT s.status,s.order_id,s.oem,s.line_total_usd,o.dealer_id
                         FROM item_slices s JOIN dealer_orders o ON o.id=s.order_id WHERE s.id=?""",(item_id,)).fetchone()
        if not row: raise KeyError(item_id)
        amount=round(float(row["line_total_usd"] or 0),2)
        if amount<=0: raise ValueError("slice has no refundable amount")
        ts=now_iso()
        c.execute("UPDATE item_slices SET status=?,updated_at=? WHERE id=?",(to_status,ts,item_id))
        c.execute("INSERT INTO item_status_history(item_slice_id,from_status,to_status,reason,actor,created_at) VALUES(?,?,?,?,?,?)",
                  (item_id,row["status"],to_status,status_reason,actor,ts))
        FinanceEngine(self.path).auto_refund(buyer_type='dealer',buyer_id=row['dealer_id'],currency='USD',slice_id=item_id,reason_code=reason_code,actor=actor,conn=c)

    def approve_dealer_refusal(self,item_id,actor="admin"):
        self.ensure_commercial_schema()
        with self.db() as c:
            row=c.execute("SELECT cancel_request_state FROM item_delays WHERE item_slice_id=?",(item_id,)).fetchone()
            if not row or row["cancel_request_state"]!="requested": raise ValueError("no cancellation request")
            c.execute("UPDATE item_delays SET cancel_request_state='approved',updated_at=? WHERE item_slice_id=?",(now_iso(),item_id))
            self._atomic_status_and_refund(c,item_id,"Отказ дилером","refusal","dealer cancellation approved",actor)

    def set_unavailable(self,item_id,refund_usd=None,actor="admin"):
        self.ensure_commercial_schema()
        with self.db() as c:
            self._atomic_status_and_refund(c,item_id,"Не поставляется","unavailable","supplier unavailable",actor)

    def _confirm_order_and_charge_conn(self,c,order_id):
        row=c.execute("SELECT dealer_id,total_usd,confirmed_at FROM dealer_orders WHERE id=?",(order_id,)).fetchone()
        if not row: raise KeyError(order_id)
        if row["confirmed_at"]: return False
        amount=round(float(row["total_usd"]),2)
        finance=FinanceEngine(self.path)
        finance.require_order_capacity(buyer_id=row['dealer_id'],buyer_type='dealer',currency='USD',amount=amount,conn=c)
        ts=now_iso()
        if self._has_column(c,'dealer_orders','updated_at'):
            c.execute("UPDATE dealer_orders SET confirmed_at=?,status='Подтвержден',updated_at=? WHERE id=?",(ts,ts,order_id))
        else:
            c.execute("UPDATE dealer_orders SET confirmed_at=?,status='Подтвержден' WHERE id=?",(ts,order_id))
        finance.post_event(buyer_type='dealer',buyer_id=row['dealer_id'],currency='USD',event_type='ORDER_CHARGE',
            amount=-abs(amount),description=f'Заказ {order_id}; размещён {ts[:10]}',reference_type='order',reference_id=order_id,
            order_id=order_id,actor='system',reason='order confirmed',idempotency_key=f'ORDER_CHARGE:order:{order_id}',conn=c)
        return True

    def confirm_order_and_charge(self,order_id,conn=None):
        if conn is not None: return self._confirm_order_and_charge_conn(conn,order_id)
        self.ensure_commercial_schema()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            return self._confirm_order_and_charge_conn(c,order_id)

    @staticmethod
    def _has_column(c,table,column):
        return any(r[1]==column for r in c.execute(f"PRAGMA table_info({table})"))

    def orders_for_buyer(self,buyer_id,buyer_type="dealer",channel="telegram",limit=20):
        if buyer_type!="dealer": raise ValueError("legacy adapter supports dealer only")
        with self.db() as c:
            rows=c.execute("""SELECT o.id,o.status,o.total_usd,o.created_at,o.confirmed_at,
              COALESCE(SUM(i.qty),0) total_qty,COUNT(DISTINCT i.oem) oem_count
              FROM dealer_orders o LEFT JOIN item_slices i ON i.order_id=o.id
              WHERE o.dealer_id=? GROUP BY o.id ORDER BY o.created_at DESC,o.id DESC LIMIT ?""",
              (buyer_id,int(limit))).fetchall()
        return [{"id":r["id"],"buyer_type":"dealer","channel":channel,"fulfillment":"usa",
                 "sell_currency":"USD","sell_total":float(r["total_usd"] or 0),
                 "status":r["status"],"created_at":r["created_at"],"confirmed_at":r["confirmed_at"],
                 "total_qty":int(r["total_qty"] or 0),"oem_count":int(r["oem_count"] or 0)} for r in rows]

    def order_for_buyer(self,order_id,buyer_id,buyer_type="dealer",channel="telegram"):
        if buyer_type!="dealer": raise ValueError("legacy adapter supports dealer only")
        with self.db() as c:
            order=c.execute("SELECT * FROM dealer_orders WHERE id=? AND dealer_id=?",(order_id,buyer_id)).fetchone()
            if not order: return None
            items=c.execute("""SELECT oem,brand,item_type,qty,status,unit_price_usd,line_total_usd
                               FROM item_slices WHERE order_id=? ORDER BY id""",(order_id,)).fetchall()
        return {"id":order["id"],"buyer_type":"dealer","channel":channel,"fulfillment":"usa",
                "sell_currency":"USD","sell_total":float(order["total_usd"] or 0),
                "status":order["status"],"created_at":order["created_at"],"confirmed_at":order["confirmed_at"],
                "items":[dict(x) for x in items]}

    def receivable_shipment_for_buyer(self,order_id,buyer_id):
        with self.db() as c:
            row=c.execute("""SELECT DISTINCT ds.id FROM dealer_shipments ds
              JOIN dealer_shipment_items dsi ON dsi.dealer_shipment_id=ds.id
              JOIN item_slices s ON s.id=dsi.item_slice_id
              JOIN dealer_orders o ON o.id=s.order_id
              WHERE o.id=? AND o.dealer_id=? AND ds.dealer_id=? AND ds.status='shipped'
              ORDER BY ds.id LIMIT 1""",(order_id,buyer_id,buyer_id)).fetchone()
        return dict(row) if row else None

    def confirm_receipt_for_buyer(self,order_id,buyer_id):
        shipment=self.receivable_shipment_for_buyer(order_id,buyer_id)
        if not shipment: raise ValueError("no shipped dealer shipment for this order")
        with self.db() as c:
            rows=c.execute("SELECT item_slice_id,qty FROM dealer_shipment_items WHERE dealer_shipment_id=?",(shipment["id"],)).fetchall()
        received={r["item_slice_id"]:r["qty"] for r in rows}
        return shipment,self.confirm_dealer_receipt(shipment["id"],received,actor="dealer")

    def order_history_for_buyer(self,order_id,buyer_id,buyer_type="dealer",channel="telegram"):
        if buyer_type!="dealer": raise ValueError("legacy adapter supports dealer only")
        with self.db() as c:
            order=c.execute("SELECT id FROM dealer_orders WHERE id=? AND dealer_id=?",(order_id,buyer_id)).fetchone()
            if not order: return None
            rows=c.execute("""SELECT s.id,s.oem,s.qty,h.from_status,h.to_status,h.created_at
              FROM item_slices s JOIN item_status_history h ON h.item_slice_id=s.id
              WHERE s.order_id=? ORDER BY s.id,h.created_at,h.id""",(order_id,)).fetchall()
        groups=[]; by_id={}
        for r in rows:
            sid=r["id"]
            if sid not in by_id:
                by_id[sid]={"oem":r["oem"],"qty":int(r["qty"]),"events":[]}; groups.append(by_id[sid])
            by_id[sid]["events"].append({"from_status":r["from_status"],"to_status":r["to_status"],"created_at":r["created_at"]})
        return {"id":order_id,"buyer_type":"dealer","channel":channel,"items":groups}

    def ensure_cart_schema(self):
        with self.db() as c:
            c.executescript("""
CREATE TABLE IF NOT EXISTS dealer_carts(
 dealer_id TEXT PRIMARY KEY REFERENCES dealers(id), updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dealer_cart_items(
 id INTEGER PRIMARY KEY AUTOINCREMENT, dealer_id TEXT NOT NULL REFERENCES dealers(id),
 oem TEXT NOT NULL, brand TEXT, name TEXT, item_type TEXT,
 dl_usd REAL NOT NULL, coefficient REAL NOT NULL, dlp_usd REAL NOT NULL,
 qty INTEGER NOT NULL CHECK(qty>0), created_at TEXT NOT NULL,
 UNIQUE(dealer_id,oem));
""")

    def cart_add(self,dealer_id,oem,dl_usd,brand=None,name=None,item_type=None,qty=1):
        self.ensure_cart_schema()
        with self.db() as c:
            dealer=c.execute("SELECT price_coefficient FROM dealers WHERE id=? AND active=1",(dealer_id,)).fetchone()
            if not dealer: raise ValueError("dealer unavailable")
            coeff=float(dealer["price_coefficient"]); dlp=round(float(dl_usd)*coeff+1e-9,2); ts=now_iso()
            c.execute("INSERT INTO dealer_carts(dealer_id,updated_at) VALUES(?,?) ON CONFLICT(dealer_id) DO UPDATE SET updated_at=excluded.updated_at",(dealer_id,ts))
            c.execute("""INSERT INTO dealer_cart_items(dealer_id,oem,brand,name,item_type,dl_usd,coefficient,dlp_usd,qty,created_at)
              VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(dealer_id,oem) DO UPDATE SET qty=dealer_cart_items.qty+excluded.qty,
              dl_usd=excluded.dl_usd,coefficient=excluded.coefficient,dlp_usd=excluded.dlp_usd""",
              (dealer_id,oem,brand,name,item_type,dl_usd,coeff,dlp,qty,ts))
            return dlp

    def cart_set_qty(self,dealer_id,oem,qty):
        self.ensure_cart_schema()
        with self.db() as c:
            if qty<=0: c.execute("DELETE FROM dealer_cart_items WHERE dealer_id=? AND oem=?",(dealer_id,oem))
            else: c.execute("UPDATE dealer_cart_items SET qty=? WHERE dealer_id=? AND oem=?",(qty,dealer_id,oem))

    def cart_view(self,dealer_id):
        self.ensure_cart_schema()
        with self.db() as c:
            rows=[dict(r) for r in c.execute("SELECT * FROM dealer_cart_items WHERE dealer_id=? ORDER BY id",(dealer_id,))]
        return {"items":rows,"total_usd":round(sum(r["dlp_usd"]*r["qty"] for r in rows),2)}

    def checkout_cart(self,dealer_id,order_id):
        self.ensure_commercial_schema(); self.ensure_cart_schema()
        with self.db() as c:
            c.execute('BEGIN IMMEDIATE')
            items=[dict(r) for r in c.execute("SELECT * FROM dealer_cart_items WHERE dealer_id=? ORDER BY id",(dealer_id,)).fetchall()]
            if not items: raise ValueError("empty cart")
            total=round(sum(float(r["dlp_usd"])*int(r["qty"]) for r in items),2)
            FinanceEngine(self.path).require_order_capacity(buyer_id=dealer_id,buyer_type='dealer',currency='USD',amount=total,conn=c)
            self.create_order(order_id,dealer_id,total,conn=c)
            for r in items:
                self.add_item(order_id,r["oem"],r["qty"],r["brand"],r["item_type"],"Принята",r["dl_usd"],r["coefficient"],r["dlp_usd"],round(r["dlp_usd"]*r["qty"],2),conn=c)
            self.confirm_order_and_charge(order_id,conn=c)
            c.execute("DELETE FROM dealer_cart_items WHERE dealer_id=?",(dealer_id,))
            return total
