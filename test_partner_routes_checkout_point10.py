#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
from common_finance_contract import (
    FulfillmentRoute,
    PaymentRoute,
    default_client_payment_route,
)
from oemixibot_finance import FinanceEngine


class User:
    id=991001
    full_name="Point10 Routes"
    username="point10"


class PartnerRoutesCheckoutPoint10Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="point10_checkout_routes_"))
        self.db=self.tmp/"orders.db"
        create_schema(self.db)

        code=compile_save_order_to_history()
        namespace={
            "sqlite3":sqlite3,
            "datetime":datetime,
            "nullcontext":nullcontext,
            "PaymentRoute":PaymentRoute,
            "FulfillmentRoute":FulfillmentRoute,
            "default_client_payment_route":default_client_payment_route,
            "ORDERS_DB_FILE":self.db,
            "USD_RUB_RATE":100.0,
            "PRICE_COEFFICIENT":1.0,
            "ITEM_TYPE_ALLOWED_TARIFFS":{
                "part":("comfort","economy"),
                "accessory":("comfort","economy"),
                "gear":("economy","mix"),
            },
            "init_orders_db":lambda:None,
            "customer_rub_price_from_dp":(
                lambda dp,coefficient,rate:
                None if dp is None
                else int(round(float(dp)*float(coefficient)*float(rate)))
            ),
            "get_oem_reference":lambda current_oem,requested_oem:None,
            "infer_item_type_from_dcp_catalog":lambda catalog:None,
        }
        exec(code,namespace)
        self.save_order=namespace["save_order_to_history"]
        self.adapter=ClientFinanceAdapter(FinanceEngine(str(self.db)))

    def persist(self,order_id,cart):
        return persist_client_checkout_atomic(
            db_path=self.db,
            save_order_fn=self.save_order,
            finance_adapter=self.adapter,
            order_id=order_id,
            user=User(),
            cart=cart,
            origin="web",
            actor="point10_checkout_test",
        )

    def warehouse_item(
        self,
        *,
        oem,
        qty=1,
        price=5000,
        payment_route="extremizer_balance",
        fulfillment_route="through_extremizer",
        warehouse_id=1,
    ):
        return {
            "manufacturer":"BRP",
            "oem":oem,
            "requested_oem":oem,
            "name":"Partner part",
            "qty":qty,
            "price_snapshot_rub":price,
            "offer_source":"warehouse",
            "warehouse_id":warehouse_id,
            "warehouse_public_name":f"Склад {warehouse_id}",
            "payment_route":payment_route,
            "fulfillment_route":fulfillment_route,
        }

    def rows(self,order_id):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [dict(x) for x in c.execute(
                """SELECT offer_source,payment_route,fulfillment_route,
                          quantity,customer_unit_rub
                   FROM order_items
                   WHERE order_id=?
                   ORDER BY position""",
                (order_id,),
            )]

    def ledger(self,order_id):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [dict(x) for x in c.execute(
                "SELECT * FROM finance_ledger WHERE order_id=? ORDER BY id",
                (order_id,),
            )]

    def test_direct_partner_dropship_has_no_client_wallet_charge(self):
        result=self.persist(
            "P10-DIRECT",
            {
                "x":self.warehouse_item(
                    oem="P10-DIRECT-OEM",
                    price=12000,
                    payment_route="direct_partner",
                    fulfillment_route="dropship",
                )
            },
        )
        self.assertIsNone(result["finance_event_id"])
        rows=self.rows("P10-DIRECT")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["payment_route"],"direct_partner")
        self.assertEqual(rows[0]["fulfillment_route"],"dropship")
        self.assertEqual(rows[0]["customer_unit_rub"],12000)
        self.assertEqual(self.ledger("P10-DIRECT"),[])

    def test_extremizer_payment_through_extremizer_charges_client(self):
        result=self.persist(
            "P10-OURSALE",
            {
                "x":self.warehouse_item(
                    oem="P10-OUR-OEM",
                    qty=2,
                    price=5000,
                    payment_route="extremizer_balance",
                    fulfillment_route="through_extremizer",
                )
            },
        )
        self.assertIsNotNone(result["finance_event_id"])
        rows=self.rows("P10-OURSALE")
        self.assertEqual(rows[0]["payment_route"],"extremizer_balance")
        self.assertEqual(
            rows[0]["fulfillment_route"],"through_extremizer"
        )
        ledger=self.ledger("P10-OURSALE")
        self.assertEqual(len(ledger),1)
        self.assertEqual(ledger[0]["event_type"],"ORDER_CHARGE")
        self.assertEqual(ledger[0]["amount"],"-10000.00")

    def test_mixed_payment_routes_charge_only_extremizer_line(self):
        result=self.persist(
            "P10-MIXED",
            {
                "ours":self.warehouse_item(
                    oem="P10-A",
                    price=5000,
                    payment_route="extremizer_balance",
                    fulfillment_route="dropship",
                    warehouse_id=1,
                ),
                "partner":self.warehouse_item(
                    oem="P10-B",
                    price=12000,
                    payment_route="direct_partner",
                    fulfillment_route="dropship",
                    warehouse_id=2,
                ),
            },
        )
        self.assertIsNotNone(result["finance_event_id"])
        rows=self.rows("P10-MIXED")
        self.assertEqual(
            [(x["payment_route"],x["fulfillment_route"]) for x in rows],
            [
                ("extremizer_balance","dropship"),
                ("direct_partner","dropship"),
            ],
        )
        ledger=self.ledger("P10-MIXED")
        self.assertEqual(len(ledger),1)
        self.assertEqual(ledger[0]["amount"],"-5000.00")

    def test_invalid_fulfillment_route_rolls_back_atomic_checkout(self):
        cart={
            "bad":self.warehouse_item(
                oem="P10-BAD",
                price=5000,
                fulfillment_route="teleport",
            )
        }
        with self.assertRaises(ValueError):
            self.persist("P10-BAD",cart)
        with sqlite3.connect(self.db) as c:
            orders=c.execute(
                "SELECT COUNT(*) FROM orders WHERE order_id='P10-BAD'"
            ).fetchone()[0]
            items=c.execute(
                "SELECT COUNT(*) FROM order_items WHERE order_id='P10-BAD'"
            ).fetchone()[0]
            ledger=c.execute(
                "SELECT COUNT(*) FROM finance_ledger WHERE order_id='P10-BAD'"
            ).fetchone()[0]
        self.assertEqual((orders,items,ledger),(0,0,0))


if __name__=="__main__":
    unittest.main(verbosity=2)
