"""Independent Retail parsing/transport/agent regressions. No external network."""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import oemixibot_dcp_price as browser
import wops_retail as retail
from test_client_offer_card import load_functions

AC_HTML = """<title>FILTER,AIR (TURBO) AC2670-265</title>
<div>Part no 2670-265</div><div>Name: FILTER,AIR (TURBO)</div>
<div>Supplier: ARCTIC CAT<span>Part Number: 2670-265</span></div>
<div>Available in: <i>7-10 days</i></div><div>Retail: <b>$50.95</b></div>"""
HO_HTML = """<div>Part no 91303-PA9-004</div><div>Name: O-RING (100X2.2)</div>
<div>Supplier: HONDA<span>Part Number: 91303-PA9-004</span></div><div>Retail: $10.38</div>"""


class PublicRetailTests(unittest.TestCase):
    def test_exact_public_retail_is_separate_from_cost_and_delivery(self):
        quote = retail.parse_retail(AC_HTML + "<p>Cost: $32.22</p>", "Arctic Cat", "2670-265")
        self.assertEqual(quote["retail_usd"], 50.95)
        self.assertEqual(quote["source"], "dealerorders_public_retail")
        self.assertEqual(quote["supplier_sku"], "AC2670-265")
        self.assertNotIn("dealer_price_usd", quote)
        self.assertNotIn("stock", quote)
        self.assertEqual(retail.parse_retail(HO_HTML, "Honda", "91303-PA9-004")["retail_usd"], 10.38)

    def test_wrong_manufacturer_oem_challenge_or_ambiguous_price_rejected(self):
        for html in (AC_HTML.replace("ARCTIC CAT", "HONDA"),
                     AC_HTML.replace("2670-265", "2670-266"),
                     AC_HTML + "<p>Retail: $60.00</p>",
                     AC_HTML + "<p>Just a moment Cloudflare</p>",
                     AC_HTML.replace("Retail:", "Cost:"),
                     AC_HTML.replace("$50.95", "$0.00"),
                     AC_HTML.replace("$50.95", "$-50.95"),
                     AC_HTML.replace("$50.95", "$50.955"),
                     AC_HTML.replace("$50.95", "$50.95e3"), ""):
            with self.subTest(html=html):
                self.assertIsNone(retail.parse_retail(html, "Arctic Cat", "2670-265"))

    def test_hidden_script_prices_are_ignored_and_unverified_brands_not_guessed(self):
        quote = retail.parse_retail(AC_HTML + "<script>Retail: $999.00</script>", "Arctic Cat", "2670-265")
        self.assertEqual(quote["retail_usd"], 50.95)
        for manufacturer, oem in (("BRP", "417300574"), ("Ski-Doo", "417300574"),
                                  ("Arctic Cat", "2670-265&mv_action=add")):
            self.assertIsNone(retail.identity(manufacturer, oem))

    def test_existing_wops_tab_only_and_get_only_no_browser_or_cart_mutations(self):
        dcp = {"type": "page", "url": "https://www.dealercostparts.com/test", "id": "dcp"}
        wops = {"type": "page", "url": retail.DETAIL_URL + "AC2670-265", "id": "wops"}
        with patch.object(browser, "_tabs", return_value=[dcp, wops]), \
             patch.object(browser, "_runtime_evaluate", return_value=AC_HTML) as evaluate:
            self.assertEqual(retail.get_public_retail("Arctic Cat", "2670-265")["retail_usd"], 50.95)
        tab, expression = evaluate.call_args.args
        self.assertEqual(tab, wops)
        self.assertEqual(evaluate.call_args.kwargs["timeout_seconds"], 4)
        self.assertIn("method:'GET'", expression)
        self.assertIn("2500", expression)
        self.assertIn(retail.DETAIL_URL + "AC2670-265", expression)
        for forbidden in ("POST", "window.open", "location.href=", ".click(", ".submit(",
                          "document.cookie", "mv_session_id", "xpress.html", "process.html"):
            self.assertNotIn(forbidden, expression)

    def test_missing_wops_tab_or_browser_failure_returns_no_retail(self):
        for tabs in ([], [{"type": "page", "url": "https://www.dealerorders.com.evil.test/"}]):
            with patch.object(browser, "_tabs", return_value=tabs), patch.object(browser, "_runtime_evaluate") as ev:
                self.assertIsNone(retail.get_public_retail("Arctic Cat", "2670-265"))
                ev.assert_not_called()
        with patch.object(browser, "_tabs", side_effect=TimeoutError):
            self.assertIsNone(retail.get_public_retail("Arctic Cat", "2670-265"))


