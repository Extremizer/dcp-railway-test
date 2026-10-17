#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse,json,os,sys
from pathlib import Path
import railway_snapshot_launcher as launcher
import backup_auto_run,restore_test_engine,business_files_fetcher

def atomic_json(path:Path,obj:dict):
    path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(".tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");os.replace(tmp,path)

def acquire_lock(path:Path):
    path.parent.mkdir(parents=True,exist_ok=True)
    try:fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:return None
    os.write(fd,f"pid={os.getpid()}\n".encode());os.close(fd);return path

def run_locked(a):
    old_fetch=sys.argv[:]
    try:
        sys.argv=[old_fetch[0],"--railway",a.railway,"--dest",str(a.business_source)]
        rc=business_files_fetcher.main()
        if rc!=0:return rc
    finally:sys.argv=old_fetch
    old=sys.argv[:]
    try:
        sys.argv=[old[0],"--railway",a.railway,"--remote-root",a.remote_root,
                  "--local-root",str(a.inbox),"--max-age-minutes",str(a.max_age_minutes)]
        rc=launcher.main()
        if rc!=0:return rc
    finally:sys.argv=old
    accepted_root=a.inbox/"accepted";candidates=sorted([p for p in accepted_root.iterdir() if p.is_dir()]) if accepted_root.exists() else []
    if not candidates:raise RuntimeError("launcher returned success but no accepted snapshot exists")
    accepted=candidates[-1];receipts=a.daily_root/".processed_snapshots";receipt=receipts/(accepted.name+".json")
    if receipt.exists():
        data=json.loads(receipt.read_text(encoding="utf-8"));cp=Path(data["daily_checkpoint"])
        if not cp.is_dir():raise RuntimeError("processed receipt exists but DAILY checkpoint is missing")
        if restore_test_engine.finalize_verification(cp).get("status")!="VERIFIED":raise RuntimeError("processed receipt DAILY is no longer VERIFIED")
        print(json.dumps({"status":"ALREADY_PROCESSED","snapshot":accepted.name,"daily_checkpoint":str(cp)},ensure_ascii=False));return 0
    row=backup_auto_run.run(accepted,a.business_source,a.daily_root,a.deployment_id,a.log_dir)
    if row.get("final_status")!="SUCCESS":raise RuntimeError("backup run did not finish SUCCESS")
    atomic_json(receipt,{"snapshot":accepted.name,"daily_checkpoint":row["daily_checkpoint"],"run_id":row["run_id"],"databases":row["databases"]})
    print(json.dumps(row,ensure_ascii=False));return 0

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--railway",required=True);ap.add_argument("--remote-root",default="/tmp/extremizer-backup-auto")
    ap.add_argument("--inbox",type=Path,required=True);ap.add_argument("--business-source",type=Path,required=True)
    ap.add_argument("--daily-root",type=Path,required=True);ap.add_argument("--log-dir",type=Path,required=True)
    ap.add_argument("--deployment-id",default="AUTO");ap.add_argument("--max-age-minutes",type=int,default=90);a=ap.parse_args()
    lock=a.daily_root/".backup_auto.lock";held=acquire_lock(lock)
    if held is None:
        print(json.dumps({"status":"ALREADY_RUNNING","lock":str(lock)},ensure_ascii=False));return 0
    try:return run_locked(a)
    except Exception as exc:
        print(json.dumps({"status":"FAILED","error":f"{type(exc).__name__}: {exc}"},ensure_ascii=False),file=sys.stderr);return 2
    finally:
        try:held.unlink()
        except FileNotFoundError:pass
if __name__=="__main__":raise SystemExit(main())
