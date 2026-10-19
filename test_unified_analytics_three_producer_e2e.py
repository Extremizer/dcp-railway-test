"""Isolated actual producer adapter → signed receiver → canonical CRM E2E."""
import json,os,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pricing_analytics as legacy
import unified_analytics_core as core
import unified_analytics_outbox as outbox
import unified_analytics_producer as producer
from unified_analytics_transport import build_router,signed_headers,PATH

SECRETS={s:"synthetic-"+s+"-secret-"+"x"*48 for s in core.SOURCES}
def update(uid,mid=10,chat=20):
    return NS(effective_user=NS(id=uid,username="synthetic",first_name="Fake",last_name="User"),
              effective_chat=NS(id=chat,title=None),effective_message=NS(message_id=mid))
class ThreeProducers(unittest.TestCase):
 def setUp(self):
    self.tmp=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    root=Path(self.tmp.name)
    self.canonical=str(root/"canonical.sqlite")
    self.producer_db=str(root/"existing-runtime.sqlite")
    legacy.init_analytics(self.canonical)
    core.initialize(self.canonical)
    with sqlite3.connect(self.producer_db) as conn:conn.execute("CREATE TABLE existing_bot_data(id INTEGER)")
    outbox.initialize(self.producer_db)
    app=FastAPI();app.include_router(build_router(canonical_db=self.canonical,secrets_by_source=SECRETS))
    self.client=TestClient(app)
 def tearDown(self):self.client.close();self.tmp.cleanup()
 def send(self,payload):
    body=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    headers=signed_headers(SECRETS[payload["source_bot"]],payload["source_bot"],body)
    response=self.client.post(PATH,data=body,headers=headers)
    return response.status_code==200
 def test_exact_three_bot_pipeline_and_currency(self):
    for source,uid,mid,price,currency in (
        ("probnik",11,1,29000,"RUB"),
        ("pricing",12,1,35000,"RUB"),
    ):
        key=producer.begin_request_from_update(self.producer_db,
             source_bot=source,source_kind="private",update=update(uid,mid),oem="3211070")
        self.assertIsNotNone(key)
        self.assertTrue(producer.complete_request(self.producer_db,key,
             manufacturer="Polaris",result_status="FOUND",price_status="FOUND",
             display_price_amount=price,display_price_currency=currency,
             stock_rf_status="NONE",stock_rf=[]))
    with patch.object(producer,"_transmit",side_effect=self.send):
        result=producer.send_dealer_completed(self.producer_db,
                telegram_user_id=13,telegram_message_id=1,
                oem="3211070",manufacturer="Polaris",result_status="FOUND",
                price_status="FOUND",display_price_amount=114.99,display_price_currency="USD")
        self.assertTrue(result)
        self.assertEqual(producer.flush_pending(self.producer_db)["delivered"],0)
    rows=core.query(self.canonical,filters={})["items"]
    self.assertEqual(len(rows),3)
    self.assertEqual({r["source_bot"] for r in rows},set(core.SOURCES))
    self.assertEqual({r["display_price_currency"] for r in rows},{"USD","RUB"})
    self.assertEqual({r["manufacturer"] for r in rows},{"Polaris"})
 def test_recovery_after_timeout_and_restart(self):
    key=producer.begin_request_from_update(self.producer_db,
        source_bot="pricing",source_kind="workchat",
        update=update(22,mid=55,chat=-100),oem="417300574")
    self.assertTrue(producer.complete_request(self.producer_db,key,
        result_status="UNAVAILABLE",price_status="UNAVAILABLE"))
    with patch.object(producer,"_transmit",return_value=False):
        self.assertEqual(producer.flush_pending(self.producer_db)["deferred"],2)
    # New worker, same producer DB; force the next retry time in test only.
    with patch.object(producer,"_transmit",side_effect=self.send):
        recovered=outbox.flush(self.producer_db,producer._transmit,now=9999999999)
        self.assertEqual(recovered["delivered"],2)
    self.assertEqual(core.query(self.canonical,filters={"oem":"417300574"})["total"],1)
    self.assertEqual(core.query(self.canonical,filters={"source_bot":"pricing"})["items"][0]["source_kind"],"workchat")
 def test_duplicate_batch_and_no_source_cross_contamination(self):
    first=producer.begin_request_from_update(self.producer_db,source_bot="pricing",
            source_kind="private",update=update(4),oem="3211070",ordinal=0)
    second=producer.begin_request_from_update(self.producer_db,source_bot="pricing",
            source_kind="private",update=update(4),oem="3211070",ordinal=1)
    self.assertNotEqual(first,second)
    with patch.object(producer,"_transmit",side_effect=self.send):
        self.assertEqual(producer.flush_pending(self.producer_db)["delivered"],2)
    self.assertEqual(core.query(self.canonical,filters={"source_bot":"pricing"})["total"],2)
if __name__=="__main__":unittest.main()
