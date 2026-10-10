"""Explicit, read-only WEB2 staging app.

Start only in an isolated environment:
    uvicorn web2_staging_app:app --host 127.0.0.1 --port 8082

No production imports, database connections, or Telegram integrations.
The staging app serves the WEB2 frontend and a deterministic mock API.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from web2_ui_routes import attach_web2_ui

app = FastAPI(title="EXTREMIZER isolated staging", docs_url=None, redoc_url=None)
attach_web2_ui(app)


def _card(oem: str) -> dict:
    if oem not in {"417300574", "NO-MSRP", "WAREHOUSE-ONLY"}:
        raise HTTPException(status_code=404, detail="OEM not in staging fixtures")
    warehouse_only = oem == "WAREHOUSE-ONLY"
    customer = None if warehouse_only else 29200
    msrp = 25000 if oem == "NO-MSRP" else 35000
    show_msrp = customer is not None and msrp > customer
    offers = []
    if customer is not None:
        offers.append({
            "key": "usa", "source": "usa", "warehouse_id": None,
            "label": "США", "price_rub": customer,
            "available_quantity": None, "can_add": True,
        })
    offers.append({
        "key": "warehouse:1", "source": "warehouse", "warehouse_id": 1,
        "label": "Склад 1", "price_rub": 34700,
        "available_quantity": 26, "can_add": True,
    })
    return {
        "manufacturer": "Ski-Doo", "oem": oem,
        "requested_oem": oem, "name": "Тестовая OEM позиция",
        "catalog": "Staging fixture",
        "warehouse_only": warehouse_only,
        "price": {
            "available": customer is not None,
            "customer_rub": customer,
            "msrp_rub": msrp if show_msrp else None,
            "benefit_pct": round((msrp - customer) / msrp * 100, 1)
            if show_msrp else None,
        },
        "offers": offers,
        "stock": {
            "known": True, "refreshing": False,
            "warehouses": [{
                "warehouse_id": 1, "warehouse": "Склад 1",
                "quantity": 26, "price_rub": 34700,
            }],
        },
        "weight": None,
        "delivery_notice": "* - в цену не входит стоимость доставки из штатов 🚚",
    }


@app.get("/api/v1/oem/{oem}")
def staging_oem(oem: str) -> dict:
    return _card(oem)


@app.get("/api/v1/oem/{oem}/stock")
def staging_stock(oem: str) -> dict:
    card = _card(oem)
    return {**card["stock"], "offers": card["offers"]}


class HandoffItem(BaseModel):
    manufacturer: str
    oem: str
    requested_oem: str | None = None
    offer_source: str
    warehouse_id: int | None = None
    qty: int = Field(ge=1, le=99)


class HandoffRequest(BaseModel):
    items: list[HandoffItem] = Field(min_length=1)


@app.post("/api/v1/handoff")
def staging_handoff(payload: HandoffRequest) -> dict:
    """Validate a mock handoff but never create an order or Telegram token."""
    for item in payload.items:
        card = _card(item.oem)
        if not any(
            offer["source"] == item.offer_source
            and offer["warehouse_id"] == item.warehouse_id
            and offer["can_add"]
            and (offer["available_quantity"] is None
                 or item.qty <= offer["available_quantity"])
            for offer in card["offers"]
        ):
            raise HTTPException(status_code=400, detail="Invalid staging offer")
    return {"telegram_url": "/web2?staging_handoff=mocked"}


@app.get("/staging-health")
def staging_health() -> dict:
    return {"status": "ok", "mode": "mock", "orders_created": False}
