"""Bounded durable transport outbox in the producer's EXISTING persistent runtime DB.

This is a delivery queue, never a second CRM or reporting database.
No standalone database creation: existing DB file required.
"""
import json,sqlite3,time
from pathlib import Path
from contextlib import contextmanager

MAX_ATTEMPTS=5
@contextmanager
def database(path):
    if not Path(path).is_file():
        raise FileNotFoundError("existing_producer_db_required")
    c=sqlite3.connect(str(path),timeout=3)
    try:
        with c:yield c
    finally:c.close()

def initialize(path):
    with database(path) as c:
        c.execute("""CREATE TABLE IF NOT EXISTS analytics_delivery_outbox(
          delivery_key TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
          attempts INTEGER NOT NULL DEFAULT 0, next_attempt REAL NOT NULL DEFAULT 0,
          error_code TEXT, state TEXT NOT NULL DEFAULT 'PENDING',
          created_at REAL NOT NULL)""")

def enqueue(path,delivery_key,payload):
    if not delivery_key or not isinstance(payload,dict):raise ValueError("bad_delivery")
    body=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False)
    with database(path) as c:
        row=c.execute("SELECT payload_json FROM analytics_delivery_outbox WHERE delivery_key=?",(delivery_key,)).fetchone()
        if row:
            if row[0]!=body:raise ValueError("delivery_identity_conflict")
            return False
        c.execute("INSERT INTO analytics_delivery_outbox(delivery_key,payload_json,created_at) VALUES(?,?,?)",
                  (delivery_key,body,time.time()))
        return True

def flush(path,send,*,now=None,batch_size=20):
    if not callable(send) or not 1<=batch_size<=100:raise ValueError("invalid_sender")
    when=time.time() if now is None else float(now)
    outcome={'delivered':0,'deferred':0,'terminal':0}
    with database(path) as c:
        candidates=c.execute("""SELECT delivery_key,payload_json,attempts
          FROM analytics_delivery_outbox
          WHERE state='PENDING' AND next_attempt<=?
          ORDER BY created_at LIMIT ?""",(when,batch_size)).fetchall()
    for key,body,attempts in candidates:
        try:
            accepted=bool(send(json.loads(body)))
            code='OK' if accepted else 'TRANSPORT_UNAVAILABLE'
        except Exception as exc:
            accepted=False
            code=type(exc).__name__[:40]
        with database(path) as c:
            if accepted:
                c.execute("UPDATE analytics_delivery_outbox SET state='SENT',error_code=NULL WHERE delivery_key=? AND state='PENDING'",(key,))
                outcome['delivered']+=1
            else:
                tries=attempts+1
                exhausted=tries>=MAX_ATTEMPTS
                c.execute("""UPDATE analytics_delivery_outbox SET attempts=?, error_code=?,
                  state=?, next_attempt=? WHERE delivery_key=? AND state='PENDING'""",
                  (tries,code,'EXHAUSTED' if exhausted else 'PENDING',when+min(3600,2**tries),key))
                outcome['terminal' if exhausted else 'deferred']+=1
    return outcome
