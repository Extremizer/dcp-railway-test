import sqlite3,shutil,datetime,os
DB="/data/extremizer_orders.db"
ROWS=[["860200987","Heavy-Duty Front Bumper With 2'' Receiver - (Skandic WT, SWT)",309.99,"Accessories"],["9779335","BLACK PLASTIC RESTORER 7.6 FL OZ/225 ML",12.49,"Accessories"],["4548660492","APEX X-TEAM EDITION T-SHIRT MEN S",31.99,"Apparel"],["4548660607","APEX X-TEAM EDITION T-SHIRT MEN M",31.99,"Apparel"],["4548660907","APEX X-TEAM EDITION T-SHIRT MEN L",31.99,"Apparel"],["4548660926","APEX X-TEAM EDITION T-SHIRT MEN L",31.99,"Apparel"],["4548660992","APEX X-TEAM EDITION T-SHIRT MEN L",31.99,"Apparel"],["4548661207","APEX X-TEAM EDITION T-SHIRT MEN XL",31.99,"Apparel"],["4548661292","APEX X-TEAM EDITION T-SHIRT MEN XL",31.99,"Apparel"],["4548661426","APEX X-TEAM EDITION T-SHIRT MEN 2XL",31.99,"Apparel"],["4548661492","APEX X-TEAM EDITION T-SHIRT MEN 2XL",31.99,"Apparel"],["4548661607","APEX X-TEAM EDITION T-SHIRT MEN 3XL",31.99,"Apparel"],["4548661626","APEX X-TEAM EDITION T-SHIRT MEN 3XL",31.99,"Apparel"],["4548661692","APEX X-TEAM EDITION T-SHIRT MEN 3XL",31.99,"Apparel"]]
c=sqlite3.connect(DB)
missing=[r for r in ROWS if not c.execute("select 1 from oem_catalog_cache where manufacturer='Ski-Doo' and current_oem=?",(r[0],)).fetchone()]
if missing:
 stamp=datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ"); bak=f"/data/extremizer_orders.pre_probe14_{stamp}.db"; c.close(); shutil.copy2(DB,bak); c=sqlite3.connect(DB); c.execute("BEGIN IMMEDIATE")
 for oem,name,msrp,catalog in missing:
  c.execute("""insert into oem_catalog_cache(manufacturer,current_oem,name,catalog,msrp_usd,msrp_verified,previous_oems_json,source_kind,source_ref,first_seen_at,last_verified_at) values('Ski-Doo',?,?,?,?,1,'[]','validated_saved_dcp_probe','dealercostparts_catalog_probe_v4_output.txt','2026-09-22','2026-09-22')""",(oem,name,catalog,msrp))
  c.execute("""insert into oem_catalog_aliases(manufacturer,alias_oem,current_oem,alias_kind) values('Ski-Doo',?,?,'current') on conflict(manufacturer,alias_oem) do nothing""",(oem,oem))
 c.commit(); print("PROBE14_IMPORT backup=",bak," added=",len(missing))
else: print("PROBE14_IMPORT no-op already present")

# Verified dealer-price snapshot captured from the user's authorized DCP session.
# The verification timestamp is fixed intentionally: future deployments must
# never make this historical DP look fresher than it really is.
DP_VERIFIED_AT="2026-10-03T20:27:00+00:00"
c.execute("""insert into dealer_price_cache(
 manufacturer,oem,dealer_price_usd,source,first_seen_at,last_verified_at,last_used_at,use_count
) values(?,?,?,?,?,?,NULL,0)
on conflict(manufacturer,oem) do update set
 dealer_price_usd=excluded.dealer_price_usd,
 source=excluded.source,
 last_verified_at=excluded.last_verified_at
where excluded.last_verified_at > dealer_price_cache.last_verified_at
""",(
 "Ski-Doo","417300571",184.06,
 "verified_local_dcp_human:live_dom",
 DP_VERIFIED_AT,DP_VERIFIED_AT
))
c.commit()
print("PROBE14_DP_CONTROL",c.execute(
 "select oem,dealer_price_usd,last_verified_at from dealer_price_cache where manufacturer='Ski-Doo' and oem='417300571'"
).fetchone())
print("PROBE14_CACHE_TOTAL",c.execute("select count(*) from oem_catalog_cache").fetchone()[0])
print("PROBE14_CONTROL",c.execute("select current_oem,catalog,msrp_usd from oem_catalog_cache where current_oem in ('860200987','4548660492') order by current_oem").fetchall()); c.close()
