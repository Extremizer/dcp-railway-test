"""Dedicated opt-in evidence-only ASGI receiver. No production mounting."""
from __future__ import annotations
import os
from pathlib import Path
from fastapi import FastAPI
from oemixi_trusted_evidence_channel import (
    initialize_evidence_store, create_evidence_router, trusted_evidence_reader
)

def create_app(*, db_path=None, producer_secret=None, initialize=False):
    db = db_path or os.getenv("EXTREMIZER_EVIDENCE_RECEIVER_DB_PATH")
    secret = producer_secret or os.getenv("EXTREMIZER_EVIDENCE_PRODUCER_HMAC_SECRET")
    if not db or not secret or len(secret)<32:
        raise ValueError("dedicated_evidence_receiver_configuration_required")
    if not Path(db).is_absolute():
        raise ValueError("absolute_evidence_db_path_required")
    if initialize:
        Path(db).parent.mkdir(parents=True,exist_ok=True)
        initialize_evidence_store(db)
    app=FastAPI(title="Isolated OEMixi Evidence Receiver",docs_url=None,redoc_url=None,openapi_url=None)
    app.include_router(create_evidence_router(db=db,producer_secret=secret))
    app.state.trusted_evidence_reader=trusted_evidence_reader(db)
    @app.get("/health")
    def health():
        return {"status":"ok","service":"oemixi-evidence-receiver"}
    return app

# Explicit startup initialization occurs before serving; never in a POST handler.
def app_factory():
    return create_app(initialize=True)
