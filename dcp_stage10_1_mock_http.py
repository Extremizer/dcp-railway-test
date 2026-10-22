"""Isolated mock-only HTTP control surface. NOT imported by production web_app.

TestClient/loopback fixture use only. Operator and producer credentials are
provided by isolated test harness; never sourced from real application secrets.
"""
from fastapi import FastAPI, HTTPException, Request
from dcp_stage10_1_mock_ownership_api import dispatch
from dcp_stage10_1_secure_control import control, Rejected

def create_mock_app(db_file, *, producer_key: bytes, operator_key: bytes):
    if not isinstance(producer_key,bytes) or len(producer_key)<24 or not isinstance(operator_key,bytes) or len(operator_key)<24 or producer_key==operator_key:
        raise ValueError("separate_mock_keys_required")
    app=FastAPI()
    async def execute(request, mode):
        body=await request.body()
        try:
            ts=int(request.headers.get("x-owner-ts",""))
            sig=request.headers.get("x-owner-sig","")
            if mode=="control":
                return control(db_file,path=request.url.path,body=body,ts=ts,sig=sig,
                               operator_key=operator_key)
            return dispatch(db_file,method=request.method,path=request.url.path,body=body,
                            timestamp=ts,signature=sig,producer_key=producer_key,
                            operator_key=operator_key)
        except (ValueError,Rejected):
            raise HTTPException(status_code=403, detail={"code":"mock_ownership_denied"})
    @app.post("/mock/ownership/acquire")
    async def acquire(request: Request):
        return await execute(request,"acquire")
    @app.get("/mock/ownership/status")
    async def status(request: Request):
        return await execute(request,"status")
    @app.post("/mock/ownership/revoke")
    async def revoke(request: Request):
        return await execute(request,"control")
    return app
