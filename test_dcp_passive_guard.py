"""Passive DCP Parts guard tests for the current authorized-search contract.

Synthetic evidence only. No real browser, producer, cart or external HTTP.
"""
import json
import os
import subprocess
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
import time
import test_dcp_reliability as reliability
import oemixibot_dcp_price as dcp_browser

from dcp_authenticated_parts_search import extract_parts_rows, parts_dom_expression

HOST="https://www.dealercostparts.com"
ACCOUNT="dealer@example.invalid"

def evidence(*,status=200,authorized=True,challenged=False,account=ACCOUNT):
    return {"http_status":status,"authorized":authorized,"challenged":challenged,
            "dcp_dealer_account":account,"rows":[{"form_sku":"2670-265",
            "current_oem":"2670-265","price_selector_exact":True,
            "price_text":"$31.25","name":"Test part"}]}

def simulate(expression, *, href=HOST+"/account",body="Sign Out",links=None,
             account=ACCOUNT,reply_status=200,reply_body="Sign Out",
             reply_links=None,reply_account=ACCOUNT):
    """Execute actual current DOM guard JS in sandboxed Node VM."""
    payload={"expression":expression,"href":href,"body":body,
             "links":links if links is not None else ["/account/logoff"],
             "account":account,"reply_status":reply_status,"reply_body":reply_body,
             "reply_links":reply_links if reply_links is not None else ["/account/logoff"],
             "reply_account":reply_account}
    script=r"""
const vm=require('node:vm'),fs=require('node:fs');
const p=JSON.parse(fs.readFileSync(0,'utf8')); const calls=[];
const doc=(body,links,account)=>({
 body:{innerText:body,textContent:body},
 querySelectorAll:selector=>{
  if(selector==='#persistant-nav a[href]')return links.map(href=>({getAttribute:()=>href}));
  if(selector==='#persistant-nav .displayname')return account?[{textContent:account}]:[];
  if(selector==='form[id^="add_"]')return [];
  return [];
 }});
const context={location:new URL(p.href),document:doc(p.body,p.links,p.account),
  URL,URLSearchParams,DOMParser:class{parseFromString(){return doc(p.reply_body,p.reply_links,p.reply_account)}},
  fetch:async(url,options)=>{calls.push({url,method:options.method});return{
   status:p.reply_status,text:async()=>p.reply_body}}};
Promise.resolve(vm.runInNewContext(p.expression,context,{timeout:1500})).then(
value=>process.stdout.write(JSON.stringify({value,calls}))
).catch(err=>{process.stderr.write(String(err));process.exitCode=1});
"""
    run=subprocess.run(["node","-e",script],input=json.dumps(payload),text=True,
                       capture_output=True,timeout=6,check=True)
    return json.loads(run.stdout)

class PassiveGuardTests(unittest.TestCase):
    def setUp(self):
        context=patch.dict(os.environ,{"EXTREMIZER_DCP_EXPECTED_DEALER_ACCOUNT":ACCOUNT})
        context.start();self.addCleanup(context.stop)
    def test_http_429_and_5xx_are_not_not_found_or_price(self):
        for status in (429,500,502,503):
            value=extract_parts_rows("Arctic Cat","2670-265",evidence(status=status),origin="server_html")
            self.assertEqual(value.status,"TECHNICAL_ERROR")
            self.assertIsNone(value.dealer_price_usd)
    def test_parts_transport_error_never_starts_accessories_search(self):
        js=parts_dom_expression("arctic_cat","2670-265",live=False)
        out=simulate(js,reply_status=503)
        self.assertEqual(len(out["calls"]),1)
        self.assertTrue(out["value"]["transport_error"])
        self.assertNotIn("/oemcatalogs/",out["calls"][0]["url"])
    def test_tab_changed_to_foreign_origin_before_eval_has_zero_fetches(self):
        out=simulate(parts_dom_expression("arctic_cat","2670-265",live=False),
                     href="https://www.dealerorders.com/account")
        self.assertEqual(out["calls"],[])
        self.assertTrue(out["value"]["context_changed"])
    def test_challenge_or_auth_loss_before_eval_has_zero_fetches(self):
        for body in ("Verify you are human","Just a moment","Checking your browser"):
            out=simulate(parts_dom_expression("arctic_cat","2670-265",live=False),body=body)
            self.assertEqual(out["calls"],[])
            self.assertTrue(out["value"]["challenged"])
        for account,links in (("",["/account/logoff"]),(ACCOUNT,[])):
            out=simulate(parts_dom_expression("arctic_cat","2670-265",live=False),
                         account=account,links=links)
            self.assertEqual(out["calls"],[])
            self.assertFalse(out["value"]["authorized"])
    def test_valid_passive_document_allows_exactly_one_parts_fetch(self):
        out=simulate(parts_dom_expression("arctic_cat","2670-265",live=False))
        self.assertEqual(len(out["calls"]),1)
        self.assertEqual(out["calls"][0]["method"],"GET")
        self.assertEqual(out["value"]["dcp_dealer_account"],ACCOUNT)
    def test_accessories_challenge_before_eval_starts_zero_requests(self):
        out=simulate(parts_dom_expression("ski_doo","779132",live=False),
                     body="Verify you are human")
        self.assertEqual(out["calls"],[])
        self.assertTrue(out["value"]["challenged"])
    def test_challenge_appears_after_accessories_search_no_detail_fetch(self):
        out=simulate(parts_dom_expression("ski_doo","779132",live=False),
                     reply_body="Just a moment")
        self.assertEqual(len(out["calls"]),1)
        self.assertTrue(out["value"]["challenged"])
    def test_http_challenge_not_not_found(self):
        out=simulate(parts_dom_expression("arctic_cat","2670-265",live=False),
                     reply_status=403)
        self.assertEqual(len(out["calls"]),1)
        self.assertTrue(out["value"]["challenged"])
    def test_accessories_http_error_stops_before_detail_fetch(self):
        out=simulate(parts_dom_expression("ski_doo","779132",live=False),
                     reply_status=503)
        self.assertEqual(len(out["calls"]),1)
        self.assertTrue(out["value"]["transport_error"])
    def test_returned_page_identity_mismatch_fails_closed(self):
        out=simulate(parts_dom_expression("arctic_cat","2670-265",live=False),
                     reply_account="other@example.invalid")
        self.assertFalse(out["value"]["authorized"])
        self.assertEqual(len(out["calls"]),1)
    def test_matching_authenticated_search_only_after_explicit_identity(self):
        valid=extract_parts_rows("Arctic Cat","2670-265",evidence(),origin="server_html")
        self.assertEqual(valid.status,"FOUND")
        bad=extract_parts_rows("Arctic Cat","2670-265",evidence(account=None),origin="server_html")
        self.assertEqual(bad.status,"AUTH_REQUIRED")
        self.assertIsNone(bad.dealer_price_usd)

