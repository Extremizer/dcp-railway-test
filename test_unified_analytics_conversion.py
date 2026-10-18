import json,sqlite3,tempfile,unittest
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pricing_analytics as legacy
import unified_analytics_core as core
from unified_analytics_transport import build_router,signed_headers,PATH_CONVERSION,PATH

SECRETS={s:"s-"+s+"-"+"x"*50 for s in core.SOURCES}
class ExactAttribution(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    self.db=str(Path(self.tmp.name)/"canonical.sqlite")
    legacy.init_analytics(self.db);core.initialize(self.db)
    app=FastAPI();app.include_router(build_router(canonical_db=self.db,secrets_by_source=SECRETS))
    self.client=TestClient(app)
 def tearDown(self):self.client.close();self.tmp.cleanup()
 def request(self,source,key,user,oem="3211070"):
    return core.begin(self.db,source_bot=source,source_kind="private",oem=oem,
        delivery_key=key,telegram_user_id=user,source_chat_id=user,telegram_message_id=1)
 def convert(self,source,key,user,action="CART",oem="3211070",order_id=None,path=PATH_CONVERSION):
    d={"source_bot":source,"delivery_key":key,"oem":oem,
       "telegram_user_id":user,"action":action}
    if order_id is not None:d["order_id"]=order_id
    body=json.dumps(d).encode()
    return self.client.post(PATH_CONVERSION,data=body,
        headers=signed_headers(SECRETS[source],source,body,path=path))
 def test_same_oem_different_requests_and_users(self):
    old=self.request("pricing","old",10)
    newer=self.request("pricing","new",10)
    other=self.request("pricing","third",11)
    self.assertEqual(self.convert("pricing","new",10).status_code,200)
    self.assertEqual(self.convert("pricing","new",10,action="ORDER_PLACED",order_id="E-123").status_code,200)
    self.assertEqual(self.convert("pricing","new",10,action="ORDER_PLACED",order_id="E-123").status_code,200)
    self.assertEqual(self.convert("pricing","old",11).status_code,409)
    self.assertEqual(self.convert("pricing","third",10).status_code,409)
    self.assertEqual(self.convert("pricing","new",10,action="ORDER_PLACED",order_id="STOLEN").status_code,409)
    with sqlite3.connect(self.db) as c:
        data=c.execute("SELECT id,cart_added,order_id FROM pricing_crm_requests ORDER BY id").fetchall()
        self.assertEqual([(i,c,o) for i,c,o in data],[(old,0,None),(newer,1,"E-123"),(other,0,None)])
        count=c.execute("SELECT COUNT(*) FROM analytics_conversion_events").fetchone()[0]
        self.assertEqual(count,2)
 def test_probnk_no_cart_and_unplaced_order_denied(self):
    self.request("probnik","prob",12)
    self.assertEqual(self.convert("probnik","prob",12).status_code,403)
    self.request("oemixibot","dealer",12)
    self.assertEqual(self.convert("oemixibot","dealer",12,action="ORDER_PLACED",order_id="E-1").status_code,409)
    self.assertEqual(self.convert("oemixibot","dealer",12,action="PAID",order_id="E-1").status_code,409)
 def test_path_signature_body_and_oem_binding(self):
    self.request("pricing","x",12)
    self.assertEqual(self.convert("pricing","x",12,path=PATH).status_code,401)
    self.assertEqual(self.convert("pricing","x",12,oem="2222222").status_code,409)
    self.assertEqual(self.convert("pricing","absent",12).status_code,409)
if __name__=="__main__":unittest.main()
