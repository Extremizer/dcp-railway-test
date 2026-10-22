"""Pre-run fail closed checks for Compose topology."""
import json,subprocess
from pathlib import Path
p=Path(__file__).parent/"compose.yaml"
data=json.loads(subprocess.check_output(["docker","compose","-f",str(p),"config","--format","json"],text=True))
assert set(data["services"])=={"broker","producer_old","producer_new"}
assert len(data["networks"])==1
net=next(iter(data["networks"].values()))
assert net.get("internal") is True and net.get("enable_ipv6") is False and not net.get("external")
assert len(data.get("volumes",{}))==3
for name,svc in data["services"].items():
 assert set(svc.get("networks",{}))==set(data["networks"])
 assert not any(x in svc for x in ("ports","network_mode","extra_hosts","privileged","devices"))
 assert svc.get("read_only") is True
 assert set(svc.get("cap_drop",[]))=={"ALL"}
 assert len(svc.get("volumes",[]))==1
 assert all(x.get("type")=="volume" for x in svc["volumes"])
print("STATIC_TOPOLOGY_GATE_PASS")