class IndependentAgentTests(unittest.TestCase):
    def agent(self, requester):
        return load_functions("dp_sync_agent.py", ["run_retail_once"],
                              {"_request": requester, "time": SimpleNamespace(monotonic=lambda: 1000),
                               "_LAST_RETAIL_POLL_MONO": 0, "_RETAIL_RETRY_AT": {}})

    def test_cache_hit_enrichment_does_not_request_dcp_or_touch_its_health(self):
        calls = []
        quote = retail.parse_retail(AC_HTML, "Arctic Cat", "2670-265")
        def request(path, **kwargs):
            calls.append((path, kwargs))
            return {"candidates": [{"manufacturer": "Arctic Cat", "oem": "2670-265"}]}
        agent = self.agent(request)
        with patch.object(retail, "get_public_retail", return_value=quote):
            self.assertTrue(agent["run_retail_once"]())
            self.assertFalse(agent["run_retail_once"]())
        self.assertEqual([c[0] for c in calls], ["/internal/retail-sync/next", "/internal/retail-sync/result"])
        self.assertEqual(calls[1][1]["payload"]["retail_usd"], 50.95)
        self.assertNotIn("dealer_price_usd", calls[1][1]["payload"])

    def test_negative_retail_does_not_poison_shared_cache_or_starve_next_oem(self):
        calls = []
        def request(path, **kwargs):
            calls.append(path)
            return {"candidates": [{"manufacturer": "Arctic Cat", "oem": "2670-265"},
                                   {"manufacturer": "Honda", "oem": "91303-PA9-004"}]}
        agent = self.agent(request)
        with patch.object(retail, "get_public_retail", return_value=None) as get:
            self.assertFalse(agent["run_retail_once"]())
            agent["_LAST_RETAIL_POLL_MONO"] = 0
            self.assertFalse(agent["run_retail_once"]())
        self.assertEqual([c.args for c in get.call_args_list], [("Arctic Cat", "2670-265"), ("Honda", "91303-PA9-004")])
        self.assertEqual(calls, ["/internal/retail-sync/next"] * 2)

    def test_old_server_or_wops_failure_cannot_set_dcp_health(self):
        agent = self.agent(lambda *a, **k: (_ for _ in ()).throw(RuntimeError("old server")))
        self.assertFalse(agent["run_retail_once"]())

    def test_foreign_wops_cloudflare_tab_does_not_block_authorized_dcp(self):
        statuses = []
        fake = SimpleNamespace(DCP_HOST="www.dealercostparts.com", _tabs=lambda: [
            {"type": "page", "url": "https://www.dealerorders.com/", "title": "Just a moment"},
            {"type": "page", "url": "https://www.dealercostparts.com/", "title": "DCP"}],
            _runtime_evaluate=lambda *args: {"authorized": True})
        functions = load_functions("dp_sync_agent.py", ["_preflight_dcp"],
            {"time": SimpleNamespace(monotonic=lambda: 1), "oemixibot_dcp_price": fake,
             "_CF_MARKERS": ("just a moment",), "_DCP_STATUS": "READY",
             "_set_dcp_health": lambda status, **kwargs: statuses.append(status)})
        self.assertEqual(functions["_preflight_dcp"](), "READY")
        self.assertEqual(statuses, ["READY"])


if __name__ == "__main__":
    unittest.main()
