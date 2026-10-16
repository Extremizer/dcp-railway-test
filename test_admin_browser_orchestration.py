import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from polaris_existing_browser_controller import ExistingBrowserProbeAdapter
from polaris_probe_cart_transaction import run_probe
from polaris_probe_errors import ProbeFailure
import oemixibot_app as app


class FailureAdapter:
    def __init__(self,mode):
        self.mode=mode
        self.last_failure_code=None
    def verify(self):
        return True
    def snapshot(self):
        if self.mode=="snapshot_cf":
            raise PermissionError("CLOUDFLARE")
        return {"lines":[]}
    def find_exact_oem(self,oem):
        if self.mode=="transition":
            raise ProbeFailure("DCP_TRANSITION_TIMEOUT")
        return {"manufacturer":"Polaris","item_category":"Parts",
                "requested_oem":oem,"resolved_oem":oem,
                "exact_match":True,"replacement_proven":False}


class ProbeFailureTests(unittest.TestCase):
    def test_01_cloudflare_surfaces(self):
        with tempfile.TemporaryDirectory() as d:
            r=run_probe("2205250",FailureAdapter("snapshot_cf"),
                lock_path=Path(d)/"lock",journal_path=Path(d)/"journal")
        self.assertEqual(r["status"],"CLOUDFLARE")
        self.assertEqual(r["failure_code"],"CLOUDFLARE")

    def test_02_transition_timeout_surfaces(self):
        with tempfile.TemporaryDirectory() as d:
            r=run_probe("2205250",FailureAdapter("transition"),
                lock_path=Path(d)/"lock",journal_path=Path(d)/"journal")
        self.assertEqual(r["status"],"DCP_TRANSITION_TIMEOUT")
        self.assertEqual(r["failure_code"],"DCP_TRANSITION_TIMEOUT")


class RetryHarness:
    def __init__(self):
        self.tab={"url":"https://www.dealercostparts.com/cart","title":"Your Shopping Cart | Dealer Cost Parts"}
        self.eval_count=0
    def get_tab(self): return dict(self.tab)
    def evaluate(self,tab,expr):
        self.eval_count+=1
        return {"started":True} if "started" in expr else True
    def verify(self): return True
    def authority(self,oem): return {}


class BrowserRetryTests(unittest.TestCase):
    def adapter(self,h,allow=False):
        return ExistingBrowserProbeAdapter(
            get_tab=h.get_tab,evaluate=h.evaluate,verify_session=h.verify,
            resolve_authority=h.authority,allow_mutation=allow,
            timeout_seconds=0.01,poll_seconds=0.001)

    def test_03_read_navigation_retries_once(self):
        h=RetryHarness();a=self.adapter(h)
        calls={"n":0}
        def wait(path,partsearch=None):
            calls["n"]+=1
            if calls["n"]==1:
                raise ProbeFailure("DCP_TRANSITION_TIMEOUT")
            return h.tab
        a._wait=wait
        a._navigate("https://www.dealercostparts.com/cart","/cart")
        self.assertEqual(calls["n"],2)
        self.assertEqual(h.eval_count,2)

    def test_04_mutation_action_never_retries(self):
        h=RetryHarness();a=self.adapter(h,allow=True)
        a._ensure_cart=lambda:h.tab
        a._wait=lambda *args,**kwargs: (_ for _ in ()).throw(ProbeFailure("DCP_TRANSITION_TIMEOUT"))
        with self.assertRaises(ProbeFailure):
            a._cart_action("validtoken123","remove")
        self.assertEqual(h.eval_count,1)


class FakeMessage:
    def __init__(self): self.replies=[]
    async def reply_text(self,text,reply_markup=None):
        self.replies.append(text)

class FakeBot:
    def __init__(self): self.sent=[]
    async def send_message(self,chat_id,text,reply_markup=None):
        self.sent.append((chat_id,text))

class FakeApplication:
    def __init__(self):
        self.bot_data={}
        self.tasks=[]
    def create_task(self,coro,name=None):
        task=asyncio.create_task(coro,name=name)
        self.tasks.append(task)
        return task

class FakeContext:
    def __init__(self):
        self.application=FakeApplication()
        self.bot=FakeBot()
        self.user_data={}

class AsyncAdminTests(unittest.IsolatedAsyncioTestCase):
    async def test_05_immediate_ack_before_probe_completion(self):
        gate=asyncio.Event()
        async def slow_resolve(oem,context):
            await gate.wait()
            return {"status":"ADMIN_DEALER_CART_PRICE_VERIFIED",
                    "manufacturer":"Polaris","item_category":"Parts",
                    "resolved_oem":"2206328","admin_unit_price_usd":"184.07",
                    "dealer_price_usd":None,"production_found":False}
        ctx=FakeContext()
        msg=FakeMessage()
        update=SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=11),
            effective_message=msg,
        )
        with patch.object(app,"_resolve_admin_oem_offer",slow_resolve):
            ok=await app._queue_admin_oem_probe(update,ctx,"2205250")
            self.assertTrue(ok)
            self.assertEqual(msg.replies,["⏳ Проверяю OEM 2205250…"])
            self.assertEqual(len(ctx.application.tasks),1)
            self.assertFalse(ctx.application.tasks[0].done())
            gate.set()
            await ctx.application.tasks[0]
        self.assertIn("$184.07",ctx.bot.sent[0][1])
        self.assertEqual(ctx.application.bot_data[app._ADMIN_POLARIS_PENDING_KEY],set())

    async def test_06_parallel_probe_blocked(self):
        ctx=FakeContext()
        key=(11,7)
        ctx.application.bot_data[app._ADMIN_POLARIS_PENDING_KEY]={key}
        msg=FakeMessage()
        update=SimpleNamespace(
            effective_user=SimpleNamespace(id=7),
            effective_chat=SimpleNamespace(id=11),
            effective_message=msg,
        )
        ok=await app._queue_admin_oem_probe(update,ctx,"2205250")
        self.assertFalse(ok)
        self.assertIn("ещё выполняется",msg.replies[0])
        self.assertEqual(ctx.application.tasks,[])


if __name__=="__main__":
    unittest.main()
