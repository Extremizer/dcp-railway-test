import argparse, json, re, sqlite3, os
from pathlib import Path
from datetime import datetime, timezone

FORM_RE=re.compile(r'<form[^>]+action="/cart/addoempart"[^>]*>',re.I)
ATTR_RE=re.compile(r'data-([\w-]+)="([^"]*)"')
URL_RE=re.compile(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']',re.I)

def parse_oem_html(path):
    text=Path(path).read_text(encoding="utf-8",errors="replace")
    m=URL_RE.search(text); url=m.group(1) if m else None
    rows=[]
    for fm in FORM_RE.finditer(text):
        a=dict(ATTR_RE.findall(fm.group(0)))
        if not a.get("sku"): continue
        rows.append(dict(oem=a["sku"].strip(),name=a.get("name"),brand=a.get("brand"),catalog_type="part",
            msrp_usd=float(a["retail"]) if a.get("retail") else None,
            qoh=int(a["qoh"]) if a.get("qoh","").lstrip("-").isdigit() else None,
            source="dealercostparts_saved_html",source_url=url,source_file=Path(path).name,
            captured_at=datetime.fromtimestamp(Path(path).stat().st_mtime,timezone.utc).isoformat(),
            qoh_is_current=False))
    return rows

def ensure(con):
    con.execute("""CREATE TABLE IF NOT EXISTS dcp_catalog_snapshot(
      id INTEGER PRIMARY KEY, oem TEXT NOT NULL, name TEXT, brand TEXT, catalog_type TEXT NOT NULL,
      msrp_usd REAL, qoh INTEGER, qoh_is_current INTEGER NOT NULL DEFAULT 0,
      source TEXT NOT NULL, source_url TEXT, source_file TEXT, captured_at TEXT NOT NULL,
      imported_at TEXT NOT NULL, UNIQUE(oem,catalog_type,source_file,captured_at))""")
    con.execute("CREATE INDEX IF NOT EXISTS idx_dcp_snapshot_oem ON dcp_catalog_snapshot(oem)")

def main():
    ap=argparse.ArgumentParser(); ap.add_argument("--html",required=True); ap.add_argument("--db"); ap.add_argument("--json")
    a=ap.parse_args(); rows=parse_oem_html(a.html)
    if a.json: Path(a.json).write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    if a.db:
        con=sqlite3.connect(a.db); ensure(con); now=datetime.now(timezone.utc).isoformat()
        for r in rows:
            con.execute("""INSERT OR IGNORE INTO dcp_catalog_snapshot
            (oem,name,brand,catalog_type,msrp_usd,qoh,qoh_is_current,source,source_url,source_file,captured_at,imported_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",(r["oem"],r["name"],r["brand"],r["catalog_type"],r["msrp_usd"],r["qoh"],int(r["qoh_is_current"]),r["source"],r["source_url"],r["source_file"],r["captured_at"],now))
        con.commit(); print("DB_ROWS",con.execute("select count(*) from dcp_catalog_snapshot").fetchone()[0]); con.close()
    print("PARSED",len(rows),"UNIQUE_OEM",len({r["oem"] for r in rows}))
if __name__=="__main__": main()