class DcpProducerPreflightContractTests(unittest.TestCase):
    """Exercise the ACTUAL dp_sync_agent._preflight_dcp with inert CDP fixtures."""

    def _run_actual_preflight(self, *, href):
        # Reuse the code-extraction helper that executes the real AST function.
        tab={"type":"page","url":HOST+"/account","title":"Account"}
        tabs=Mock(return_value=[tab])
        evaluate=Mock(return_value={
            "authorized":True,"challenged":False,
            **({"href":href} if href is not None else {}),
        })
        browser=SimpleNamespace(
            _tabs=tabs,
            _is_dcp_page=dcp_browser._is_dcp_page,
            _runtime_evaluate=evaluate,
        )
        ns=reliability.functions("dp_sync_agent.py",{"_preflight_dcp"},{
            "time":time,
            "oemixibot_dcp_price":browser,
            "_CF_MARKERS":("just a moment",),
            "_DCP_STATUS":"UNKNOWN",
            "_LAST_HEALTH_CHECK_MONO":0.0,
        })
        observed=[]
        def record(status,**kwargs):
            ns["_DCP_STATUS"]=status
            observed.append((status,kwargs.get("detail")))
        ns["_set_dcp_health"]=record
        result=ns["_preflight_dcp"]()
        tabs.assert_called_once_with()
        evaluate.assert_called_once()
        return result,observed,evaluate.call_args.args[1]

    def test_missing_current_href_is_technical_error_never_ready(self):
        result,observed,_=self._run_actual_preflight(href=None)
        self.assertEqual(result,"TECHNICAL_ERROR")
        self.assertEqual(observed,[("TECHNICAL_ERROR","dcp_document_context_changed")])
        self.assertNotIn("READY",[status for status,_ in observed])

    def test_foreign_origin_current_href_is_technical_error_never_ready(self):
        result,observed,_=self._run_actual_preflight(
            href="https://www.dealerorders.com/account")
        self.assertEqual(result,"TECHNICAL_ERROR")
        self.assertEqual(observed,[("TECHNICAL_ERROR","dcp_document_context_changed")])
        self.assertNotIn("READY",[status for status,_ in observed])

    def test_matching_ready_preflight_is_passive_no_fetch_or_navigation(self):
        result,observed,expression=self._run_actual_preflight(href=HOST+"/account")
        self.assertEqual(result,"READY")
        self.assertEqual(observed,[("READY","authorized_session_ready")])
        self.assertNotIn("fetch(",expression)
        self.assertNotIn("XMLHttpRequest",expression)
        self.assertNotIn("Page.navigate",expression)
        self.assertNotIn("window.open",expression)
        self.assertNotRegex(expression,
            r"location\\s*\\.\\s*(?:href\\s*=|assign\\s*\\(|replace\\s*\\(|reload\\s*\\()")
        self.assertNotRegex(expression,r"\\.reload\\s*\\(")


if __name__=="__main__":
    unittest.main()
