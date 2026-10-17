"""Isolated WEB2 Cart M3 browser coverage."""
import json, unittest
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server, mock_card

class CartM3(unittest.TestCase):
    def test_three_oems_quantity_remove_and_handoff(self):
        with mock_server() as (base,handoffs), sync_playwright() as pw:
            b=pw.chromium.launch(headless=True)
            p=b.new_page(viewport={"width":390,"height":844})
            def response(route):
                oem=route.request.url.split("/")[-1]
                card=mock_card()
                card.update(oem=oem,requested_oem=oem)
                route.fulfill(status=200,content_type="application/json",body=json.dumps(card))
            p.route("**/api/v1/oem/*",response)
            p.goto(base+"/web2")
            for oem,qty in [("AAA",2),("BBB",1),("CCC",3)]:
                p.locator("#oemInput").fill(oem)
                p.locator("#searchButton").click()
                p.wait_for_function("(v)=>document.querySelector('#productOem').textContent===v",arg=oem)
                for _ in range(qty-1): p.locator("#qtyPlus").click()
                p.locator('[data-offer-key="usa"]').click()
                p.locator("#cartClose").click()
            self.assertEqual(p.locator("#cartCount").inner_text(),"6")
            self.assertEqual(p.locator("#cartItems .cart-item").count(),3)
            self.assertEqual(p.locator("#cartTotal").inner_text().replace("\xa0"," "),"175 200 ₽")
            p.locator("#cartButton").click()
            p.locator('[data-remove="1"]').click()
            self.assertEqual(p.locator("#cartItems .cart-item").count(),2)
            self.assertIn("AAA",p.locator("#cartItems").inner_text())
            self.assertIn("CCC",p.locator("#cartItems").inner_text())
            p.locator("#cartClose").click()
            p.locator("#oemInput").fill("BBB");p.locator("#searchButton").click()
            p.wait_for_function("()=>document.querySelector('#productOem').textContent==='BBB'")
            p.locator('[data-offer-key="usa"]').click()
            self.assertEqual(p.locator("#cartItems .cart-item").count(),3)
            self.assertEqual(p.locator("#cartCount").inner_text(),"6")
            self.assertFalse(p.evaluate("document.documentElement.scrollWidth > innerWidth+1"))
            p.route("https://t.me/**",lambda route:route.fulfill(status=200,body="ok"))
            p.locator("#telegramButton").click()
            p.wait_for_url("https://t.me/Extremizer_bot")
            self.assertEqual([(x["oem"],x["qty"]) for x in handoffs[0]["items"]],
                             [("AAA",2),("CCC",3),("BBB",1)])
            b.close()

    def test_corrupt_persisted_snapshot_fails_closed(self):
        with mock_server() as (base,_),sync_playwright() as pw:
            b=pw.chromium.launch(headless=True)
            p=b.new_page()
            corrupt=[
                {"key":"X|A|usa","manufacturer":"X","oem":"A","offer_source":"usa","warehouse_id":None,"unit_rub":float("nan"),"qty":1},
                {"key":"X|B|usa","manufacturer":"X","oem":"B","offer_source":"usa","warehouse_id":None,"unit_rub":100,"qty":0},
                {"key":"X|C|usa","manufacturer":"X","oem":"C","offer_source":"usa","warehouse_id":None,"unit_rub":1.5,"qty":2},
                {"key":"X|D|warehouse:1","manufacturer":"X","oem":"D","offer_source":"warehouse","warehouse_id":1,"available_quantity":1,"unit_rub":100,"qty":2}
            ]
            p.add_init_script("localStorage.setItem('extremizer_web2_cart', "+json.dumps(json.dumps(corrupt))+");")
            p.goto(base+"/web2")
            self.assertEqual(p.locator("#cartCount").inner_text(),"0")
            p.locator("#cartButton").click()
            self.assertTrue(p.locator("#cartEmpty").is_visible())
            self.assertFalse(p.locator("#cartFooter").is_visible())
            b.close()

if __name__=="__main__": unittest.main()
