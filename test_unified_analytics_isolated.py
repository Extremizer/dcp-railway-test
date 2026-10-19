import tempfile,sqlite3,unittest
from pathlib import Path
from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import pricing_analytics as legacy
import unified_analytics_core as core
from unified_analytics_admin_api import create_admin_router

class UnifiedTests(unittest.TestCase):
 def setUp(self):
  self.t=tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
  self.db=str(Path(self.t.name)/'canonical-orders.db')
  legacy.init_analytics(self.db)
  core.initialize(self.db)
 def tearDown(self):self.t.cleanup()
 def event(self,source,key,oem='3211070'):
  return core.begin(self.db,source_bot=source,source_kind='private',oem=oem,
                    delivery_key=key,telegram_user_id=123,source_chat_id=456)
 def test_all_sources_deduplicate_exact_delivery(self):
  ids=[self.event(s,'telegram:42') for s in ('probnik','pricing','oemixibot')]
  self.assertEqual(len(set(ids)),3)
  for s,i in zip(('probnik','pricing','oemixibot'),ids):
   self.assertEqual(self.event(s,'telegram:42'),i)
  self.assertEqual(core.query(self.db,filters={})['total'],3)
 def test_conflicting_delivery_key_rejected(self):
  original=self.event('pricing','fixed')
  with self.assertRaises(ValueError):
   core.begin(self.db,source_bot='pricing',source_kind='private',oem='9999999',
              delivery_key='fixed',telegram_user_id=123,source_chat_id=456)
  with self.assertRaises(ValueError):
   core.begin(self.db,source_bot='pricing',source_kind='private',oem='3211070',
              delivery_key='fixed',telegram_user_id=777,source_chat_id=456)
  self.assertEqual(core.query(self.db,filters={})['total'],1)
 def test_batch_same_oem_distinct_keys(self):
  self.assertNotEqual(self.event('pricing','batch:1'),self.event('pricing','batch:2'))
 def test_currency_completion_and_conflicting_retry(self):
  a=self.event('probnik','one');b=self.event('oemixibot','two')
  self.assertTrue(core.complete(self.db,a,result_status='FOUND',price_status='FOUND',price=1200,currency='RUB'))
  self.assertTrue(core.complete(self.db,b,result_status='FOUND',price_status='FOUND',price=95.75,currency='USD'))
  self.assertTrue(core.complete(self.db,b,result_status='FOUND',price_status='FOUND',price=95.75,currency='USD'))
  self.assertFalse(core.complete(self.db,b,result_status='FOUND',price_status='FOUND',price=90,currency='USD'))
  self.assertFalse(core.complete(self.db,b,result_status='FOUND',price_status='FOUND',price=95,currency='EUR'))
 def test_exact_cart_order_only(self):
  a=self.event('pricing','a');b=self.event('pricing','b')
  self.assertTrue(core.link(self.db,a,cart=True,order_id='order-1'))
  self.assertFalse(core.link(self.db,a,order_id='other-order'))
  rows=core.query(self.db,filters={'source_bot':'pricing'})['items']
  self.assertEqual([r['order_id'] for r in rows], [None,'order-1'])
 def test_failure_nonblocking_and_no_shadow_db(self):
  self.assertIsNone(core.begin(str(Path(self.t.name)/'missing.sqlite'),source_bot='pricing',
                              source_kind='private',oem='1234567',delivery_key='k'))
  self.assertFalse((Path(self.t.name)/'missing.sqlite').exists())
 def test_admin_rbac_masked_pagination(self):
  self.event('probnik','p')
  app=FastAPI();actor={'allowed':False};audits=[]
  def auth():
   if not actor['allowed']:raise HTTPException(403,'forbidden')
   return 'admin'
  app.include_router(create_admin_router(canonical_db=self.db,require_admin=auth,
                            audit=lambda **kw:audits.append(kw)))
  with TestClient(app) as client:
   self.assertEqual(client.get('/admin/internal/unified-analytics/requests').status_code,403)
   actor['allowed']=True
   response=client.get('/admin/internal/unified-analytics/requests?limit=1')
   self.assertEqual(response.status_code,200)
   d=response.json();self.assertEqual(d['total'],1)
   self.assertTrue(d['items'][0]['telegram_user_id'].startswith('***'))
   self.assertEqual(len(audits),1)
   self.assertEqual(client.get('/admin/internal/unified-analytics/requests?limit=101').status_code,422)
if __name__=='__main__':unittest.main()
