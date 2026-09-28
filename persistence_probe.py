import os, sqlite3, sys, time
DB=os.getenv("EXTREMIZER_ORDERS_DB_FILE","/data/extremizer_orders.db")
MODE=os.getenv("PERSISTENCE_TEST_MODE","write")
MARKER="extremizer-persistence-v1"
os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
con=sqlite3.connect(DB)
con.execute("CREATE TABLE IF NOT EXISTS persistence_probe (k TEXT PRIMARY KEY, v TEXT NOT NULL, updated_at TEXT NOT NULL)")
if MODE=="write":
    ts=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    con.execute("INSERT INTO persistence_probe(k,v,updated_at) VALUES('marker',?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v,updated_at=excluded.updated_at",(MARKER,ts))
    con.commit()
    print("PERSISTENCE_WRITE_OK", DB, MARKER, ts, flush=True)
elif MODE=="read":
    row=con.execute("SELECT v,updated_at FROM persistence_probe WHERE k='marker'").fetchone()
    print("PERSISTENCE_READ", DB, row, flush=True)
    if not row or row[0]!=MARKER:
        sys.exit(2)
    print("PERSISTENCE_CONFIRMED", flush=True)
else:
    raise SystemExit("bad mode")
con.close()
