"""Runtime-level v1.4 regression; disposable DB, no network or polling."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from oemixibot_finance import FinanceEngine
from oemixibot_store import OemixiStore


class OemixiDebtRuntimeV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store=OemixiStore(str(Path(self.tmp.name)/"runtime.db"))
        self.store.init()
        self.store.add_dealer("d",101,"Dealer",coefficient=1)
        self.finance=FinanceEngine(self.store.path)

    def test_checkout_does_not_require_prepaid_balance(self):
        self.store.cart_add("d","TEST",60,qty=1)
        total=self.store.checkout_cart("d","E01A-0001")
        self.assertEqual(total,60)
        self.assertEqual(self.finance.balance("d"),Decimal("-60.00"))
        self.assertEqual(self.store.cart_view("d")["items"],[])

    def test_topup_reduces_debt_without_second_order_charge(self):
        self.store.cart_add("d","TEST",60,qty=1)
        self.store.checkout_cart("d","E01A-0001")
        self.finance.payment(
            buyer_type="dealer",buyer_id="d",currency="USD",amount=60,
            payment_id="p1",actor="admin",reason="received",
            payment_method="usd",source_amount=60,source_currency="USD"
        )
        self.assertEqual(self.finance.balance("d"),Decimal("0.00"))
        self.assertTrue(self.finance.dcp_procurement_ready("d")["ready"])
        charges=[x for x in self.finance.history("d") if x["event_type"]=="ORDER_CHARGE"]
        self.assertEqual(len(charges),1)


if __name__=="__main__":
    unittest.main()
