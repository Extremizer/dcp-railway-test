# -*- coding: utf-8 -*-
"""Fail-closed RF stock on U.S. catalogue cache miss."""
from unittest.mock import patch
import extremizer_bot as app

BASE={"status":"CACHE_MISS","manufacturer":"Ski-Doo","query_oem":"417300574"}

def test_cache_miss_shows_independent_fresh_stock():
    with patch.object(app,"_format_client_stock_message",return_value="🏬 Наличие на складах\nСклад 1 — 34 700 ₽ • 26 шт.\nСклад 3 — 38 700 ₽ • 6 шт.") as stock:
        card=app.format_client_offer_card(dict(BASE))
    stock.assert_called_once_with("417300574")
    assert "Каталог США:" in card and "не подтверждены" in card
    assert "Склад 1" in card and "Склад 3" in card
    assert "Найдено" not in card and "Ваша цена" not in card

def test_cache_miss_unknown_stock_fails_closed():
    with patch.object(app,"_format_client_stock_message",return_value=None):
        card=app.format_client_offer_card(dict(BASE))
    assert "не подтверждены" in card
    assert "Наличие на складах" not in card
    assert "Ваша цена" not in card

def test_human_review_and_found_paths_unchanged():
    with patch.object(app,"format_result",return_value="REVIEW") as render:
        assert app.format_client_offer_card({"status":"HUMAN_REVIEW"})=="REVIEW"
    render.assert_called_once()
