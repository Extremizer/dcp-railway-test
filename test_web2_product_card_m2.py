"""Product card M2 isolated contract/browser regression."""
import json, unittest
from playwright.sync_api import sync_playwright
from test_web2_browser import mock_server, mock_card

class ProductCardM2(unittest.TestCase):
    def test_product_cases(self):
        cases = ["NORMAL","NO-NAME","NO-MANUFACTURER","NO-RRP","LOW-RRP","MULTI","MALFORMED","MALICIOUS","BAD-PRICE"]
        with mock_server() as (base, _), sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":390,"height":844})
            def handler(route):
                oem=route.request.url.split("/")[-1]
                c=mock_card()
                c["oem"]=oem
                c["requested_oem"]=oem
                if oem=="NO-NAME": c.pop("name")
                if oem=="NO-MANUFACTURER": c.pop("manufacturer")
                if oem=="NO-RRP": c["price"]["msrp_rub"]=None
                if oem=="LOW-RRP": c["price"]["msrp_rub"]=10000
                if oem=="MULTI":
                    c["stock"]["warehouses"].append({"warehouse_id":2,"warehouse":"Склад 2","quantity":3,"price_rub":35000})
                if oem=="MALFORMED":
                    c["stock"]["warehouses"].append({"warehouse":"broken"})
                    c["offers"].append({"source":"warehouse","key":"bad","can_add":True,"price_rub":"NaN","warehouse_id":2})
                if oem=="MALICIOUS":
                    c["name"]="<script>window.__pwned=1</script>"
                    c["stock"]["warehouses"][0]["warehouse"]="<script>window.__pwned=1</script>"
                if oem=="BAD-PRICE":
                    c["price"]["customer_rub"]=None
                    c["offers"][0]["price_rub"]=None
                route.fulfill(status=200,content_type="application/json",body=json.dumps(c))
            page.route("**/api/v1/oem/*",handler)
            page.goto(base+"/web2")
            for oem in cases:
                page.locator("#oemInput").fill(oem)
                page.locator("#searchButton").click()
                page.wait_for_function("(x)=>document.querySelector('#productOem').textContent===x",arg=oem)
                self.assertTrue(page.locator("#product").is_visible())
                self.assertFalse(page.evaluate("document.documentElement.scrollWidth>innerWidth+1"))
                if oem in ("NO-RRP","LOW-RRP","BAD-PRICE"):
                    self.assertFalse(page.locator("#msrpBlock").is_visible())
                if oem=="NORMAL": self.assertTrue(page.locator("#msrpBlock").is_visible())
                if oem=="NO-NAME": self.assertEqual(page.locator("#productName").inner_text(),"Название уточняется")
                if oem=="MALFORMED":
                    self.assertNotIn("NaN",page.locator("#stock").inner_text())
                    self.assertTrue(page.locator('[data-offer-key="bad"]').is_disabled())
                if oem=="MALICIOUS":
                    self.assertEqual(page.locator("#product script").count(),0)
                    self.assertFalse(page.evaluate("Boolean(window.__pwned)"))
                if oem=="BAD-PRICE": self.assertTrue(page.locator('[data-offer-key="usa"]').is_disabled())
            browser.close()

    def test_stock_error_and_stale_request(self):
        with mock_server() as (base,_), sync_playwright() as pw:
            browser=pw.chromium.launch(headless=True)
            page=browser.new_page(viewport={"width":390,"height":844})
            def search(route):
                oem=route.request.url.split("/")[-1]
                c=mock_card();c["oem"]=oem;c["requested_oem"]=oem
                c["stock"]={"known":False,"refreshing":True,"warehouses":[]}
                route.fulfill(status=200,content_type="application/json",body=json.dumps(c))
            page.route("**/api/v1/oem/A",search)
            page.route("**/api/v1/oem/B",search)
            page.route("**/api/v1/oem/A/stock?*",lambda r:r.fulfill(status=500,body="broken"))
            page.route("**/api/v1/oem/B/stock?*",lambda r:r.fulfill(status=200,content_type="application/json",body=json.dumps({"known":True,"refreshing":False,"warehouses":[{"warehouse":"Склад 3","quantity":2,"price_rub":30000}]})))
            page.goto(base+"/web2")
            page.locator("#oemInput").fill("A");page.locator("#searchButton").click()
            page.wait_for_function("()=>document.querySelector('#productOem').textContent==='A'")
            page.locator("#oemInput").fill("B");page.locator("#searchButton").click()
            page.wait_for_function("()=>document.querySelector('#productOem').textContent==='B'")
            page.locator("#stock").get_by_text("Склад 3").wait_for(timeout=10000)
            self.assertEqual(page.locator("#productOem").inner_text(),"B")
            self.assertTrue(page.locator("#customerPrice").is_visible())
            browser.close()

if __name__=="__main__":unittest.main()
