"""One-shot synthetic producer. No browser, no OEM jobs, no real DCP."""
import json,os,socket,sys,time
from pathlib import Path
import httpx
from dcp_stage10_1_secure_control import signature
if os.environ.get("STAGE10_2_MOCK_ONLY")!="1":raise RuntimeError("mock_only_required")
host=sys.argv[1]
if host not in {"old-host","new-host"}:raise RuntimeError("unexpected_host")
state=Path("/state")
(state/"journal.json").write_text(json.dumps({"host":host,"status":"synthetic_only"}))
body=json.dumps({"fixture":"ping","host":host}).encode()
ts=int(time.time())
headers={"x-time":str(ts),"x-host":host,"x-signature":signature(
    ("TEST-ONLY-OFFLINE-"+host).encode(),"POST","/signed",ts,body)}
response=httpx.post("http://broker:8800/signed",content=body,headers=headers,timeout=8)
response.raise_for_status()
if response.json()!={"accepted":True,"host":host}:raise RuntimeError("invalid_broker_ack")
print("SIGNED_BROKER_OK",host,flush=True)

# Remain alive solely for CI runtime network namespace inspection.
while True:time.sleep(60)
