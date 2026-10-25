"""EXTREMIZER POWERSPORTS isolated WEB-only staging.

Only synthetic fixtures from existing WEB2 test_browser contract are served.
Never import production WEB1, live finance, backup, or Telegram components.
Handoff and unknown OEM fail closed, prices are NOT live quotes.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException, Response
from web2_ui_routes import attach_web2_ui

app = FastAPI(title="EXTREMIZER WEB2 isolated MOCK staging", docs_url=None, redoc_url=None, openapi_url=None)
attach_web2_ui(app)
from web2_stage04_gate import attach_stage04
attach_stage04(app)
from web2_stage04_mobile_qa import attach_mobile_qa
attach_mobile_qa(app)

TEST_OEM = "417300574"


@app.middleware("http")
async def test_data_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Extremizer-Environment"] = "ISOLATED_TEST_FIXTURE"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "environment": "isolated-staging", "api": "synthetic-fixture-only", "production_data": False}


def fixture():
    # Values copied from WEB2 browser test mocks, not asserted to reflect real inventory.
    return {
        "manufacturer": "Ski-Doo",
        "oem": TEST_OEM,
        "requested_oem": TEST_OEM,
        "name": "ТЕСТОВАЯ ПОЗИЦИЯ — НЕ РЕАЛЬНАЯ ЦЕНА",
        "catalog": "MOCK / ISOLATED",
        "price": {"customer_rub": 29200, "msrp_rub": 35000, "benefit_pct": 16.6},
        "offers": [
            {"key": "usa", "source": "usa", "warehouse_id": None,
             "label": "США — ТЕСТ", "price_rub": 29200,
             "available_quantity": None, "can_add": True},
            {"key": "warehouse:1", "source": "warehouse", "warehouse_id": 1,
             "label": "Склад 1 — ТЕСТ", "price_rub": 34700,
             "available_quantity": 26, "can_add": True},
        ],
        "stock": {"known": True, "refreshing": False, "warehouses": [
            {"warehouse_id": 1, "warehouse": "Склад 1 — ТЕСТ",
             "quantity": 26, "price_rub": 34700}]},
        "weight": None,
        "delivery_notice": "* - в цену не входит стоимость доставки из штатов 🚚",
    }


@app.get("/api/v1/oem/{oem}")
def search_oem(oem: str, response: Response):
    if oem != TEST_OEM:
        raise HTTPException(status_code=404, detail="MOCK_FIXTURE_NOT_FOUND")
    response.headers["X-Extremizer-Price-Provenance"] = "SYNTHETIC_NOT_LIVE"
    return fixture()


@app.get("/api/v1/oem/{oem}/stock")
def read_stock(oem: str, response: Response):
    if oem != TEST_OEM:
        raise HTTPException(status_code=404, detail="MOCK_FIXTURE_NOT_FOUND")
    response.headers["X-Extremizer-Price-Provenance"] = "SYNTHETIC_NOT_LIVE"
    row = fixture()
    return {**row["stock"], "offers": row["offers"]}


@app.post("/api/v1/handoff")
def block_handoff():
    raise HTTPException(status_code=403, detail="ISOLATED_STAGING_ORDERS_DISABLED")
