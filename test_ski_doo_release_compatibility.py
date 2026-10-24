"""Pinned release compatibility, entirely offline.

Only dcp_price_gateway differs in the deployed runtime slice. Load its old
classes from immutable release bases and verify the other participating
runtime modules byte-for-byte before exercising signed HTTP and the bot.
"""
import functools
from pathlib import Path
import subprocess
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

import test_ski_doo_gateway_bot_acceptance as acceptance

CORE_BASE = "ee4d22851036faed409f63ef405398f4fb3b1611"
OEMIXI_BASE = "d05827268039c40c7cb7f2f920c06c31a760c9f0"
ROOT = Path(__file__).resolve().parent
UNCHANGED = (
    "oemixibot_app.py", "oemixibot_catalog.py",
    "oemixi_secure_gateway_transport.py", "unified_price_contract_v1.py",
    "dp_live_bridge.py", "dp_result_completion.py",
    "polaris_dealer_cart_completion_contract.py",
)


@functools.lru_cache()
def source(ref, name):
    return subprocess.check_output(
        ["git", "show", ref + ":" + name], cwd=ROOT, text=True)


def load_module(label, text):
    module = ModuleType(label)
    module.__file__ = str(ROOT / (label + ".py"))
    with patch.dict(sys.modules, {label: module}):
        exec(compile(text, module.__file__, "exec"), module.__dict__)
    return module


class ReleaseCompatibilityTests(unittest.TestCase):
    setUp = acceptance.SkiDooBotAcceptance.setUp
    reply = acceptance.SkiDooBotAcceptance.reply
    assert_unavailable = acceptance.SkiDooBotAcceptance.assert_unavailable

    def versions(self):
        for name in UNCHANGED:
            current = (ROOT / name).read_text()
            self.assertEqual(source(CORE_BASE, name), current, name)
            self.assertEqual(source(OEMIXI_BASE, name), current, name)
        old_core = load_module(
            "release_old_core_gateway", source(CORE_BASE, "dcp_price_gateway.py"))
        old_consumer = load_module(
            "release_old_consumer_gateway", source(OEMIXI_BASE, "dcp_price_gateway.py"))
        with patch.dict(sys.modules, {"dcp_price_gateway": old_consumer}):
            old_transport = load_module(
                "release_old_consumer_transport",
                source(OEMIXI_BASE, "oemixi_secure_gateway_transport.py"))
        return old_core, old_consumer, old_transport

    def legacy_consumer(self):
        _, old_gateway, old_transport = self.versions()
        # The unmodified bot body imports these classes from its own release.
        return (
            patch.object(acceptance.bot, "SharedDealerPriceAdapter",
                         old_gateway.SharedDealerPriceAdapter),
            patch.object(acceptance, "OEMixiCanonicalSharedPriceAdapter",
                         old_transport.OEMixiCanonicalSharedPriceAdapter),
        )

    def use_old_core(self):
        old_core, _, _ = self.versions()
        self.core.side_effect = lambda m, o, **kw: old_core.request_price(
            self.path, m, o, live_request=self.live, **kw)

    def test_old_core_old_oemixi_preserves_price_unavailable(self):
        self.use_old_core()
        guard, adapter = self.legacy_consumer()
        with guard, adapter:
            self.assertIn("недоступна", self.assert_unavailable())
        self.assertEqual(self.core.call_count, 1)

    def test_new_core_old_oemixi_is_safe_but_does_not_claim_fixed_output(self):
        guard, adapter = self.legacy_consumer()
        with guard, adapter:
            self.assertIn("недоступна", self.assert_unavailable())
        # Core can serve the price, but the old consumer intentionally refuses it.
        self.live.assert_not_called()
        self.assertEqual(self.core.call_count, 1)

    def test_new_core_new_oemixi_delivers_price(self):
        self.versions()
        text, context = self.reply()
        self.assertIn("$207.07", text)
        self.assertEqual(context.user_data["offer:417300574"]["dl_usd"], 207.07)
        self.live.assert_not_called()
        self.assertEqual(self.core.call_count, 1)

    def test_old_core_new_oemixi_fails_closed_during_reverse_rollback(self):
        self.use_old_core()
        self.assertIn("недоступна", self.assert_unavailable())
        self.assertEqual(self.core.call_count, 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
