"""Cross-process exclusive Windows-compatible file lock and durable recovery journal."""
import json,os,time,uuid
from datetime import datetime,timezone
from pathlib import Path

class ProbeBusy(Exception): pass

def _pid_alive(pid):
    if not isinstance(pid,int) or pid<=0:return None
    if os.name=='nt':
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION=0x1000
        h=ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION,False,pid)
        if h:
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        err=ctypes.windll.kernel32.GetLastError()
        return False if err==87 else None
    try:os.kill(pid,0);return True
    except ProcessLookupError:return False
    except PermissionError:return True
    except OSError:return None


class ProbeLock:
    def __init__(self,path,probe_id=None,owner_instance_id=None,journal_path=None):
        self.path=Path(path);self.fd=None
        self.journal_path=Path(journal_path) if journal_path is not None else None
        self.probe_id=probe_id or uuid.uuid4().hex
        self.owner_instance_id=owner_instance_id or uuid.uuid4().hex
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try:self.fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:raise ProbeBusy("PROBE_BUSY") from None
        owner={"probe_id":self.probe_id,"pid":os.getpid(),"created_at":datetime.now(timezone.utc).isoformat(),"owner_instance_id":self.owner_instance_id}
        os.write(self.fd,json.dumps(owner,sort_keys=True).encode());os.fsync(self.fd)
        return self
    def __exit__(self,*args):
        if self.fd is not None:
            os.close(self.fd);self.fd=None
            if self.journal_path is None or not self.journal_path.exists():
                self.path.unlink()

class Journal:
    def __init__(self,path): self.path=Path(path)
    def read(self):
        try: return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:return None
        except (ValueError,UnicodeError):return {"state":"RECOVERY_REQUIRED"}
    def save(self,data):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        tmp=self.path.with_name(self.path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            with open(tmp,"w",encoding="utf-8") as f:
                json.dump(data,f,ensure_ascii=False,sort_keys=True);f.flush();os.fsync(f.fileno())
            os.replace(tmp,self.path)
        finally:
            if tmp.exists():tmp.unlink()
    def clear(self):
        self.path.unlink(missing_ok=True)

class RecoveryOwnership:
    """Take over only a demonstrably dead same-machine owner with matching journal."""
    def __init__(self,lock_path,journal_path):
        self.lock_path=Path(lock_path);self.journal=Journal(journal_path);self.guard=None;self.owner=None
    def __enter__(self):
        guard=self.lock_path.with_name(self.lock_path.name+'.recovery')
        try:self.guard=os.open(guard,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:raise ProbeBusy('RECOVERY_REQUIRED') from None
        try:
            data=json.loads(self.lock_path.read_text(encoding='utf-8'))
            j=self.journal.read()
            if (not isinstance(j,dict) or not isinstance(data,dict) or
                not all(data.get(k) for k in ('probe_id','pid','created_at','owner_instance_id')) or
                j.get('probe_id')!=data['probe_id'] or
                j.get('owner_instance_id')!=data['owner_instance_id'] or
                j.get('mutation_state') in (None,'RESTORED') or
                _pid_alive(data['pid']) is not False):
                raise ProbeBusy('RECOVERY_REQUIRED')
            self.owner=data
            return self
        except (OSError,ValueError,TypeError,KeyError):
            raise ProbeBusy('RECOVERY_REQUIRED') from None
        finally:
            if self.owner is None:self._cleanup()
    def _cleanup(self):
        if self.guard is not None:
            os.close(self.guard);self.guard=None
            self.lock_path.with_name(self.lock_path.name+'.recovery').unlink(missing_ok=True)
    def __exit__(self,*exc):
        self._cleanup()
