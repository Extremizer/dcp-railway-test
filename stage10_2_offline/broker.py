"""Synthetic broker: never mounts production routes, credentials, or suppliers."""
import os
import sqlite3
from fastapi import FastAPI, HTTPException, Request
from dcp_stage10_1_secure_control import signature, verify
from dcp_stage10_ownership_epoch import _init
DB=os.environ.get("STAGE10_2_DB","/state/mock.sqlite")
if os.environ.get("STAGE10_2_MOCK_ONLY")!="1":
    raise RuntimeError("mock_only_required")
app=FastAPI()
with sqlite3.connect(DB) as conn:
    _init(conn)
    conn.execute("CREATE TABLE IF NOT EXISTS mock_messages (host TEXT, event TEXT)")
@app.get("/health")
def health():
    return {"mock":True}
@app.post("/signed")
async def signed(request:Request):
    body=await request.body()
    ts=request.headers.get("x-time")
    sig=request.headers.get("x-signature")
    host=request.headers.get("x-host")
    if host not in {"old-host","new-host"}:raise HTTPException(403)
    # Synthetic key only. Different host keys; no real broker signing keys.
    key=("TEST-ONLY-OFFLINE-"+host).encode()
    try:verify(key,"POST","/signed",int(ts),body,sig)
    except Exception:raise HTTPException(403)
    with sqlite3.connect(DB) as conn:
        conn.execute("INSERT INTO mock_messages VALUES (?,?)",(host,"signed_ping"))
    return {"accepted":True,"host":host}
