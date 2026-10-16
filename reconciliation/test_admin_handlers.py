import ast, asyncio, pathlib, types, unittest
from unittest.mock import AsyncMock
SOURCE=pathlib.Path(__file__).with_name("oemixibot_app.py").read_text(encoding="utf-8")
T=ast.parse(SOURCE)
def load(*names):
    nodes=[n for n in T.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
    module=ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[]))
    class Button:
        def __init__(self,text,callback_data):self.text=text;self.callback_data=callback_data
    class Markup:
        def __init__(self,rows):self.inline_keyboard=rows
    ns=dict(InlineKeyboardButton=Button,InlineKeyboardMarkup=Markup,ACCESS_DENIED="NO",
       is_telegram_admin=lambda uid:uid==123,_admin_dealers=lambda:[{"id":"d1","name":"Dealer"}],
       normalize_oem=lambda text:(text or "").strip().upper(),
       _resolve_admin_oem_offer=AsyncMock(return_value={"status":"DEALER_PRICE_UNVERIFIED","price_error":"hold"}),
       _dealer=lambda update:None)
    exec(compile(module,"reconciliation/oemixibot_app.py","exec"),ns)
    return ns
class Fake:
    def __init__(self,mode=None,user=123,data="admsearch",text="2206328"):
        self.context=types.SimpleNamespace(user_data=dict(mode or {}))
        self.q=types.SimpleNamespace(data=data,answer=AsyncMock(),edit_message_text=AsyncMock())
        self.message=types.SimpleNamespace(text=text,reply_text=AsyncMock())
        self.update=types.SimpleNamespace(effective_user=types.SimpleNamespace(id=user),callback_query=self.q,effective_message=self.message)
class HandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_01_admin_command_keyboard(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","admin_command");f=Fake()
        await n["admin_command"](f.update,f.context)
        k=f.message.reply_text.await_args.kwargs["reply_markup"]
        self.assertEqual([r[0].text for r in k.inline_keyboard][:3],["🔎 Поиск по OEM","💳 Задолженность дилеров","⏳ Aging по дилерам"])
        self.assertIn("👤",k.inline_keyboard[3][0].text)
    async def test_02_mode_enter_and_wizards_clear(self):
        # callback extraction limits execution to branch under test.
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_dealers=lambda:[{"id":"d1","name":"Dealer"}],_admin_debt_rows=lambda:[],_admin_debt_text=lambda *a:"debt",_admin_debt_keyboard=lambda *a:None,_admin_aging_all_rows=lambda:[],_admin_aging_sum_text=lambda *a:"aging",_admin_aging_sum_keyboard=lambda *a:None)
        f=Fake({"admin_credit":1,"admin_topup":2,"admin_debit":3,"admin_due":4})
        await n["callback"](f.update,f.context)
        self.assertEqual(f.context.user_data,{"admin_oem_search":True})
    async def test_03_admin_text_search(self):
        n=load("_admin_search_keyboard","_admin_offer_text","oem_message")
        f=Fake({"admin_oem_search":True})
        await n["oem_message"](f.update,f.context)
        self.assertIn("Дилерская цена недоступна",f.message.reply_text.await_args.args[0])
    async def test_04_non_admin(self):
        n=load("_admin_search_keyboard","_admin_offer_text","oem_message")
        f=Fake({"admin_oem_search":True},user=999)
        await n["oem_message"](f.update,f.context)
        self.assertNotIn("admin_oem_search",f.context.user_data)
        self.assertEqual(f.message.reply_text.await_args.args[0],"NO")
    async def test_05_home_clears_mode(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        f=Fake({"admin_oem_search":True,"admin_credit":True},data="admhome")
        await n["callback"](f.update,f.context)
        self.assertFalse(f.context.user_data)
    async def test_06_stale_dealer(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        f=Fake({"admin_oem_search":True},data="admdealer:d1")
        await n["callback"](f.update,f.context)
        self.assertNotIn("admin_oem_search",f.context.user_data)
    async def test_07_stale_debt(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_debt_rows=lambda:[],_admin_debt_text=lambda *x:"debt",_admin_debt_keyboard=lambda *x:None)
        f=Fake({"admin_oem_search":True},data="admdebt")
        await n["callback"](f.update,f.context)
        self.assertNotIn("admin_oem_search",f.context.user_data)
    async def test_08_stale_aging(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_aging_all_rows=lambda:[],_admin_aging_sum_text=lambda *x:"aging",_admin_aging_sum_keyboard=lambda *x:None)
        f=Fake({"admin_oem_search":True},data="admagingsum:all:0")
        await n["callback"](f.update,f.context)
        self.assertNotIn("admin_oem_search",f.context.user_data)
    async def test_09_stale_finance(self):
        n=load("_clear_admin_input_modes","_admin_root_keyboard","callback")
        n.update(_admin_finance_text=lambda *x:"balance",_admin_finance_keyboard=lambda *x:None)
        f=Fake({"admin_oem_search":True},data="admfin:view:d1")
        await n["callback"](f.update,f.context)
        self.assertNotIn("admin_oem_search",f.context.user_data)
if __name__=="__main__":unittest.main()
