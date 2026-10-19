"""Synthetic RBAC, XSS and pagination coverage for opt-in WEB ADMIN UI."""
import html
from unittest.mock import patch
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
from unified_analytics_admin_page import build_router

def build():
    app=FastAPI()
    def auth():
        return "admin"
    app.include_router(build_router(canonical_db="synthetic-not-production.db",require_admin=auth,audit=lambda **kw:None))
    return TestClient(app)

def test_filters_escape_and_paging():
    client=build()
    payload={"total":51,"items":[{"created_at":"2026-10-19","source_bot":"probnik",
             "oem":"<script>alert(1)</script>","manufacturer":"Arctic Cat",
             "result_status":"OK","display_price_amount":120,
             "display_price_currency":"USD","cart_added":1,"order_id":"synthetic"}]}
    with patch("unified_analytics_admin_page.query",return_value=payload) as query_mock:
        response=client.get("/admin/analytics?source_bot=probnik&page=1")
        assert response.status_code==200
        assert "&lt;script&gt;" in response.text
        assert "<script>" not in response.text
        assert "Страница 1 / 2" in response.text
        assert "Далее" in response.text
        assert query_mock.call_args.kwargs["limit"]==50

def test_empty_selection_errors_and_validation():
    client=build()
    with patch("unified_analytics_admin_page.query",return_value={"total":0,"items":[]}):
        assert "Нет заявок" in client.get("/admin/analytics").text
    assert client.get("/admin/analytics?source_bot=bad").status_code==400
    assert client.get("/admin/analytics?page=0").status_code==422
    with patch("unified_analytics_admin_page.query",side_effect=OSError("secret path")):
        response=client.get("/admin/analytics")
        assert response.status_code==503
        assert "secret path" not in response.text

def test_unauthorized_session():
    app=FastAPI()
    def denied():
        raise HTTPException(403,"admin_forbidden")
    app.include_router(build_router(canonical_db="synthetic",require_admin=denied,audit=lambda **kw:None))
    assert TestClient(app).get("/admin/analytics").status_code==403
