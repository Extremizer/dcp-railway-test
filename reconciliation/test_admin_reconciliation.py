import ast
import pathlib
import unittest

SOURCE=pathlib.Path(__file__).with_name("oemixibot_app.py").read_text(encoding="utf-8")
TREE=ast.parse(SOURCE)
FUNCTIONS={n.name:n for n in TREE.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef))}
def body(name): return ast.get_source_segment(SOURCE,FUNCTIONS[name])
class AdminReconciliationTests(unittest.TestCase):
    def test_01_admin_root_search_and_debt_aging(self):
        text=body("_admin_root_keyboard")
        for label in ("🔎 Поиск по OEM","💳 Задолженность дилеров","⏳ Aging по дилерам","admdealer:"):
            self.assertIn(label,text)
    def test_02_admin_search_callback(self):
        text=body("callback")
        self.assertIn('data=="admsearch"',text)
        self.assertIn('"admin_oem_search"',text)
    def test_03_admin_text_mode_and_rbac(self):
        text=body("oem_message")
        self.assertIn('context.user_data.get("admin_oem_search")',text)
        self.assertIn("is_telegram_admin",text)
    def test_04_admin_root_rbac(self):
        self.assertIn("is_telegram_admin",body("admin_command"))
    def test_05_admin_home_clear_search(self):
        text=body("callback")
        self.assertIn('data=="admhome"',text)
        self.assertIn('"admin_oem_search"',text)
    def test_06_finance_routes_preserved(self):
        text=body("callback")
        for route in ("admdebt","admagingsum","admaging:","admtopup:","admdebit:","admfin:"):
            self.assertIn(route,text)
    def test_07_admin_search_has_no_cart_checkout(self):
        search=body("_resolve_admin_oem_offer")
        for forbidden in ("cart_add(","checkout_cart(","cart_set_qty("):
            self.assertNotIn(forbidden,search)
    def test_08_search_price_fail_closed(self):
        text=body("_admin_offer_text")
        self.assertIn("format_admin_price",text)
        self.assertIn("oemixi_admin_polaris_cart_price",text)
        self.assertNotIn("dl_usd",text)
    def test_09_current_dealer_buttons(self):
        self.assertIn('admdealer:',body("_admin_root_keyboard"))
    def test_10_aging_scheduler_preserved(self):
        self.assertIn("_aging_alert_job",body("build_application"))
    def test_11_callback_registration(self):
        text=body("build_application")
        for k in ("admsearch$","admhome$","admdebt$","admagingsum:","admaging:"):
            self.assertIn(k,text)
    def test_12_finance_navigation_preserved(self):
        for f in ("_admin_debt_rows","_admin_aging_all_rows","_admin_aging_sum_text","_admin_finance_keyboard"):
            self.assertIn(f,FUNCTIONS)
if __name__=="__main__": unittest.main()
