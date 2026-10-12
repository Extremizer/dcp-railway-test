"""Real reference/cache/card functions on disposable DBs; no network or bots."""
import ast
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import re
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import client_offer_metadata as metadata
import oem_reference_service
import test_client_offer_card as card_tests
from test_client_offer_card import load_functions
from _prod_finance_switch_regression import require_web_admin_unchanged


class OfferMetadataTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.reference = self.root / "reference.db"
        with sqlite3.connect(self.reference) as c:
            c.execute("""CREATE TABLE oem_reference(
                oem TEXT PRIMARY KEY,item_type TEXT,actual_weight_kg REAL,volume_weight_kg REAL,
                weight_state TEXT,needs_review INTEGER,type_conflict INTEGER,
                actual_weight_conflict INTEGER,volume_weight_conflict INTEGER,
                direct_observation_count INTEGER,linked_group_count INTEGER)""")
            c.executemany("INSERT INTO oem_reference VALUES(?,?,?,?,?,?,?,?,?,?,?)", [
                ("2670-265", "part", .13, None, "ACTUAL_ONLY", 0, 0, 0, 0, 3, 0),
                ("CONFLICT", "part", .13, None, "ACTUAL_ONLY", 1, 0, 1, 0, 2, 0),
                ("BOTH", "part", .01, .231, "BOTH", 0, 0, 0, 0, 1, 0),
            ])
        self.ref_patch = patch.object(oem_reference_service, "DB_FILE", self.reference)
        self.ref_patch.start(); self.addCleanup(self.ref_patch.stop)
        self.orders = self.root / "orders.db"
        with sqlite3.connect(self.orders) as c:
            c.execute("""CREATE TABLE oem_catalog_cache(manufacturer TEXT,current_oem TEXT,
                      msrp_usd REAL,msrp_verified INTEGER,last_verified_at TEXT)""")

    def seed_msrp(self, value=50.95, *, manufacturer="Arctic Cat", verified=1, age=0):
        now = (datetime.now(timezone.utc) - timedelta(hours=age)).isoformat()
        with sqlite3.connect(self.orders) as c:
            c.execute("INSERT INTO oem_catalog_cache VALUES(?,?,?,?,?)",
                      (manufacturer, "2670-265", value, verified, now))

    def test_weight_read_only_and_client_rounding(self):
        before = hashlib.sha256(self.reference.read_bytes()).hexdigest()
        lines = metadata.reference_weight_lines("2670-265")
        self.assertEqual(lines, ["Справочный вес за 1 шт. — <b>0,13 кг</b>",
                               "Расчётный вес для доставки за 1 шт. — <b>0,2 кг</b>"])
        self.assertEqual(hashlib.sha256(self.reference.read_bytes()).hexdigest(), before)

    def test_conflicts_and_missing_weights_hidden_without_seed(self):
        self.assertEqual(metadata.reference_weight_lines("CONFLICT"), [])
        self.assertEqual(metadata.reference_weight_lines("UNKNOWN"), [])
        missing = self.root / "missing.db"
        with patch.object(oem_reference_service, "DB_FILE", missing):
            self.assertEqual(metadata.reference_weight_lines("2670-265"), [])
        self.assertFalse(missing.exists())

    def test_current_oem_falls_back_to_requested_and_volume_uses_maximum(self):
        self.assertIn("0,13 кг", metadata.reference_weight_lines("NEW", "2670-265")[0])
        self.assertIn("0,3 кг", metadata.reference_weight_lines("BOTH")[-1])

    def test_only_fresh_verified_manufacturer_msrp_is_read(self):
        for kwargs in ({"verified": 0}, {"age": 169}, {"manufacturer": "Yamaha"}):
            self.seed_msrp(**kwargs)
        self.assertIsNone(metadata.cached_rrp_rub(self.orders, "Arctic Cat", ["2670-265"], 105))
        self.seed_msrp()
        before = hashlib.sha256(self.orders.read_bytes()).hexdigest()
        self.assertEqual(metadata.cached_rrp_rub(self.orders, "Arctic Cat", ["2670-265"], 105), 5350)
        self.assertEqual(hashlib.sha256(self.orders.read_bytes()).hexdigest(), before)

    def test_invalid_or_private_price_never_becomes_msrp(self):
        for value in (None, 0, -1, True, float("nan"), float("inf"), "50.95"):
            self.assertIsNone(metadata.verified_msrp(value, "dcp_public_form_metadata"))
        self.assertIsNone(metadata.verified_msrp(30.05, "server_html:.c2 .dbl"))
        self.assertEqual(metadata.verified_msrp(50.95, "dcp_public_form_metadata"), 50.95)

    def test_both_real_cards_use_cached_msrp_and_reference_weight(self):
        self.seed_msrp()
        cards = card_tests.ClientOfferCardTests(); cards.setUp()
        cards.probnik.update(ORDERS_DB=self.orders, USD_RUB_RATE=105.0)
        cards.procenka.update(ORDERS_DB_FILE=self.orders, USD_RUB_RATE=105.0,
                             load_usd_rub_rate=lambda: 105.0)
        info = {"manufacturer": "Arctic Cat", "customer_rub": 4300, "rrp_rub": None}
        result = {"status": "FOUND", "oem": "2670-265", "manufacturer": "Arctic Cat",
                  "_dealer_price_usd": 30.05, "price": None}
        a = cards.probnik["_compose"]("2670-265", info, cards.rows)
        b = cards.procenka["format_client_offer_card"](result)
        self.assertEqual(a, b)
        for text in ("4 300* ₽", "РРЦ — <b>5 350 ₽</b>", "19.6%", "0,13 кг", "0,2 кг"):
            self.assertIn(text, a)

    def test_msrp_conversion_does_not_diverge_at_rounding_boundary(self):
        self.assertEqual(metadata.msrp_rub(50.96, 105), 5400)
        self.assertIsNone(metadata.msrp_rub(float("nan"), 105))


