import unittest
from reconciliation.test_admin_handlers import load,Fake
class FinanceNavigationTests(unittest.IsolatedAsyncioTestCase):
    async def test_01_debt_navigation(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_debt_rows=lambda:[],_admin_debt_text=lambda *x:"debt",_admin_debt_keyboard=lambda *x:None)
        f=Fake({"admin_oem_search":True},data="admdebt")
        await n["callback"](f.update,f.context)
        self.assertEqual(f.q.edit_message_text.await_args.args[0],"debt")
        self.assertNotIn("admin_oem_search",f.context.user_data)
    async def test_02_aging_navigation(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_aging_all_rows=lambda:[],_admin_aging_sum_text=lambda *x:"aging",_admin_aging_sum_keyboard=lambda *x:None)
        f=Fake({"admin_oem_search":True},data="admagingsum:all:0")
        await n["callback"](f.update,f.context)
        self.assertEqual(f.q.edit_message_text.await_args.args[0],"aging")
    async def test_03_finance_navigation(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_finance_text=lambda did:"finance:"+did,_admin_finance_keyboard=lambda did:None)
        f=Fake({"admin_oem_search":True},data="admfin:view:d1")
        await n["callback"](f.update,f.context)
        self.assertEqual(f.q.edit_message_text.await_args.args[0],"finance:d1")
    async def test_04_debit_navigation(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_dealers=lambda:[{"id":"d1","name":"Dealer"}])
        f=Fake({"admin_oem_search":True},data="admfin:debit:d1")
        await n["callback"](f.update,f.context)
        self.assertIn("admin_debit",f.context.user_data)
        self.assertNotIn("admin_oem_search",f.context.user_data)
    async def test_05_credit_cleared_on_search(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        f=Fake({"admin_credit":{"step":"amount"}},data="admsearch")
        await n["callback"](f.update,f.context)
        self.assertNotIn("admin_credit",f.context.user_data)
    async def test_06_topup_cleared_on_search(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        f=Fake({"admin_topup":{"step":"reason"}},data="admsearch")
        await n["callback"](f.update,f.context)
        self.assertNotIn("admin_topup",f.context.user_data)
if __name__=="__main__":unittest.main()
