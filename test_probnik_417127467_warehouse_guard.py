"""P0 safeguard: supplier warehouse B must not silently become warehouse 3.

The page fragment is a synthetic fixture resembling the adapter's current HTML
selectors; it is NOT an archival capture of OEM 417127467 or an ID mapping.
"""
from warehouse_adapters.vladextremelife import VladExtremeLifeAdapter

URL="https://vladextremelife.ru/catalog/synthetic/"
def store(name, store_id, quantity, price):
    return (f'<div class="store_title">{name}</div>'
            f'<div class="warehouse-stock" data-store="{store_id}" data-max="{quantity}">'
            f'<span itemprop="price" content="{price} руб">Цена</span></div>')

def test_store_b_26_not_equated_to_approved_store_3():
    html=store("СкладВ Красноярск Удаленный",26,2,7650)
    result=VladExtremeLifeAdapter._parse_exact_page(URL,html,lookup_source="synthetic")
    assert result.status=="out_of_stock"
    assert result.quantity==0
    assert result.details["included_stores"]==[]

def test_approved_krasnoyarsk_only_and_foreign_ignored():
    html=("".join([
        store("Склад1 Красноярск Высотная",11,1,7650),
        store("Склад3 Красноярск Удаленный",19,2,7650),
        store("СкладВ Красноярск Удаленный",26,2,7650),
        store("Склад3 Москва",31,70,700),
        store("Склад2 Санкт-Петербург",32,80,900)]))
    result=VladExtremeLifeAdapter._parse_exact_page(URL,html,lookup_source="synthetic")
    assert result.status=="in_stock"
    assert result.quantity==3
    assert result.price_rub==7650
    assert len(result.details["included_stores"])==2
    assert all("Красноярск" in s["name"] for s in result.details["included_stores"])

def test_no_warehouse_structure_fail_closed():
    result=VladExtremeLifeAdapter._parse_exact_page(URL,"<h1>417127467</h1>",lookup_source="synthetic")
    assert result.status=="quantity_unknown"
    assert result.quantity is None
