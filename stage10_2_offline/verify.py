"""CI-only, strict Docker Compose topology and runtime evidence."""
import json,os,subprocess,sys
from pathlib import Path
def cmd(*args):
    return subprocess.check_output(args,text=True).strip()
def guard(ok,label):
    if not ok:raise RuntimeError("ISOLATION_FAIL:"+label)
base=Path(__file__).parent
config=json.loads(cmd("docker","compose","-f",str(base/"compose.yaml"),"config","--format","json"))
guard(set(config["services"])=={"broker","producer_old","producer_new"},"services")
guard(len(config["networks"])==1,"network_count")
network=next(iter(config["networks"].values()))
guard(network.get("internal") is True,"network_not_internal")
guard(network.get("enable_ipv6") is False,"ipv6_enabled")
guard(not network.get("external"),"external_network")
for name,svc in config["services"].items():
    guard(len(svc.get("networks",{}))==1,"secondary_network_"+name)
    guard(not any(k in svc for k in ("ports","network_mode","extra_hosts","privileged","devices")),"unsafe_configuration_"+name)
    guard(svc.get("read_only") is True,"writable_root_"+name)
    guard(set(svc.get("cap_drop",[]))=={"ALL"},"caps_"+name)
    for mount in svc.get("volumes",[]):
        guard(mount.get("type")=="volume","nonvolume_mount_"+name)
for service in config["services"]:
    cid=cmd("docker","compose","-f",str(base/"compose.yaml"),"ps","-q",service)
    guard(bool(cid),"missing_container_"+service)
    state=json.loads(cmd("docker","inspect",cid))[0]
    guard(not state["NetworkSettings"]["Ports"] or all(v is None for v in state["NetworkSettings"]["Ports"].values()),"published_ports_"+service)
    nets=state["NetworkSettings"]["Networks"]
    guard(len(nets)==1,"runtime_network_count_"+service)
    name,network_state=next(iter(nets.items()))
    inspect=json.loads(cmd("docker","network","inspect",network_state["NetworkID"]))[0]
    guard(inspect["Internal"] is True,"runtime_not_internal_"+service)
    guard(inspect["EnableIPv6"] is False,"runtime_ipv6_"+service)
    print("NETWORK_MEMBERSHIP_OK",service,name)
# Use reserved TEST-NET IPv4 and documentation IPv6. Never target production or DCP.
probe="""import socket
for af,target in ((socket.AF_INET,('192.0.2.10',443)),(socket.AF_INET,('198.51.100.10',443)),(socket.AF_INET6,('2001:db8::10',443,0,0))):
 s=socket.socket(af,socket.SOCK_STREAM);s.settimeout(1.5)
 try:
  s.connect(target);raise AssertionError('EGRESS_UNEXPECTEDLY_SUCCEEDED')
 except (OSError,TimeoutError):
  print('EGRESS_DENIED',af,target[0])
 finally:s.close()
"""
for service in config["services"]:
    out=cmd("docker","compose","-f",str(base/"compose.yaml"),"exec","-T",service,"python","-c",probe)
    guard(out.count("EGRESS_DENIED")==3,"egress_"+service)
    print(service,out.replace("\n","; "))
for service in ("producer_old","producer_new"):
    out=cmd("docker","compose","-f",str(base/"compose.yaml"),"logs","--no-color",service)
    guard("SIGNED_BROKER_OK" in out,"signed_connection_"+service)
print("OFFLINE_NETWORK_GATE_PASS")
