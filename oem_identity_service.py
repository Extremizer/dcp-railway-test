"""Persistent canonical OEM identity learned only from confirmed sources."""
import os, sqlite3
from pathlib import Path
import dealercostparts_manufacturer_finder_v6_6 as finder
DB=Path(os.getenv("OEMIXIBOT_IDENTITY_DB","").strip() or Path(__file__).with_name("oemixibot_identity.db"))
def init():
    with sqlite3.connect(DB) as c:
        c.execute("""create table if not exists oem_identity(
          oem text primary key, manufacturer text, item_type text,
          source text not null, confirmed_at text not null default current_timestamp)""")
def remember(oem,manufacturer,item_type=None,source="confirmed"):
    init(); oem=finder.normalize_oem(oem); manufacturer=finder.manufacturer_alias(manufacturer)
    if not oem or not manufacturer: return False
    with sqlite3.connect(DB) as c:
        old=c.execute("select manufacturer,item_type from oem_identity where oem=?",(oem,)).fetchone()
        if old and old[0] != manufacturer: raise ValueError("manufacturer conflict for "+oem)
        typ=item_type or (old[1] if old else None)
        c.execute("""insert into oem_identity(oem,manufacturer,item_type,source) values(?,?,?,?)
          on conflict(oem) do update set manufacturer=excluded.manufacturer,
          item_type=coalesce(excluded.item_type,oem_identity.item_type),
          source=excluded.source,confirmed_at=current_timestamp""",(oem,manufacturer,typ,source))
    return True
def lookup(oem):
    init(); oem=finder.normalize_oem(oem)
    with sqlite3.connect(DB) as c:
        r=c.execute("select oem,manufacturer,item_type,source,confirmed_at from oem_identity where oem=?",(oem,)).fetchone()
    return dict(zip(("oem","manufacturer","item_type","source","confirmed_at"),r)) if r else None
