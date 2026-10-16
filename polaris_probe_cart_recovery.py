"""Cross-process exclusive Windows-compatible file lock and durable recovery journal."""
import json,os,time,uuid
from pathlib import Path

class ProbeBusy(Exception): pass
class ProbeLock:
    def __init__(self,path):
        self.path=Path(path);self.fd=None
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        try:self.fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        except FileExistsError:raise ProbeBusy("PROBE_BUSY") from None
        os.write(self.fd,str(os.getpid()).encode());os.fsync(self.fd)
        return self
    def __exit__(self,*args):
        if self.fd is not None:
            os.close(self.fd);self.fd=None
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
