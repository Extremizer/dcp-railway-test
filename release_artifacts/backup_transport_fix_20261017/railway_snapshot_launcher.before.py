#!/usr/bin/env python3
# -*- coding: utf-8 -*-
from __future__ import annotations
import argparse, datetime as dt, hashlib, json, os, re, shlex, shutil, sqlite3, subprocess, sys, tempfile, time
from pathlib import Path

EXPECTED=("extremizer_orders.db","oem_reference.db")
STAMP_RE=re.compile(r"^\d{8}T\d{6}Z$")
MAX_AGE_MINUTES=90

def sha256(p:Path):
    h=hashlib.sha256()
    with p.open("rb") as f:
        for b in iter(lambda:f.read(1024*1024),b""): h.update(b)
    return h.hexdigest()

def run_cli(args:list[str]):
    cp=subprocess.run(args,capture_output=True,text=True,stdin=subprocess.DEVNULL)
    if cp.returncode!=0:
        raise RuntimeError("Railway CLI failed: "+(cp.stderr.strip() or cp.stdout.strip()))
    return cp.stdout

def parse_json(text:str):
    i=text.find("["); j=text.find("{")
    starts=[x for x in (i,j) if x>=0]
    if not starts: raise RuntimeError("Railway CLI returned no JSON")
    return json.loads(text[min(starts):])

def names_from_listing(obj):
    rows=obj if isinstance(obj,list) else obj.get("entries",obj.get("files",[]))
    out=[]
    for x in rows:
        if isinstance(x,str): out.append(Path(x.rstrip("/")).name)
        elif isinstance(x,dict):
            v=x.get("name") or x.get("path") or x.get("filename")
            if v: out.append(Path(str(v).rstrip("/")).name)
    return out

def verify_snapshot(folder:Path):
    mp=folder/"manifest.json"
    if not mp.is_file(): raise RuntimeError("manifest.json missing")
    m=json.loads(mp.read_text(encoding="utf-8"))
    if m.get("status")!="READY": raise RuntimeError("manifest status is not READY")
    declared=m.get("files")
    if not isinstance(declared,list): raise RuntimeError("manifest files invalid")
    by={x.get("name"):x for x in declared if isinstance(x,dict)}
    if set(by)!=set(EXPECTED): raise RuntimeError("manifest must declare exactly 2 expected DB files")
    result=[]
    for name in EXPECTED:
        p=folder/name; x=by[name]
        if not p.is_file(): raise RuntimeError(name+" missing")
        size=p.stat().st_size
        if size!=int(x["size"]): raise RuntimeError(name+" size mismatch")
        digest=sha256(p)
        if digest.lower()!=str(x["sha256"]).lower(): raise RuntimeError(name+" sha256 mismatch")
        c=sqlite3.connect("file:"+p.as_posix()+"?mode=ro",uri=True,timeout=30)
        try: integrity=c.execute("PRAGMA integrity_check").fetchone()[0]
        finally: c.close()
        if integrity!="ok": raise RuntimeError(name+" integrity_check="+str(integrity))
        result.append({"name":name,"size":size,"sha256":digest,"integrity_check":"ok"})
    return {"status":"PASS","snapshot":folder.name,"files":result}

PY_REMOTE="/mise/installs/python/latest/bin/python"
CHUNK_SIZE=64*1024*1024
SSH_START_TIMEOUT=30
SSH_PROBE_TIMEOUT=30
SSH_CHUNK_TIMEOUT=180
SSH_CLEANUP_TIMEOUT=30
SSH_RETRIES=5
REMOTE_CHUNK_DEADMAN=165
SNAPSHOT_PROBES=12


def event(name,**fields):
    print(json.dumps({"event":name,**fields},ensure_ascii=False),flush=True)


