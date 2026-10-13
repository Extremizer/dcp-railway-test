"""CONTRACT v1.4 dealer USD debt regression on disposable SQLite."""
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest

from oemixibot_finance import FinanceEngine
from oemixibot_store import OemixiStore


class DealerDebtV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db=str(Path(self.tmp.name)/"dealer.db")
        self.store=OemixiStore(self.db)
        self.store.init()
        self.store.add_dealer("d",101,"Dealer",coefficient=1)
        self.finance=FinanceEngine(self.db)

    def add_order(self, oid, amount):
        self.store.cart_add("d",oid+"-OEM",float(amount),qty=1)
        return self.store.checkout_cart("d",oid)

    def topup(self, amount, pid):
        return self.finance.payment(
            buyer_type="dealer",buyer_id="d",currency="USD",amount=amount,
            payment_id=pid,actor="admin",reason="received",
            payment_method="usd",source_amount=amount,source_currency="USD"
        )

    def test_order_at_zero_balance_is_charged_once_and_goes_negative(self):
        self.assertEqual(self.add_order("E01A-0001",60),60)
        self.assertEqual(self.finance.balance("d"),Decimal("-60.00"))
        charges=[x for x in self.finance.history("d") if x["event_type"]=="ORDER_CHARGE"]
        self.assertEqual(len(charges),1)
        self.assertFalse(self.store.confirm_order_and_charge("E01A-0001"))
        self.assertEqual(self.finance.balance("d"),Decimal("-60.00"))

    def test_multiple_orders_accumulate_one_shared_obligation_without_recharge(self):
        self.add_order("E01A-0001",60)
        self.add_order("E01A-0002",40)
        self.assertEqual(self.finance.balance("d"),Decimal("-100.00"))
        charges=[x for x in self.finance.history("d") if x["event_type"]=="ORDER_CHARGE"]
        self.assertEqual({x["order_id"] for x in charges},{"E01A-0001","E01A-0002"})
        self.assertEqual(len(charges),2)

    def test_dcp_gate_stays_closed_until_aggregate_debt_is_repaid(self):
        self.add_order("E01A-0001",60)
        self.add_order("E01A-0002",40)
        gate=self.finance.dcp_procurement_ready("d")
        self.assertFalse(gate["ready"])
        self.assertEqual(gate["balance"],Decimal("-100.00"))

        self.topup(99,"p1")
        self.assertFalse(self.finance.dcp_procurement_ready("d")["ready"])
        self.topup(1,"p2")
        self.assertTrue(self.finance.dcp_procurement_ready("d")["ready"])
        self.assertEqual(self.finance.balance("d"),Decimal("0.00"))

    def test_positive_balance_after_repayment_is_ready(self):
        self.add_order("E01A-0001",60)
        self.topup(75,"p1")
        gate=self.finance.dcp_procurement_ready("d")
        self.assertTrue(gate["ready"])
        self.assertEqual(gate["balance"],Decimal("15.00"))

    def test_other_debits_do_not_get_hidden_credit(self):
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_AVAILABLE"):
            self.finance.adjustment_minus(
                buyer_type="dealer",buyer_id="d",currency="USD",amount=1,
                adjustment_id="a",actor="admin",reason="manual"
            )
        with self.store.db() as c:
            c.execute("INSERT INTO usa_shipments(code,delivery_method,created_at) VALUES('S','x','t')")
            c.execute("INSERT INTO dealer_arrivals(shipment_id,dealer_id,created_at) VALUES(1,'d','t')")
        with self.assertRaisesRegex(ValueError,"INSUFFICIENT_AVAILABLE"):
            self.finance.delivery_charge(
                buyer_type="dealer",buyer_id="d",currency="USD",amount=1,
                arrival_id=1,actor="admin",description="delivery"
            )

    def test_inactive_dealer_still_cannot_order_charge(self):
        with self.store.db() as c:
            c.execute("UPDATE dealers SET active=0 WHERE id='d'")
        with self.assertRaisesRegex(ValueError,"inactive dealer"):
            self.finance.post_event(
                buyer_type="dealer",buyer_id="d",currency="USD",
                event_type="ORDER_CHARGE",amount=-10,description="order",
                reference_type="order",reference_id="E01A-0001",
                order_id="E01A-0001",actor="system",
                idempotency_key="ORDER_CHARGE:order:E01A-0001"
            )


if __name__=="__main__":
    unittest.main()
