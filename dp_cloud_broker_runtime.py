"""OFF by default cloud broker router mount, never a separate DB/cache.

Review-only integration: production entrypoints remain unchanged.
"""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI, HTTPException
from dp_cloud_owner_http import build_cloud_router, signing_key

def build_app(*, db_file=None, signing_token=None):
    if os.getenv("DCP_CLOUD_BROKER_RUNTIME_ENABLED") != "1":
        raise RuntimeError("cloud_broker_runtime_disabled")
    if os.getenv("EXTREMIZER_DP_CLOUD_OWNERSHIP_ENABLED") != "1":
        raise RuntimeError("owner_fencing_required")
    db_file = db_file or os.getenv("EXTREMIZER_ORDERS_DB_FILE")
    token = signing_token or os.getenv("DP_SYNC_TOKEN")
    if not db_file or not Path(db_file).is_absolute():
        raise RuntimeError("explicit_shared_broker_db_required")
    app=FastAPI(title="DCP Cloud Broker Isolated",docs_url=None,redoc_url=None,openapi_url=None)
    app.include_router(build_cloud_router(db_file,key=signing_key(token)))
    return app