def kill_process_tree(pid:int):
    subprocess.run(
        ["taskkill","/PID",str(pid),"/T","/F"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
    )


def run_ssh_text(railway:str,remote_cmd:str,timeout:int,retries:int=1):
    last=""
    for attempt in range(1,retries+1):
        p=subprocess.Popen(
            [railway,"ssh","--","sh","-lc",remote_cmd],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
        )
        try:
            out,err=p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            kill_process_tree(p.pid)
            try:
                out,err=p.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
                out,err=p.communicate()
            last=f"timeout after {timeout}s"
        else:
            if p.returncode==0:
                return out
            last=(err or out or f"rc={p.returncode}").strip()
        if attempt<retries:
            time.sleep(min(2**attempt,15))
    raise RuntimeError("Railway SSH text failed: "+last)


def run_ssh_to_file(
    railway:str,
    remote_cmd:str,
    dest:Path,
    timeout:int,
    expected_size:int|None=None,
    retries:int=SSH_RETRIES,
):
    last=""
    for attempt in range(1,retries+1):
        dest.parent.mkdir(parents=True,exist_ok=True)
        started=time.monotonic()

        event(
            "SSH_FILE_ATTEMPT_START",
            attempt=attempt,
            retries=retries,
            timeout=timeout,
            expected_size=expected_size,
            dest=dest.name,
        )

        with dest.open("wb") as out:
            p=subprocess.Popen(
                [railway,"ssh","--","sh","-lc",remote_cmd],
                stdout=out,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
            )
            try:
                _,err=p.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                kill_process_tree(p.pid)
                try:
                    _,err=p.communicate(timeout=10)
                except subprocess.TimeoutExpired:
                    p.kill()
                    _,err=p.communicate()

                elapsed=round(time.monotonic()-started,2)
                size=dest.stat().st_size if dest.exists() else 0
                stderr=(err or b"").decode("utf-8","replace").strip()

                event(
                    "SSH_FILE_ATTEMPT_FAIL",
                    attempt=attempt,
                    reason="timeout",
                    elapsed_seconds=elapsed,
                    bytes_received=size,
                    expected_size=expected_size,
                    stderr=stderr[-500:],
                )

                last=f"timeout after {timeout}s"
            else:
                elapsed=round(time.monotonic()-started,2)

                if p.returncode==0:
                    size=dest.stat().st_size

                    if expected_size is None or size==expected_size:
                        event(
                            "SSH_FILE_ATTEMPT_PASS",
                            attempt=attempt,
                            elapsed_seconds=elapsed,
                            bytes_received=size,
                            expected_size=expected_size,
                        )
                        return

                    event(
                        "SSH_FILE_ATTEMPT_FAIL",
                        attempt=attempt,
                        reason="size_mismatch",
                        elapsed_seconds=elapsed,
                        bytes_received=size,
                        expected_size=expected_size,
                        stderr=(err or b"").decode("utf-8","replace").strip()[-500:],
                    )

                    last=f"size mismatch expected={expected_size} actual={size}"
                else:
                    size=dest.stat().st_size if dest.exists() else 0
                    stderr=(err or b"").decode("utf-8","replace").strip()

                    event(
                        "SSH_FILE_ATTEMPT_FAIL",
                        attempt=attempt,
                        reason="returncode",
                        returncode=p.returncode,
                        elapsed_seconds=elapsed,
                        bytes_received=size,
                        expected_size=expected_size,
                        stderr=stderr[-500:],
                    )

                    last=stderr or f"rc={p.returncode}"

        dest.unlink(missing_ok=True)

        if attempt<retries:
            time.sleep(min(2**attempt,15))

    raise RuntimeError("Railway SSH file transfer failed: "+last)


def download_chunked(railway:str,remote_file:str,local_file:Path,total_size:int):
    chunk_py=(
        "import signal,sys;"
        f"signal.alarm({REMOTE_CHUNK_DEADMAN});"
        "p=sys.argv[1];o=int(sys.argv[2]);n=int(sys.argv[3]);"
        "f=open(p,'rb');f.seek(o);"
        "sys.stdout.buffer.write(f.read(n))"
    )

    local_file.parent.mkdir(parents=True,exist_ok=True)
    local_file.unlink(missing_ok=True)
    chunk_file=local_file.with_suffix(local_file.suffix+".part")

    offset=0
    with local_file.open("wb") as final:
        while offset<total_size:
            count=min(CHUNK_SIZE,total_size-offset)
            event(
                "DOWNLOAD_CHUNK_START",
                file=local_file.name,
                offset=offset,
                bytes=count,
                total=total_size,
            )

            cmd=(
                PY_REMOTE+" -c "+shlex.quote(chunk_py)+" "
                +shlex.quote(remote_file)+" "
                +str(offset)+" "
                +str(count)
            )

            run_ssh_to_file(
                railway,
                cmd,
                chunk_file,
                SSH_CHUNK_TIMEOUT,
                expected_size=count,
                retries=SSH_RETRIES,
            )

            with chunk_file.open("rb") as part:
                shutil.copyfileobj(part,final,1024*1024)

            chunk_file.unlink(missing_ok=True)
            offset+=count

            event(
                "DOWNLOAD_CHUNK_OK",
                file=local_file.name,
                downloaded=offset,
                total=total_size,
            )

    actual=local_file.stat().st_size
    if actual!=total_size:
        raise RuntimeError(
            f"{local_file.name} assembled size mismatch expected={total_size} actual={actual}"
        )


def cleanup_remote_run(railway:str,run_remote_root:str):
    rr=shlex.quote(run_remote_root)
    py=shlex.quote(PY_REMOTE)

    cleanup_cmd=(
        "rr="+rr+"; py="+py+"; "
        "for d in /proc/[0-9]*; do "
        "pid=${d#/proc/}; "
        "exe=$(readlink \"$d/exe\" 2>/dev/null || true); "
        "[ \"$exe\" = \"$py\" ] || continue; "
        "cmd=$(tr '\\000' ' ' < \"$d/cmdline\" 2>/dev/null || true); "
        "printf '%s' \"$cmd\" | grep -Fq -- \"$rr\" || continue; "
        "kill -TERM \"$pid\" 2>/dev/null || true; "
        "done; "
        "sleep 2; "
        "for d in /proc/[0-9]*; do "
        "pid=${d#/proc/}; "
        "exe=$(readlink \"$d/exe\" 2>/dev/null || true); "
        "[ \"$exe\" = \"$py\" ] || continue; "
        "cmd=$(tr '\\000' ' ' < \"$d/cmdline\" 2>/dev/null || true); "
        "printf '%s' \"$cmd\" | grep -Fq -- \"$rr\" || continue; "
        "kill -KILL \"$pid\" 2>/dev/null || true; "
        "done; "
        "rm -rf -- \"$rr\""
    )

    run_ssh_text(
        railway,
        cleanup_cmd,
        SSH_CLEANUP_TIMEOUT,
        retries=SSH_RETRIES,
    )


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--railway",default=r"C:\Railway\railway.exe")
    ap.add_argument("--remote-root",default="/data/backups/auto_transport")
    ap.add_argument("--local-root",default=str(Path(__file__).with_name("railway_snapshot_inbox")))
    ap.add_argument("--max-age-minutes",type=int,default=MAX_AGE_MINUTES)
    a=ap.parse_args()

    railway=os.path.expandvars(a.railway)
    if Path(railway).suffix.lower()!=".exe":
        raise RuntimeError("V3 requires direct railway.exe, not railway.cmd")

    root=Path(a.local_root)
    root.mkdir(parents=True,exist_ok=True)

    run_id=dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"-"+str(os.getpid())
    run_remote_root=a.remote_root.rstrip("/")+"/run-"+run_id
    temp=None
    failed=False

    try:
        producer_log=run_remote_root+"/producer.log"
        start_cmd=(
            "mkdir -p -- "+shlex.quote(run_remote_root)+"; "
            "nohup "+PY_REMOTE+" -u backup_snapshot_helper.py --out-root "
            +shlex.quote(run_remote_root)+" > "
            +shlex.quote(producer_log)+" 2>&1 < /dev/null & "
            "printf BACKUP_PRODUCER_STARTED"
        )

        event("SNAPSHOT_PRODUCER_START",run_root=run_remote_root)

        start_error=None
        try:
            started=run_ssh_text(
                railway,
                start_cmd,
                SSH_START_TIMEOUT,
                retries=2,
            )
            if "BACKUP_PRODUCER_STARTED" not in started:
                start_error="start marker missing"
        except Exception as e:
            start_error=str(e)

        probe_cmd=(
            "for d in "+shlex.quote(run_remote_root)+"/20*T*Z; do "
            "[ -f \"$d/manifest.json\" ] || continue; "
            "grep -q '\"status\"[[:space:]]*:[[:space:]]*\"READY\"' "
            "\"$d/manifest.json\" || continue; "
            "printf '%s\\n' \"$d\"; "
            "done | sort | tail -n 1"
        )

        remote=None
        probe_errors=[]
        for attempt in range(1,SNAPSHOT_PROBES+1):
            try:
                out=run_ssh_text(
                    railway,
                    probe_cmd,
                    SSH_PROBE_TIMEOUT,
                    retries=1,
                ).strip()
                if out:
                    remote=out.splitlines()[-1].strip()
                    break
            except Exception as e:
                probe_errors.append(str(e))

            event("SNAPSHOT_WAIT",attempt=attempt)
            time.sleep(3)

        if remote is None:
            log_tail=""
            try:
                log_tail=run_ssh_text(
                    railway,
                    "tail -n 30 "+shlex.quote(producer_log)+" 2>/dev/null || true",
                    SSH_PROBE_TIMEOUT,
                    retries=1,
                ).strip()
            except Exception:
                pass

            raise RuntimeError(
                "SNAPSHOT_NOT_READY"
                +("; start_error="+start_error if start_error else "")
                +("; probe_error="+probe_errors[-1] if probe_errors else "")
                +("; producer_log="+log_tail if log_tail else "")
            )

        prefix=run_remote_root.rstrip("/")+"/"
        if not remote.startswith(prefix):
            raise RuntimeError("snapshot path outside own run root")

        stamp=Path(remote).name
        if not STAMP_RE.fullmatch(stamp):
            raise RuntimeError("invalid snapshot stamp")

        ts=dt.datetime.strptime(stamp,"%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)
        age=(dt.datetime.now(dt.timezone.utc)-ts).total_seconds()/60
        if age<0 or age>a.max_age_minutes:
            raise RuntimeError(f"STALE_SNAPSHOT age_minutes={age:.1f}")

        event("SNAPSHOT_READY",snapshot=stamp)

        accepted=root/"accepted"/stamp
        if accepted.exists():
            report=verify_snapshot(accepted)
            event("ACCEPTED_ALREADY_VERIFIED",snapshot=stamp)
            print(json.dumps(report,ensure_ascii=False),flush=True)
            return 0

        temp=Path(tempfile.mkdtemp(prefix=stamp+"-",dir=str(root)))
        folder=temp/"snapshot"
        folder.mkdir()

        manifest_file=folder/"manifest.json"
        run_ssh_to_file(
            railway,
            "cat -- "+shlex.quote(remote+"/manifest.json"),
            manifest_file,
            SSH_PROBE_TIMEOUT,
            expected_size=None,
            retries=SSH_RETRIES,
        )

        manifest=json.loads(manifest_file.read_text(encoding="utf-8"))
        if manifest.get("status")!="READY":
            raise RuntimeError("manifest status is not READY")

        declared=manifest.get("files")
        if not isinstance(declared,list):
            raise RuntimeError("manifest files invalid")

        by={x.get("name"):x for x in declared if isinstance(x,dict)}
        if set(by)!=set(EXPECTED):
            raise RuntimeError("manifest must declare exactly 2 expected DB files")

        for name in EXPECTED:
            expected_size=int(by[name]["size"])
            download_chunked(
                railway,
                remote+"/"+name,
                folder/name,
                expected_size,
            )

        event("LOCAL_VERIFY_START",snapshot=stamp)
        report=verify_snapshot(folder)
        event("LOCAL_VERIFY_PASS",snapshot=stamp)

        accepted.parent.mkdir(parents=True,exist_ok=True)
        shutil.move(str(folder),str(accepted))

        (accepted/"launcher_verification.json").write_text(
            json.dumps(report,ensure_ascii=False,indent=2)+"\n",
            encoding="utf-8",
        )

        print(json.dumps(report,ensure_ascii=False),flush=True)
        return 0

    except Exception:
        failed=True
        raise

    finally:
        if temp is not None and Path(temp).exists():
            shutil.rmtree(temp,ignore_errors=True)

        try:
            cleanup_remote_run(
                railway,
                run_remote_root,
            )
            event("REMOTE_CLEANUP_PASS",run_root=run_remote_root)
        except Exception as e:
            if failed:
                print(
                    json.dumps(
                        {
                            "warning":"REMOTE_RUN_CLEANUP_FAILED",
                            "run_root":run_remote_root,
                            "error":str(e),
                        },
                        ensure_ascii=False,
                    ),
                    file=sys.stderr,
                    flush=True,
                )
            else:
                raise RuntimeError("REMOTE_RUN_CLEANUP_FAILED: "+str(e))


if __name__=="__main__":
    try: raise SystemExit(main())
    except Exception as e:
        print(json.dumps({"status":"FAIL","error":f"{type(e).__name__}: {e}"},ensure_ascii=False),file=sys.stderr)
        raise SystemExit(2)
