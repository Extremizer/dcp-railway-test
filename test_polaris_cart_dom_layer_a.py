import unittest
from datetime import datetime,timezone
from polaris_dealer_cart_dom import extract_and_validate
NOW=datetime(2026,10,16,12,tzinfo=timezone.utc)
TOKEN="6ad1990e7863f17456494535"
def row(sku="2206328",visible=None,token=TOKEN,price="184.07",qty="1",total="184.07",brand="Polaris",currency="USD",sell=None,old="2204967",omit_price=False,control_tokens=None):
    visible=visible or sku
    attrs=f'data-brand="{brand}" data-currency="{currency}" data-itemid="{sku}" data-itemname="QUICKDRIVE BELT KIT" data-sellprice="{sell or price}" data-origprice="{price}" data-qty="{qty}"'
    control_tokens=control_tokens or [token]*3
    controls="".join(f'<a href="/cart/{action}/{tok}" {attrs}></a>' for action,tok in zip(("decrease","increase","remove"),control_tokens))
    return f'<div class="cartitem container_16"><div class="grid_4 desc"><span class="title">QUICKDRIVE BELT KIT</span><span class="sku">Polaris item: {visible}</span></div><div class="grid_4 opt">Old Sku: {old}</div><div class="grid_2 itemprice">{"" if omit_price else f"<p class=price>&#36;{price}</p>"}</div><div class="grid_2 modify"><p class="qty">{qty}</p>{controls}</div><div class="grid_2 ext"><p class="price">&#36;{total}</p></div></div>'
def html(*rows,usd=True):
    return "<html><body>"+("Prices shown are USD" if usd else "")+"".join(rows)+"</body></html>"
def check(document,**kwargs):
    kw=dict(requested_oem="2206328",dealer_authenticated=True,category_server_verified=True,observed_at=NOW.isoformat(),source_provenance="passive-cart-observation")
    kw.update(kwargs)
    return extract_and_validate(document,now=NOW,**kw)
class LayerATests(unittest.TestCase):
    def reject(self,document,**kwargs):
        self.assertEqual(check(document,**kwargs)["status"],"DEALER_PRICE_UNVERIFIED")
    def test_01_exact(self):
        r=check(html(row()))
        self.assertEqual(r["status"],"VALIDATED_DEALER_CART_CANDIDATE")
        self.assertEqual(r["unit_price_usd"],"184.07")
    def test_02_unrelated_rows(self):
        self.assertEqual(check(html(row(),row(sku="8899999",token="different")))["status"],"VALIDATED_DEALER_CART_CANDIDATE")
    def test_03_duplicate(self): self.reject(html(row(),row(token="other")))
    def test_04_sku_conflict(self): self.reject(html(row(visible="9999999")))
    def test_05_control_item_conflict(self): self.reject(html(row(sku="9999999",visible="2206328")))
    def test_06_split_tokens(self): self.reject(html(row(control_tokens=[TOKEN,"other",TOKEN])))
    def test_07_missing_token(self): self.reject(html(row(token="")))
    def test_08_missing_unit(self): self.reject(html(row(omit_price=True)))
    def test_09_total_only(self): self.reject(html(row(omit_price=True,total="184.07")))
    def test_10_sell_conflict(self): self.reject(html(row(sell="209.99")))
    def test_11_qty_conflict(self): self.reject(html(row(qty="2",total="184.07"))) # B subtotal check
    def test_12_no_usd(self): self.reject(html(row(),usd=False))
    def test_13_wrong_manufacturer(self): self.reject(html(row(brand="Honda")))
    def test_14_previous_sku(self):
        self.assertEqual(check(html(row(old="2204967")))["status"],"VALIDATED_DEALER_CART_CANDIDATE")
    def test_15_bad_money(self): self.reject(html(row(price="xx")))
    def test_16_bad_qty(self): self.reject(html(row(qty="nope")))
    def test_17_no_auth(self): self.reject(html(row()),dealer_authenticated=False)
    def test_18_no_server_category(self): self.reject(html(row()),category_server_verified=False)
    def test_19_token_reuse(self): self.reject(html(row(),row(sku="8899999",token=TOKEN)))
    def test_20_total_contradiction(self): self.reject(html(row(total="200.00")))
if __name__=="__main__": unittest.main()
