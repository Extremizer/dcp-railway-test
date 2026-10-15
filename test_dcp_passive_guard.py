"""Read-only browser simulations; execute the real JS with an inert Node fetch."""
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import unittest
from unittest.mock import Mock
import time

import test_dcp_reliability as reliability

HOST = 'https://www.dealercostparts.com'


class PassiveGuardTests(unittest.TestCase):
    def setUp(self):
        self.fx = reliability.BrowserTests(methodName='runTest')
        self.fx.setUp()
        self.addCleanup(self.fx.tearDown)
        self.ns = self.fx.producer

    def lookup(self, manufacturer='Arctic Cat', oem='2670-265'):
        return self.ns['get_dealer_price'](manufacturer, oem)

    def test_http_429_and_5xx_are_not_not_found_or_price(self):
        for code in (429, 500, 502, 503):
            for found in (False, True):
                self.ns['_runtime_evaluate'].return_value = {
                    'http_status': code, 'authorized': True, 'found': found,
                    'dealer_price_text': '$31.25', 'matched_sku': '2670-265'}
                r = self.lookup()
                self.assertEqual(r.status, 'TECHNICAL_ERROR')
                self.assertIsNone(r.dealer_price_usd)

    def test_parts_transport_error_never_starts_accessories_search(self):
        self.ns['_parts_slug'] = lambda m: ('Ski-Doo', 'ski_doo')
        self.ns['_runtime_evaluate'].side_effect = [
            {'http_status': 503, 'authorized': True, 'found': False},
            {'http_status': 200, 'authorized': True, 'found': True,
             'matched_sku': '779132', 'dealer_price_text': '$31.25'}]
        self.assertEqual(self.lookup('Ski-Doo', '779132').status, 'TECHNICAL_ERROR')
        self.assertEqual(self.ns['_runtime_evaluate'].call_count, 1)

    def javascript(self, expression, *, href=HOST+'/account', body='Sign Out', responses=None):
        # No browser, sockets or live HTTP; all responses are supplied locally.
        payload = {'expression': expression, 'href': href, 'body': body,
                   'responses': responses or [{'status': 200, 'body': 'Sign Out'}]}
        script = r'''
const vm=require('node:vm'), fs=require('node:fs');
const p=JSON.parse(fs.readFileSync(0,'utf8')), calls=[];
const doc=(body,links=[])=>({body:{innerText:body},querySelector:()=>null,
  querySelectorAll:selector=>selector.includes('#CategoryItems')?
    links.map(href=>({getAttribute:()=>href})):[]});
let responseIndex=0;
const context={location:new URL(p.href),document:doc(p.body),URL,URLSearchParams,
  DOMParser:class {parseFromString(html){const r=p.responses[responseIndex-1];return doc(html,r.links)}},
  fetch:async(url,options)=>{calls.push({url,method:options?.method||'GET'});
    const r=p.responses[responseIndex++];if(!r)throw Error('unexpected fetch');
    if(r.change_body!==undefined) context.document=doc(r.change_body);
    return {status:r.status,url:new URL(url,p.href).href,text:async()=>r.body};}};
Promise.resolve(vm.runInNewContext(p.expression,context,{timeout:1000}))
 .then(value=>process.stdout.write(JSON.stringify({value,calls})))
 .catch(e=>{process.stderr.write(String(e));process.exitCode=1});
'''
        r = subprocess.run(['node', '-e', script], input=json.dumps(payload),
                           text=True, capture_output=True, timeout=5, check=True)
        return json.loads(r.stdout)

    def accessories_expression(self):
        self.ns['_parts_slug'] = lambda m: ('Ski-Doo', 'ski_doo')
        self.ns['_runtime_evaluate'].side_effect = [
            {'http_status':200,'authorized':True,'found':False},
            {'http_status':403,'authorized':False,'challenged':True}]
        self.lookup('Ski-Doo', '779132')
        return self.ns['_runtime_evaluate'].call_args.args[1]

    def capture_expression(self):
        self.lookup()
        return self.ns['_runtime_evaluate'].call_args.args[1]

    def test_tab_changed_to_foreign_origin_before_eval_has_zero_fetches(self):
        out = self.javascript(self.capture_expression(), href='https://www.dealerorders.com/account')
        self.assertEqual(out['calls'], [])
        self.assertTrue(out['value'].get('context_changed'))

    def test_challenge_or_auth_loss_before_eval_has_zero_fetches(self):
        for body in ('Verify you are human', 'Выполнение проверки безопасности', 'Please sign in'):
            out = self.javascript(self.capture_expression(), body=body)
            self.assertEqual(out['calls'], [])
            self.assertFalse(out['value'].get('found', False))

    def test_valid_passive_document_allows_exactly_one_parts_fetch(self):
        out = self.javascript(self.capture_expression())
        self.assertEqual(len(out['calls']), 1)
        self.assertEqual(out['calls'][0]['method'], 'GET')

    def test_accessories_challenge_before_eval_starts_zero_requests(self):
        out = self.javascript(self.accessories_expression(), body='Verify you are human')
        self.assertEqual(out['calls'], [])
        self.assertTrue(out['value']['challenged'])

    def test_challenge_appears_after_accessories_search_no_detail_fetch(self):
        out = self.javascript(self.accessories_expression(), responses=[{
            'status':200,'body':'Sign Out','links':['/oemcatalogs/p/fake-sku'],
            'change_body':'Verify you are human'}])
        self.assertEqual(len(out['calls']), 1)
        self.assertTrue(out['value']['challenged'])

    def test_http_challenge_not_not_found(self):
        out = self.javascript(self.capture_expression(), responses=[{
            'status':403,'body':'Verify you are human'}])
        self.assertEqual(len(out['calls']), 1)
        self.assertTrue(out['value']['challenged'])
        self.assertFalse(out['value']['found'])

    def test_preflight_missing_current_href_cannot_publish_ready(self):
        self.check_preflight({'authorized':True,'challenged':False}, 'TECHNICAL_ERROR')

    def test_accessories_http_error_stops_before_detail_fetch(self):
        self.ns['_parts_slug'] = lambda m: ('Ski-Doo', 'ski_doo')
        self.ns['_runtime_evaluate'].side_effect = [
            {'http_status': 200, 'authorized': True, 'found': False},
            {'http_status': 503, 'authorized': True, 'found': False}]
        self.assertEqual(self.lookup('Ski-Doo', '779132').status, 'TECHNICAL_ERROR')
        expression = self.ns['_runtime_evaluate'].call_args.args[1]
        out = self.javascript(expression, responses=[{'status':503,'body':'Sign Out'}])
        self.assertEqual(len(out['calls']), 1)
        self.assertTrue(out['value'].get('transport_error'))

    def test_health_dom_from_changed_origin_cannot_publish_ready(self):
        self.check_preflight({'authorized':True,'challenged':False,
                            'href':'https://www.dealerorders.com/account'}, 'TECHNICAL_ERROR')

    def test_manual_ready_detection_is_passive(self):
        ns, helper = self.check_preflight({'authorized':True,'challenged':False,
                                           'href':HOST+'/account'}, 'READY')
        expression = helper._runtime_evaluate.call_args.args[1]
        self.assertNotIn('fetch(', expression)
        self.assertNotRegex(expression, r'location\.(href|assign|replace)\s*[=(]|Page\.navigate|\.reload\(')

    def check_preflight(self, probe, expected):
        helper = SimpleNamespace(_is_dcp_page=self.ns['_is_dcp_page'],
            _tabs=Mock(return_value=[{'type':'page','url':HOST+'/account','title':'Account'}]),
            _runtime_evaluate=Mock(return_value=probe))
        ns = reliability.functions('dp_sync_agent.py', {'_preflight_dcp'}, {
            'time':time,'oemixibot_dcp_price':helper,'_CF_MARKERS':('just a moment',),
            '_DCP_STATUS':'UNKNOWN'})
        def observe(status, **kw): ns['_DCP_STATUS'] = status
        ns['_set_dcp_health'] = observe
        self.assertEqual(ns['_preflight_dcp'](), expected)
        return ns, helper


if __name__ == '__main__': unittest.main()
