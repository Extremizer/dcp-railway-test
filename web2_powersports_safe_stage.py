"""EXTREMIZER POWERSPORTS isolated staging: WEB-only fail-closed API.

Does not import production WEB1, bots, finance, backup, or pricing.
Real OEM E2E requires a separately authorized isolated data source.
"""
from fastapi import FastAPI, HTTPException
from web2_ui_routes import attach_web2_ui

app = FastAPI(title="EXTREMIZER WEB2 isolated staging", docs_url=None, redoc_url=None, openapi_url=None)
attach_web2_ui(app)


@app.get("/health")
def health():
    return {"status": "ok", "environment": "isolated-staging", "api": "not-configured"}


@app.get("/api/v1/oem/{oem}")
def search_oem(oem: str):
    raise HTTPException(status_code=503, detail="ISOLATED_STAGING_OEM_DATA_NOT_CONFIGURED")


@app.get("/api/v1/oem/{oem}/stock")
def read_stock(oem: str):
    raise HTTPException(status_code=503, detail="ISOLATED_STAGING_STOCK_DATA_NOT_CONFIGURED")
