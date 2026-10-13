"""CONTRACT v1.4 P0 regression: safe client payment routing."""
import sqlite3
import tempfile
import unittest
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path

from _client_checkout_callback_adapter_regression import (
    compile_save_order_to_history,
    create_schema,
)
from client_checkout_orchestrator import persist_client_checkout_atomic
from client_finance_adapter import ClientFinanceAdapter
from client_order_finance_bridge import post_persisted_order_charge
from common_finance_contract import (
    FulfillmentRoute,
    PaymentRoute,
    default_client_payment_route,
    resolve_internal_client_payment_route,
)
from oemixibot_finance import FinanceEngine


class User:
    id = 991401
    full_name = "Finance P0"
    username = "finance_p0"


class InternalPaymentRoutingV14Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "orders.db"
        create_schema(self.db)
        namespace = {
            "sqlite3": sqlite3,
            "datetime": datetime,
            "nullcontext": nullcontext,
            "PaymentRoute": PaymentRoute,
            "FulfillmentRoute": FulfillmentRoute,
            "default_client_payment_route": default_client_payment_route,
            "ORDERS_DB_FILE": self.db,
            "USD_RUB_RATE": 100.0,
            "PRICE_COEFFICIENT": 1.0,
            "ITEM_TYPE_ALLOWED_TARIFFS": {
                "part": ("comfort", "economy"),
                "accessory": ("comfort", "economy"),
                "gear": ("economy", "mix"),
            },
            "init_orders_db": lambda: None,
            "customer_rub_price_from_dp": (
                lambda dp, coefficient, rate:
                None if dp is None else int(round(float(dp) * float(coefficient) * float(rate)))
            ),
            "get_oem_reference": lambda current_oem, requested_oem: None,
            "infer_item_type_from_dcp_catalog": lambda catalog: None,
        }
        exec(compile_save_order_to_history(), namespace)
        self.save_order = namespace["save_order_to_history"]
        self.adapter = ClientFinanceAdapter(FinanceEngine(str(self.db)))

    def warehouse_item(self, oem, price=12000, warehouse_id=1, payment_route=None):
        item = {
            "manufacturer": "BRP",
            "oem": oem,
            "requested_oem": oem,
            "name": "RF part",
            "qty": 1,
            "price_snapshot_rub": price,
            "offer_source": "warehouse",
            "warehouse_id": warehouse_id,
            "warehouse_public_name": f"Склад {warehouse_id}",
        }
        if payment_route is not None:
            item["payment_route"] = (
                payment_route.value if isinstance(payment_route, PaymentRoute) else payment_route
            )
        return item

    def usa_item(self, oem, dealer_usd=50):
        return {
            "manufacturer": "BRP",
            "oem": oem,
            "requested_oem": oem,
            "name": "USA part",
            "qty": 1,
            "_dealer_price_usd": dealer_usd,
            "offer_source": "usa",
        }

    def persist(self, order_id, cart):
        return persist_client_checkout_atomic(
            db_path=self.db,
            save_order_fn=self.save_order,
            finance_adapter=self.adapter,
            order_id=order_id,
            user=User(),
            cart=cart,
            origin="web",
            actor="routing-regression",
        )

    def routes(self, order_id):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                "SELECT offer_source,payment_route FROM order_items "
                "WHERE order_id=? ORDER BY position",
                (order_id,),
            ).fetchall()

    def ledger(self, order_id):
        with sqlite3.connect(self.db) as c:
            return c.execute(
                "SELECT event_type,amount FROM finance_ledger "
                "WHERE order_id=? ORDER BY id",
                (order_id,),
            ).fetchall()

    def test_policy_defaults_are_source_specific(self):
        self.assertEqual(
            default_client_payment_route("usa"),
            PaymentRoute.EXTREMIZER_BALANCE,
        )
        self.assertEqual(
            default_client_payment_route("warehouse"),
            PaymentRoute.DIRECT_PARTNER,
        )

    def test_internal_default_direct_partner_has_no_wallet_charge(self):
        result = self.persist("I-P0-DIRECT", {"x": self.warehouse_item("RF-1")})
        self.assertIsNone(result["finance_event_id"])
        self.assertEqual(
            self.routes("I-P0-DIRECT"),
            [("warehouse", "direct_partner")],
        )
        self.assertEqual(self.ledger("I-P0-DIRECT"), [])

    def test_usa_default_remains_extremizer_wallet(self):
        result = self.persist("E-P0-USA", {"x": self.usa_item("USA-1", 50)})
        self.assertIsNotNone(result["finance_event_id"])
        self.assertEqual(
            self.routes("E-P0-USA"),
            [("usa", "extremizer_balance")],
        )
        self.assertEqual(self.ledger("E-P0-USA"), [("ORDER_CHARGE", "-5000.00")])

    def test_legacy_mixed_cart_charges_only_usa_default_line(self):
        result = self.persist(
            "LEGACY-P0-MIXED",
            {
                "usa": self.usa_item("USA-2", 50),
                "rf": self.warehouse_item("RF-2", 12000),
            },
        )
        self.assertIsNotNone(result["finance_event_id"])
        self.assertEqual(
            self.routes("LEGACY-P0-MIXED"),
            [
                ("usa", "extremizer_balance"),
                ("warehouse", "direct_partner"),
            ],
        )
        self.assertEqual(
            self.ledger("LEGACY-P0-MIXED"),
            [("ORDER_CHARGE", "-5000.00")],
        )

    def test_admin_exception_requires_admin_and_full_partner_reservation(self):
        self.assertEqual(
            resolve_internal_client_payment_route(),
            PaymentRoute.DIRECT_PARTNER,
        )
        with self.assertRaisesRegex(PermissionError, "REQUIRES_ADMIN"):
            resolve_internal_client_payment_route(
                admin_assign_extremizer=True,
                admin_authorized=False,
                full_partner_amount_reserved=True,
            )
        with self.assertRaisesRegex(ValueError, "REQUIRES_FULL_PARTNER_BALANCE"):
            resolve_internal_client_payment_route(
                admin_assign_extremizer=True,
                admin_authorized=True,
                full_partner_amount_reserved=False,
            )
        route = resolve_internal_client_payment_route(
            admin_assign_extremizer=True,
            admin_authorized=True,
            full_partner_amount_reserved=True,
        )
        self.assertEqual(route, PaymentRoute.EXTREMIZER_BALANCE)
        result = self.persist(
            "I-P0-ADMIN",
            {"x": self.warehouse_item("RF-ADMIN", 16575, payment_route=route)},
        )
        self.assertIsNotNone(result["finance_event_id"])
        self.assertEqual(
            self.routes("I-P0-ADMIN"),
            [("warehouse", "extremizer_balance")],
        )
        self.assertEqual(
            self.ledger("I-P0-ADMIN"),
            [("ORDER_CHARGE", "-16575.00")],
        )

    def test_repeated_finance_bridge_never_double_charges(self):
        result = self.persist("E-P0-REPLAY", {"x": self.usa_item("USA-R", 25)})
        first = result["finance_event_id"]
        with sqlite3.connect(self.db) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            second = post_persisted_order_charge(
                self.adapter,
                conn=c,
                client_id=str(User.id),
                order_id="E-P0-REPLAY",
                actor="routing-regression",
            )
            third = post_persisted_order_charge(
                self.adapter,
                conn=c,
                client_id=str(User.id),
                order_id="E-P0-REPLAY",
                actor="routing-regression",
            )
            c.commit()
        self.assertEqual((first, second, third), (first, first, first))
        self.assertEqual(
            self.ledger("E-P0-REPLAY"),
            [("ORDER_CHARGE", "-2500.00")],
        )

    def test_explicit_existing_snapshot_is_not_rewritten_by_new_default(self):
        self.persist(
            "LEGACY-P0-SNAPSHOT",
            {
                "x": self.warehouse_item(
                    "RF-OLD",
                    9000,
                    payment_route=PaymentRoute.EXTREMIZER_BALANCE,
                )
            },
        )
        self.assertEqual(
            self.routes("LEGACY-P0-SNAPSHOT"),
            [("warehouse", "extremizer_balance")],
        )


if __name__ == "__main__":
    unittest.main()
