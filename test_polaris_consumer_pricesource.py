import ast
import sqlite3
import types
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest.mock import patch

from test_polaris_dp_e2e_integration import PolarisDealerPriceE2EIntegrationTests
import dcp_price_gateway


def _client_cache_reader(db):
    source=Path("extremizer_bot.py").read_text(encoding="utf-8")
    funcs={n.name:n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef)}
    tree=ast.fix_missing_locations(ast.Module(body=[
        funcs["_dealer_price_cache_key"],
        funcs["get_dealer_price_cache"],
    ],type_ignores=[]))
    ns={
        "finder":types.SimpleNamespace(
            manufacturer_alias=lambda x:x,
            normalize_oem=lambda x:x.strip().upper(),
        ),
        "init_orders_db":lambda:None,
        "ORDERS_DB_FILE":db,
        "DP_CACHE_MAX_AGE_HOURS":24.0,
        "sqlite3":sqlite3,
        "datetime":datetime,
    }
    exec(compile(tree,"extremizer_bot.py","exec"),ns)
    return ns["get_dealer_price_cache"]


class PolarisConsumerPriceSourceTests(PolarisDealerPriceE2EIntegrationTests):
    def _seed_verified(self):
        self.test_01_alias_producer_protocol_fence_completion_cache_pricesource_consumers()

    def test_client_cache_reads_requested_and_resolved(self):
        self._seed_verified()
        reader=_client_cache_reader(self.db)
        requested=reader("Polaris","2205250")
        resolved=reader("Polaris","2206328")
        self.assertEqual(requested["dealer_price_usd"],184.07)
        self.assertEqual(resolved["dealer_price_usd"],184.07)
        self.assertTrue(requested["fresh"])
        self.assertTrue(resolved["fresh"])

    def test_client_cache_rejects_msrp(self):
        self._seed_verified()
        reader=_client_cache_reader(self.db)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE dealer_price_cache SET source='msrp'")
        self.assertIsNone(reader("Polaris","2206328"))

    def test_client_cache_rejects_stale_25h(self):
        self._seed_verified()
        stale=(datetime.now(timezone.utc)-timedelta(hours=25)).isoformat(timespec="seconds")
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE dealer_price_cache SET last_verified_at=?",(stale,))
        self.assertIsNone(_client_cache_reader(self.db)("Polaris","2206328"))

    def test_oemixi_catalog_uses_common_verified_cache(self):
        self._seed_verified()
        import oemixibot_catalog as catalog
        gateway=lambda manufacturer,oem,max_age_hours: dcp_price_gateway.request_price(
            self.db,
            manufacturer,
            oem,
            max_age_hours=max_age_hours,
            live_request=lambda *a,**k: {"status":"TECHNICAL_ERROR"},
        )
        adapter=dcp_price_gateway.SharedDealerPriceAdapter(gateway,max_age_hours=24)
        with patch.object(
            catalog,
            "resolve_identity",
            return_value={"oem":"2205250","manufacturer":"Polaris","item_type":"part"},
        ), patch.object(
            catalog.oem_reference_service,
            "lookup_oem",
            return_value={},
        ), patch.object(catalog,"confirm_identity"):
            offer=catalog.resolve_offer("2205250",manufacturer="Polaris",price_gateway=adapter)
        self.assertEqual(offer["dl_usd"],184.07)
        self.assertEqual(offer["requested_oem"],"2205250")
        self.assertEqual(offer["oem"],"2206328")


if __name__=="__main__":
    unittest.main()
