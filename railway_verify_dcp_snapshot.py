import os,sqlite3
p=os.getenv("EXTREMIZER_ORDERS_DB_FILE","/data/extremizer_orders.db")
c=sqlite3.connect(p)
print("VERIFY_DB",p)
print("VERIFY_COUNT",c.execute("select count(*) from dcp_catalog_snapshot").fetchone()[0])
print("VERIFY_CONTROL",c.execute("select oem,name,brand,msrp_usd,qoh,qoh_is_current,catalog_type,source from dcp_catalog_snapshot where oem='417224332'").fetchall())
c.close()