class AgentMetadataTests(unittest.TestCase):
    def test_exact_row_msrp_is_separate_from_dealer_price(self):
        funcs = load_functions("oemixibot_dcp_price.py", ["_money", "_msrp_kwargs", "_public_msrp_fields"],
                               {"re": re})
        self.assertEqual(funcs["_msrp_kwargs"]({"dealer_price_text": "$30.05"}),
                         {"msrp_usd": None, "msrp_source": None})
        self.assertEqual(funcs["_msrp_kwargs"]({"msrp_text": "$50.95",
                         "msrp_source": "dcp_public_form_metadata"})["msrp_usd"], 50.95)
        js = funcs["_public_msrp_fields"]("form")
        self.assertNotIn(".dbl", js)
        self.assertNotIn(".price", js)

    def test_agent_forwards_optional_msrp_without_changing_dp(self):
        sent = []
        funcs = load_functions("dp_sync_agent.py", ["_finish"],
                               {"_request": lambda *a, **k: sent.append(k["payload"])})
        funcs["_finish"]("synthetic-request", {"status": "FOUND", "dealer_price_usd": 30.05,
                         "msrp_usd": 50.95, "msrp_source": "dcp_public_form_metadata"})
        self.assertEqual(sent[0]["dealer_price_usd"], 30.05)
        self.assertEqual(sent[0]["msrp_usd"], 50.95)
        self.assertEqual(sent[0]["msrp_source"], "dcp_public_form_metadata")

    def test_web_admin_scope_guard_preserves_auth_and_business_routes(self):
        source = Path(__file__).with_name("web_app.py").read_text()
        require_web_admin_unchanged(source)
        changed = source.replace('"/admin/orders/{order_id}/prepare-apply"', '"/unprotected"', 1)
        self.assertNotEqual(changed, source)
        with self.assertRaisesRegex(AssertionError, "outside DP result"):
            require_web_admin_unchanged(changed)


if __name__ == "__main__":
    unittest.main()
