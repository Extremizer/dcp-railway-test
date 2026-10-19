"""Signed shared identity endpoint -> OEMixi client -> ADMIN resolution.
Disposable canonical-schema SQLite; no real Telegram, external HTTP or production DB.
"""
import ast
import asyncio
import hashlib
import hmac
import json
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient
import dealercostparts_manufacturer_finder_v6_6 as finder
import oemixibot_identity_client as remote
import oemixibot_app as bot
import probnik_app as probnik


def api(path):
    application = FastAPI()
    names = {"_require_oemixibot_identity_signature", "oemixibot_identity_lookup"}
    nodes = [node for node in ast.parse(Path("web_app.py").read_text(encoding="utf-8-sig")).body
             if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    if len(nodes) != 2:
        raise AssertionError("signed identity endpoint missing")
    core = SimpleNamespace(
        ORDERS_DB_FILE=path,
        finder=finder,
        init_orders_db=lambda: None,
        resolve_oem_identity=lambda oem: {"manufacturer":None,"manufacturer_candidates":[],"item_type":"part"},
    )
    ns = {"app":application, "core":core, "sqlite3":sqlite3,
          "Header":Header, "HTTPException":HTTPException,
          "Any":object, "hashlib":hashlib, "hmac":hmac,
          "time":time, "secrets":__import__("secrets"),
          "_oemixibot_identity_key":lambda:hashlib.sha256(
              b"extremizer-oemixibot-identity-v1\0"+b"isolated-test-token").digest()}
    exec(compile(ast.Module(body=nodes,type_ignores=[]),"web_app.py","exec"),ns)
    return TestClient(application)


class FakeResponse:
    def __init__(self, result):
        self.result=result
        self.status=result.status_code
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def read(self):
        return self.result.content


class ThreeInterfaceIdentity(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=Path(self.tmp.name)/"canonical.db"
        with sqlite3.connect(self.db) as conn:
            conn.execute("""CREATE TABLE probnik_oem_identity
                (manufacturer TEXT,oem TEXT,current_oem TEXT,
                 name TEXT,verified INTEGER,source_kind TEXT,source_ref TEXT,
                 last_verified_at TEXT)""")
            conn.execute("""CREATE TABLE dealer_price_cache
                (manufacturer TEXT,oem TEXT,dealer_price_usd REAL,last_verified_at TEXT)""")
        self.client=api(self.db)
        self.addCleanup(self.client.close)

    def seed(self, manufacturer):
        with sqlite3.connect(self.db) as conn:
            conn.execute("""INSERT INTO probnik_oem_identity VALUES
                (?, '417127467','417127467','Release Spring',1,
                 'verified_public_catalog','synthetic://identity',
                 '2026-10-19T00:00:00+00:00')""",(manufacturer,))

    def signed_request(self):
        with patch.dict("os.environ",{"OEMIXIBOT_TOKEN":"isolated-test-token"}):
            headers=remote._headers(int(time.time()))
        return self.client.get("/internal/oemixibot/identity?oem=417127467",headers=headers)

    def test_probnik_and_signed_shared_identity(self):
        self.seed("Ski-Doo")
        with patch.object(probnik,"ORDERS_DB",self.db),patch.object(
            probnik.dp_live_bridge,"request_live_dp",return_value={"status":"ADMISSION_CLOSED"}
        ) as dp:
            found=probnik._identity_and_price("417127467")
        self.assertEqual(found["manufacturer"],"Ski-Doo")
        self.assertIsNone(found["dp_usd"])
        dp.assert_called_once()
        response=self.signed_request()
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json()["manufacturer"],"Ski-Doo")
        self.assertEqual(response.json()["status"],"FOUND")

    def test_signed_transport_and_admin_consumer(self):
        self.seed("Ski-Doo")
        def fake_urlopen(req,timeout):
            return FakeResponse(self.client.get(req.full_url.split("?",1)[0].replace(
                "https://isolated.test","")+"?"+req.full_url.split("?",1)[1],
                headers=dict(req.header_items())))
        with patch.dict("os.environ",{"OEMIXIBOT_TOKEN":"isolated-test-token"}),patch.object(
            remote.urllib.request,"urlopen",side_effect=fake_urlopen):
            payload=remote.lookup_shared_identity("417127467",base_url="https://isolated.test")
        self.assertEqual(payload["manufacturer"],"Ski-Doo")
        seen=[]
        async def runtime(oem,ctx):
            return {"status":"FOUND","manufacturer":"Ski-Doo","dl_usd":None} if seen else {"status":"IDENTITY_REQUIRED"}
        with patch.object(bot,"_resolve_runtime_offer",side_effect=runtime),patch.object(
            bot,"lookup_shared_identity",return_value=payload
        ),patch.object(bot.oem_identity_service,"remember",side_effect=lambda *a,**kw:seen.append(a) or True):
            offer=asyncio.run(bot._resolve_admin_oem_offer(
                "417127467",SimpleNamespace(application=SimpleNamespace(bot_data={}))))
        self.assertEqual(offer["manufacturer"],"Ski-Doo")
        self.assertEqual(len(seen),1)
        self.assertIn("417127467",seen[0])

    def test_absent_conflicting_and_invalid_signature_fail_closed(self):
        unsigned=self.client.get("/internal/oemixibot/identity?oem=417127467")
        self.assertEqual(unsigned.status_code,401)
        response=self.signed_request()
        self.assertEqual(response.json()["status"],"UNRESOLVED")
        self.seed("Ski-Doo")
        self.seed("Polaris")
        response=self.signed_request()
        self.assertEqual(response.json()["status"],"UNRESOLVED")
        self.assertIsNone(response.json()["manufacturer"])

    def test_admin_non_polaris_does_not_use_cart_provider(self):
        self.seed("Ski-Doo")
        signed=self.signed_request().json()
        from unittest.mock import Mock
        polaris=Mock(side_effect=AssertionError("wrong Polaris cart"))
        calls=[]
        async def runtime(oem, context):
            calls.append(oem)
            return {"status":"IDENTITY_REQUIRED"} if len(calls)==1 else {"status":"PRICE_UNAVAILABLE","manufacturer":"Ski-Doo","dl_usd":None}
        ctx=SimpleNamespace(application=SimpleNamespace(bot_data={bot._ADMIN_POLARIS_PRICE_RESOLVER_KEY:polaris}))
        with patch.object(bot,"lookup_shared_identity",return_value=signed),patch.object(
            bot,"_resolve_runtime_offer",side_effect=runtime),patch.object(
            bot.oem_identity_service,"remember",return_value=True):
            result=asyncio.run(bot._resolve_admin_oem_offer("417127467",ctx))
        self.assertEqual(result["manufacturer"],"Ski-Doo")
        polaris.assert_not_called()

    def test_dealer_message_uses_shared_identity_without_telegram(self):
        self.seed("Ski-Doo")
        signed=self.signed_request().json()
        messages=[]
        class Msg:
            text="417127467"
            async def reply_text(self, message,**kwargs):
                messages.append(message)
        calls=[]
        async def runtime(oem,context):
            calls.append(oem)
            return {"status":"IDENTITY_REQUIRED"} if len(calls)==1 else {"status":"PRICE_UNAVAILABLE","manufacturer":"Ski-Doo","dl_usd":None}
        update=SimpleNamespace(effective_user=None,effective_message=Msg())
        ctx=SimpleNamespace(user_data={},application=SimpleNamespace(bot_data={},create_task=lambda coro:coro.close()))
        with patch.object(bot,"_dealer",return_value={"id":1}),patch.object(
            bot,"_resolve_runtime_offer",side_effect=runtime),patch.object(
            bot,"lookup_shared_identity",return_value=signed),patch.object(
            bot.oem_identity_service,"remember",return_value=True):
            asyncio.run(bot.oem_message(update,ctx))
        self.assertGreaterEqual(len(calls),2)
        self.assertTrue(any("Цена из США сейчас недоступна" in m for m in messages))


if __name__=="__main__":
    unittest.main()
