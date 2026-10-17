#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse,json,subprocess
from pathlib import Path
import restore_test_engine

def latest_jsonl(path:Path):
    if not path.is_file(): return None
    rows=[x for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    return json.loads(rows[-1]) if rows else None

def task_state(name:str):
    ps=f"$t=Get-ScheduledTask -TaskName '{name}' -ErrorAction SilentlyContinue;if(-not $t){{'NOT_REGISTERED'}}else{{$i=Get-ScheduledTaskInfo -TaskName '{name}';[pscustomobject]@{{State=$t.State.ToString();LastTaskResult=$i.LastTaskResult;LastRunTime=$i.LastRunTime.ToString('o')}}|ConvertTo-Json -Compress}}"
    cp=subprocess.run(["powershell.exe","-NoProfile","-Command",ps],capture_output=True,text=True)
    s=cp.stdout.strip()
    if s=="NOT_REGISTERED" or not s:return {"status":"NOT_REGISTERED"}
    try:
        x=json.loads(s)
        return {"status":"SUCCESS" if int(x["LastTaskResult"])==0 else "FAILED",**x}
    except Exception:return {"status":"UNKNOWN","raw":s}

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--inbox",type=Path,required=True);ap.add_argument("--daily-root",type=Path,required=True)
    ap.add_argument("--log-dir",type=Path,required=True);ap.add_argument("--task-name",default="Extremizer Backup AUTO")
    a=ap.parse_args()
    accepted=sorted([p for p in (a.inbox/"accepted").glob("*") if p.is_dir()])
    snap=accepted[-1] if accepted else None
    receipt_path=(a.daily_root/".processed_snapshots"/(snap.name+".json")) if snap else None
    receipt=json.loads(receipt_path.read_text(encoding="utf-8")) if receipt_path and receipt_path.is_file() else None
    journal=latest_jsonl(a.log_dir/"backup_auto_runs.jsonl")
    dbs=receipt.get("databases",{}) if receipt else {}
    cp=Path(receipt["daily_checkpoint"]) if receipt and receipt.get("daily_checkpoint") else None
    daily="FAILED"
    if cp and cp.is_dir():
        try: daily="VERIFIED" if restore_test_engine.finalize_verification(cp).get("status")=="VERIFIED" else "FAILED"
        except Exception: daily="FAILED"
    task=task_state(a.task_name)
    report={
      "railway_snapshot":"PASS" if snap else "FAILED",
      "snapshot":snap.name if snap else None,
      "databases":f"{sum(1 for n in ('extremizer_orders.db','oem_reference.db') if n in dbs)}/2",
      "daily":daily,
      "daily_checkpoint":str(cp) if cp else None,
      "journal":"SUCCESS" if journal and journal.get("final_status")=="SUCCESS" and receipt and journal.get("run_id")==receipt.get("run_id") else "FAILED",
      "task_scheduler":task["status"],
      "task_details":task
    }
    required=[report["railway_snapshot"]=="PASS",report["databases"]=="2/2",daily=="VERIFIED",report["journal"]=="SUCCESS",report["task_scheduler"]=="SUCCESS"]
    report["overall"]="SUCCESS" if all(required) else ("READY_EXCEPT_TASK" if all(required[:4]) and report["task_scheduler"]=="NOT_REGISTERED" else "FAILED")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    print(f"Railway snapshot: {report['railway_snapshot']} → DB {report['databases']} → DAILY {report['daily']} → journal {report['journal']} → Task Scheduler {report['task_scheduler']}")
    return 0 if report["overall"] in ("SUCCESS","READY_EXCEPT_TASK") else 2
if __name__=="__main__":raise SystemExit(main())
