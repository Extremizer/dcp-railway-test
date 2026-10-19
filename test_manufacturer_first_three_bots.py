"""Manufacturer-first UX regression, synthetic only; no Telegram, DCP or DB requests."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import manufacturer_first_ux as ux
import probnik_app as probnik
import extremizer_bot as pricing
import oemixibot_app as oemixi


class Message:
    def __init__(self, text):
        self.text=text
        self.responses=[]
    async def reply_text(self, text, **kwargs):
        self.responses.append((text,kwargs))
        return self
    async def edit_text(self,text,**kwargs):
        self.responses.append((text,kwargs))


def ctx():
    return SimpleNamespace(user_data={},args=[],application=SimpleNamespace(bot_data={},create_task=lambda coro:coro.close()))


def update(text, *, private=True):
    return SimpleNamespace(effective_message=Message(text),
       effective_chat=SimpleNamespace(type="private" if private else "group",id=123),
       effective_user=SimpleNamespace(id=101,username=None,first_name=None,last_name=None))


def test_canonical_manufacturer_buttons_equal_dcp_catalog_order():
    expected=list(dict.fromkeys(c.manufacturer for c in pricing.finder.CATALOGS))
    assert expected == list(ux.MANUFACTURERS) == pricing.MANUFACTURERS
    assert len(expected)==13
    assert [x.text for row in probnik.manufacturer_keyboard().inline_keyboard for x in row]==expected
    assert [x.text for row in oemixi._mfg_keyboard().inline_keyboard for x in row if x.callback_data.startswith("mfg:")]==expected
    assert [x.text for row in oemixi._mfg_keyboard(admin=True).inline_keyboard for x in row]==expected


def test_state_and_oem_guards():
    state={"last_oem":"old","offer:old":{},"found_items":{"old":1}}
    assert ux.choose(state,"Arctic Cat")
    assert state == {"manufacturer":"Arctic Cat"}
    assert ux.selected(state)=="Arctic Cat"
    assert ux.exact_oem("0746-965")=="0746-965"
    assert ux.exact_oem("Can-Am 709200703") is None
    assert ux.oem_batch("Ski-Doo 417300571") is None
    assert ux.oem_batch("0746-965 0746-933")==["0746-965","0746-933"]
    assert ux.choose(state,"Can-Am")
    assert "last_oem" not in state
    assert not ux.choose(state,"Unknown")
    ux.clear_search(state)
    assert not ux.selected(state)


def test_probnik_no_manufacturer_no_search():
    u=update("0746-965"); c=ctx()
    with patch.object(probnik,"_identity_and_price") as price,patch.object(probnik,"_stock_with_refresh") as stock:
        asyncio.run(probnik.oem_message(u,c))
    price.assert_not_called();stock.assert_not_called()
    assert "производителя" in u.effective_message.responses[-1][0]


def test_probnik_selected_pair_to_price_and_stock_independently():
    for manufacturer,oem in (("Arctic Cat","0746-965"),("Can-Am","709200703")):
        u=update(oem);c=ctx()
        ux.choose(c.user_data,manufacturer)
        info={"manufacturer":manufacturer,"customer_rub":None,"rrp_rub":None}
        async def stock_fun(value):
            assert value==oem
            return []
        with patch.object(probnik,"_identity_and_price",return_value=info) as price,patch.object(
            probnik,"_stock_with_refresh",side_effect=stock_fun) as stock,patch.object(
            probnik.pricing_analytics,"begin_request_from_update",return_value="synthetic"),patch.object(
            probnik.pricing_analytics,"complete_request"):
            asyncio.run(probnik.oem_message(u,c))
        price.assert_called_once_with(oem,manufacturer)
        stock.assert_called_once_with(oem)


def test_oemixi_unauthorized_or_missing_manufacturer_never_calls_gateway():
    c=ctx();u=update("0746-965")
    with patch.object(oemixi,"_dealer",return_value={"id":1}),patch.object(oemixi,"_resolve_runtime_offer") as price:
        asyncio.run(oemixi.oem_message(u,c))
    price.assert_not_called()
    assert "производителя" in u.effective_message.responses[-1][0]


def test_oemixi_dealer_selected_pairs_not_guessed():
    for manufacturer,oem in (("Arctic Cat","0746-965"),("Can-Am","709200703")):
        c=ctx();u=update(oem);ux.choose(c.user_data,manufacturer)
        with patch.object(oemixi,"_dealer",return_value={"id":1}),patch.object(
            oemixi,"_resolve_runtime_offer",new_callable=AsyncMock,return_value={
              "status":"PRICE_UNAVAILABLE","manufacturer":manufacturer,"oem":oem,"dl_usd":None,
            }) as gateway:
            asyncio.run(oemixi.oem_message(u,c))
        gateway.assert_awaited_once_with(oem,c,manufacturer=manufacturer)
        assert "недоступна" in u.effective_message.responses[-1][0]


def test_oemixi_admin_selected_pair_to_search():
    for manufacturer,oem in (("Arctic Cat","0746-965"),("Can-Am","709200703")):
        c=ctx()
        with patch.object(oemixi,"_resolve_runtime_offer",new_callable=AsyncMock,
             return_value={"status":"PRICE_UNAVAILABLE","manufacturer":manufacturer}) as gateway:
            offer=asyncio.run(oemixi._resolve_admin_oem_offer(oem,c,manufacturer=manufacturer))
        gateway.assert_awaited_once_with(oem,c,manufacturer=manufacturer)
        assert offer["manufacturer"]==manufacturer


def test_polaris_admin_cart_only():
    c=ctx();calls=[]
    def resolver(oem):
        calls.append(oem)
        return {"status":"DEALER_PRICE_UNVERIFIED","manufacturer":"Polaris"}
    c.application.bot_data[oemixi._ADMIN_POLARIS_PRICE_RESOLVER_KEY]=resolver
    with patch.object(oemixi,"_resolve_runtime_offer",new_callable=AsyncMock) as gateway:
        offer=asyncio.run(oemixi._resolve_admin_oem_offer("2206328",c,manufacturer="Polaris"))
        assert offer["manufacturer"]=="Polaris"
        gateway.assert_not_awaited()
        other=asyncio.run(oemixi._resolve_admin_oem_offer("709200703",c,manufacturer="Can-Am"))
        gateway.assert_awaited_once()
    assert calls==["2206328"]
