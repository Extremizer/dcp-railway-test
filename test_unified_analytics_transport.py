"""Synthetic signed HTTPS-contract behavior on isolated in-memory ASGI HTTP client."""
import json,tempfile,time,unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pricing_analytics as legacy
import unified_analytics_core as core
from unified_analytics_transport import build_router,PATH,signed_headers

SECRETS={s:"synthetic-"+s+"-"+"s"*40 for s in core.SOURCES}
def event(source,delivery="tg:1:2:0:3211070",**update):
    d={"source_bot":source,"source_kind":"private","delivery_key":delivery,
       "telegram_user_id":123,"telegram_message_id":2,"source_chat_id":1,
       "username":"synthetic_user","first_name":"Fake","last_name":"Person",
       "oem":"3211070","manufacturer":"Polaris","result_status":"FOUND",
       "price_status":"FOUND","display_price_amount":114.99 if source=="oemixibot" else 11900,
       "display_price_currency":"USD" if source=="oemixibot" else "RUB",
       "stock_rf_status":"NOT_APPLICABLE" if source=="oemixibot" else "NONE","stock_rf":[]}
    d.update(update);return d

class SignedTransport(unittest.TestCase):
 def setUp(self):
    self.temp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    self.db=str(Path(self.temp.name)/"canonical.sqlite")
    legacy.init_analytics(self.db);core.initialize(self.db)
    self.app=FastAPI();self.app.include_router(build_router(canonical_db=self.db,secrets_by_source=SECRETS))
    self.c=TestClient(self.app)
 def tearDown(self):self.c.close();self.temp.cleanup()
 def call(self,payload,headers=None,secret=None,ts=None):
    body=json.dumps(payload,sort_keys=True).encode()
    head=headers if headers is not None else signed_headers(
        secret or SECRETS[payload["source_bot"]],payload["source_bot"],body,timestamp=ts)
    return self.c.post(PATH,data=body,headers=head)
 def test_three_sources_one_canonical_db_and_duplicate(self):
    ids=[]
    for source in sorted(core.SOURCES):
        payload=event(source)
        r=self.call(payload)
        self.assertEqual(r.status_code,200,r.text)
        ids.append(r.json()["request_id"])
        self.assertEqual(self.call(payload).json()["request_id"],ids[-1])
    self.assertEqual(len(set(ids)),3)
    self.assertEqual(core.query(self.db,filters={})["total"],3)
 def test_conflicts_and_security(self):
    payload=event("pricing")
    self.assertEqual(self.call(payload).status_code,200)
    self.assertEqual(self.call(event("pricing",oem="9999999")).status_code,409)
    self.assertEqual(self.call(event("pricing",telegram_user_id=999)).status_code,409)
    self.assertEqual(self.call(event("pricing",telegram_message_id=55)).status_code,409)
    self.assertEqual(self.call(event("pricing",manufacturer="Arctic Cat")).status_code,409)
    self.assertEqual(self.call(event("pricing",display_price_amount=1)).status_code,409)
    body=json.dumps(payload,sort_keys=True).encode()
    signed=signed_headers(SECRETS["pricing"],"pricing",body)
    self.assertEqual(self.call(event("pricing",oem="bad"),headers=signed).status_code,401)
    self.assertEqual(self.call(payload,secret="wrong-"+"x"*40).status_code,401)
    self.assertEqual(self.call(payload,ts=int(time.time())-70).status_code,401)
 def test_unknown_manufacturer_enrichment(self):
    blank=event("probnik",manufacturer=None,result_status=None,price_status=None,
                display_price_currency=None,display_price_amount=None)
    self.assertEqual(self.call(blank).status_code,200)
    final=event("probnik",manufacturer="Polaris")
    self.assertEqual(self.call(final).status_code,200)
    self.assertEqual(self.call(blank).status_code,200)
    self.assertEqual(core.query(self.db,filters={"manufacturer":"Polaris"})["total"],1)
 def test_missing_db_is_503_no_shadow(self):
    missing=str(Path(self.temp.name)/"missing.sqlite")
    app=FastAPI();app.include_router(build_router(canonical_db=missing,secrets_by_source=SECRETS))
    with TestClient(app) as client:
        payload=event("pricing")
        body=json.dumps(payload,sort_keys=True).encode()
        r=client.post(PATH,data=body,headers=signed_headers(SECRETS["pricing"],"pricing",body))
        self.assertEqual(r.status_code,503)
    self.assertFalse(Path(missing).exists())
if __name__=="__main__":unittest.main()
