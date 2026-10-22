"""Stage 10 isolated signed-v2 transport for the Stage 9 Linux MOCK runner.

Carries server-issued ephemeral ownership proof on exact existing v2 HTTP
claim/result operations. Local loopback fixture ONLY; no actual browser, DCP,
Railway, Windows agent, or new broker/cache path.
"""
import json
import time
import urllib.request
from urllib.parse import urlsplit

import dp_sync_protocol
import dcp_stage9_mock_linux_runner as stage9

def install_mock_transport(agent, config, proof):
    p=urlsplit(config["url"])
    if p.scheme!="http" or p.hostname not in {"127.0.0.1","::1","localhost"}:
        raise stage9.UnsafeConfiguration("only mock loopback target allowed")
    if not isinstance(proof,dict) or proof.get("owner")!=agent.PRODUCER_ID or type(proof.get("epoch")) is not int or not proof.get("nonce"):
        raise stage9.UnsafeConfiguration("explicit server-issued ownership proof required")
    def request(path, *, method="GET", payload=None):
        if path not in {"/internal/dp-sync/next","/internal/dp-sync/result"}:
            raise stage9.UnsafeConfiguration("mock transport endpoint not allowlisted")
        body=b"" if payload is None else json.dumps(
            {**payload,"ownership_proof":proof},
            ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
        ts=int(time.time())
        headers={
            "X-DP-Protocol":"2",
            "X-DP-Producer-ID":agent.PRODUCER_ID,
            "X-DP-Sync-Ts":str(ts),
            "X-DP-Sync-Sig":dp_sync_protocol.sign(agent._key(),method,path,ts,agent.PRODUCER_ID,body),
        }
        if method=="GET":
            headers["X-DP-Ownership-Epoch"]=str(proof["epoch"])
            headers["X-DP-Ownership-Nonce"]=proof["nonce"]
        else:
            headers["Content-Type"]="application/json"
        req=urllib.request.Request(config["url"]+path,
            data=body if payload is not None else None,headers=headers,method=method)
        with urllib.request.urlopen(req,timeout=20) as response:
            result=json.loads(response.read().decode("utf-8"))
        if not isinstance(result,dict):
            raise ValueError("invalid_mock_broker_response")
        return result
    agent._request=request

def execute(config, agent, proof, *, approved_identities, lookup=None):
    install_mock_transport(agent,config,proof)
    return stage9.execute(config,agent=agent,lookup=lookup,
                          approved_identities=approved_identities)
