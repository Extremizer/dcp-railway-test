#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Point 9: full isolated RF checkout -> delivery E2E."""
from __future__ import annotations

import ast
import asyncio
from contextlib import nullcontext
from datetime import datetime
import sqlite3
import tempfile
import unittest
from pathlib import Path

from _client_checkout_callback_adapter_regression import (
    create_schema,
    compile_save_order_to_history,
)
from client_checkout_callback_adapter import execute_prepared_checkout
from client_finance_adapter import ClientFinanceAdapter
from common_finance_contract import FulfillmentRoute, PaymentRoute, default_client_payment_route
import fulfillment_slices as fs
from oemixibot_finance import FinanceEngine
import stock_engine
import supplier_channels
import supplier_customer_notify
import supplier_delivery
import warehouse_store
from supplier_order_service import SupplierOrderService


ROOT=Path(__file__).resolve().parent
BOT_SOURCE=ROOT/"extremizer_bot.py"


def compile_bot_function(name):
    source=BOT_SOURCE.read_text(encoding="utf-8")
    tree=ast.parse(source,filename=str(BOT_SOURCE))
    target=next(
        node for node in tree.body
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef))
        and node.name==name
    )
    module=ast.Module(body=[target],type_ignores=[])
    ast.fix_missing_locations(module)
    return compile(module,str(BOT_SOURCE),"exec")


class User:
    id=7005635854
    full_name="Point 9 E2E Client"
    username="point9_e2e"


class SupplierFullE2EPoint9Tests(unittest.TestCase):
    def setUp(self):
        self.tmp_path=Path(tempfile.mkdtemp(prefix="supplier_point9_"))
        self.db=self.tmp_path/"orders.db"
        create_schema(self.db)

        # The production order schema has a business status. The isolated
        # callback fixture intentionally omits it, so add it with the same
        # non-invasive default needed by the larger logistics contour.
        with sqlite3.connect(self.db) as c:
            c.execute(
                "ALTER TABLE orders ADD COLUMN status TEXT DEFAULT 'confirmed'"
            )

        warehouse_store.init_warehouse_db(self.db)
        self.wid=warehouse_store.add_warehouse(
            "WH1","Moscow","W1","Склад 1",
            priority=10,db_file=self.db,
        )
        warehouse_store.ensure_source(
            self.wid,"website",enabled=True,priority=10,
            ttl_minutes=60,db_file=self.db,
        )
        warehouse_store.record_source_stock(
            self.wid,"OEM-E2E","in_stock",10,"website",
            manufacturer="BRP",name="Point 9 Part",
            price_rub=2500,db_file=self.db,
        )

        self.finance=ClientFinanceAdapter(FinanceEngine(str(self.db)))
        self.service=SupplierOrderService(self.db)
        supplier_channels.save(
            self.db,self.wid,"telegram","test-supplier-destination"
        )

        # Compile the exact current production persistence/reservation functions
        # out of extremizer_bot.py without importing Telegram runtime.
        namespace={
            "sqlite3":sqlite3,
            "datetime":datetime,
            "nullcontext":nullcontext,
            "PaymentRoute":PaymentRoute,
            "FulfillmentRoute":FulfillmentRoute,
            "default_client_payment_route":default_client_payment_route,
            "ORDERS_DB_FILE":self.db,
            "stock_engine":stock_engine,
            "USD_RUB_RATE":100.0,
            "PRICE_COEFFICIENT":1.0,
            "ITEM_TYPE_ALLOWED_TARIFFS":{
                "part":("comfort","economy"),
                "accessory":("comfort","economy"),
                "gear":("economy","mix"),
            },
            "init_orders_db":lambda: warehouse_store.init_warehouse_db(self.db),
            "customer_rub_price_from_dp":(
                lambda dp,coefficient,rate:
                None if dp is None
                else int(round(float(dp)*float(coefficient)*float(rate)))
            ),
            "get_oem_reference":lambda current_oem,requested_oem:None,
            "infer_item_type_from_dcp_catalog":lambda catalog:None,
        }
        exec(compile_save_order_to_history(),namespace)
        exec(compile_bot_function("reserve_local_order_items"),namespace)
        self.save_order=namespace["save_order_to_history"]
        self.reserve_order=namespace["reserve_local_order_items"]

        self._old_notify_shortage=supplier_customer_notify.notify_shortage
        supplier_customer_notify.notify_shortage=(
            lambda service,shortage,public_name:
            "shortage-client-message"
        )

    def tearDown(self):
        supplier_customer_notify.notify_shortage=self._old_notify_shortage

    def cart(self,qty=2):
        return {
            "rf":{
                "manufacturer":"BRP",
                "oem":"OEM-E2E",
                "requested_oem":"OEM-E2E",
                "name":"Point 9 Part",
                "qty":qty,
                "price_snapshot_rub":2500,
                "available_snapshot":10,
                "offer_source":"warehouse",
                "warehouse_id":self.wid,
                "warehouse_public_name":"Склад 1",
            }
        }

    async def checkout_async(self,order_id,qty=2):
        events=[]

        def state():
            with sqlite3.connect(self.db) as c:
                return {
                    "orders":c.execute(
                        "SELECT COUNT(*) FROM orders WHERE order_id=?",
                        (order_id,),
                    ).fetchone()[0],
                    "items":c.execute(
                        "SELECT COUNT(*) FROM order_items WHERE order_id=?",
                        (order_id,),
                    ).fetchone()[0],
                    "ledger":c.execute(
                        "SELECT COUNT(*) FROM finance_ledger WHERE order_id=?",
                        (order_id,),
                    ).fetchone()[0],
                }

        def manager_notify():
            self.assertEqual(
                state(),{"orders":1,"items":1,"ledger":1}
            )
            events.append("manager")

        def supplier_orders():
            self.assertEqual(
                state(),{"orders":1,"items":1,"ledger":1}
            )
            result=self.service.create_from_client_order(
                order_id,
                {
                    "name":"Client",
                    "phone":"+70000000000",
                    "city":"Moscow",
                    "method":"CDEK",
                    "address":"Test address",
                },
            )
            events.append("supplier")
            return result

        def reservation():
            self.assertEqual(
                state(),{"orders":1,"items":1,"ledger":1}
            )
            result=self.reserve_order(order_id)
            events.append("reserve")
            return result

        def finalize():
            events.append("finalize")

        result=await execute_prepared_checkout(
            db_path=self.db,
            save_order_fn=self.save_order,
            finance_adapter=self.finance,
            order_id=order_id,
            user=User(),
            cart=self.cart(qty),
            actor="point9_e2e",
            manager_notify=manager_notify,
            supplier_orders=supplier_orders,
            warehouse_reservation=reservation,
            finalize_session=finalize,
        )
        self.assertEqual(
            events,["manager","supplier","reserve","finalize"]
        )
        return result

    def checkout(self,order_id,qty=2):
        return asyncio.run(self.checkout_async(order_id,qty))

    def supplier_id(self,order_id):
        with sqlite3.connect(self.db) as c:
            return int(c.execute(
                "SELECT id FROM supplier_orders WHERE client_order_id=?",
                (order_id,),
            ).fetchone()[0])

    def supplier_item_id(self,sid):
        with sqlite3.connect(self.db) as c:
            return int(c.execute(
                "SELECT id FROM supplier_order_items WHERE supplier_order_id=?",
                (sid,),
            ).fetchone()[0])

    def reservation(self,order_id):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            row=c.execute(
                """SELECT * FROM warehouse_stock_reservations
                   WHERE order_id=? ORDER BY id""",
                (order_id,),
            ).fetchall()
            return [dict(x) for x in row]

    def slices(self,sid):
        return fs.list_for_supplier_order(self.db,sid)

    def fulfillment_events(self,sid):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [dict(x) for x in c.execute(
                """SELECT fe.*
                   FROM fulfillment_events fe
                   JOIN fulfillment_slices fs ON fs.id=fe.slice_id
                   WHERE fs.supplier_order_id=?
                   ORDER BY fe.id""",
                (sid,),
            )]

    def finance_rows(self,order_id):
        with sqlite3.connect(self.db) as c:
            c.row_factory=sqlite3.Row
            return [dict(x) for x in c.execute(
                "SELECT * FROM finance_ledger WHERE order_id=? ORDER BY id",
                (order_id,),
            )]

    def test_reservation_retry_conflict_is_explicit_and_non_destructive(self):
        order_id="E-P9-CONFLICT"
        self.checkout(order_id,2)
        rows=self.reservation(order_id)
        self.assertEqual(len(rows),1)
        order_item_id=int(rows[0]["order_item_id"])

        result=stock_engine.reserve_stock(
            self.wid,
            "OEM-E2E",
            1,
            order_id=order_id,
            order_item_id=order_item_id,
            manufacturer="BRP",
            require_fresh=True,
            db_file=self.db,
        )
        self.assertFalse(result["ok"])
        self.assertEqual(result["reason"],"reservation_conflict")
        after=self.reservation(order_id)
        self.assertEqual(len(after),1)
        self.assertEqual(after[0]["id"],rows[0]["id"])
        self.assertEqual(after[0]["quantity"],2)

    def test_full_rf_checkout_to_delivered_e2e(self):
        order_id="E-P9-FULL"
        checkout_result=self.checkout(order_id,2)
        self.assertEqual(checkout_result.order_id,order_id)

        # Checkout facts.
        with sqlite3.connect(self.db) as c:
            item=c.execute(
                """SELECT id,offer_source,warehouse_id,quantity,
                          customer_unit_rub,payment_route
                   FROM order_items WHERE order_id=?""",
                (order_id,),
            ).fetchone()
        self.assertEqual(item[1],"warehouse")
        self.assertEqual(int(item[2]),self.wid)
        self.assertEqual(int(item[3]),2)
        self.assertEqual(int(item[4]),2500)
        self.assertEqual(item[5],"extremizer_balance")

        finance=self.finance_rows(order_id)
        self.assertEqual(len(finance),1)
        self.assertEqual(finance[0]["event_type"],"ORDER_CHARGE")
        self.assertEqual(finance[0]["amount"],"-5000.00")

        sid=self.supplier_id(order_id)
        reservations=self.reservation(order_id)
        self.assertEqual(len(reservations),1)
        self.assertEqual(reservations[0]["status"],"reserved")
        self.assertEqual(reservations[0]["quantity"],2)

        roots=self.slices(sid)
        self.assertEqual(len(roots),1)
        self.assertEqual(roots[0]["status"],fs.RF_PENDING_RESERVATION)

        # Warehouse execution.
        supplier_delivery.send_one(
            self.service,sid,
            {"telegram":lambda destination,text:"provider:E2E-FULL"},
        )
        self.assertEqual(
            self.reservation(order_id)[0]["status"],"committed"
        )
        self.assertEqual(
            self.slices(sid)[0]["status"],fs.RF_SENT_TO_WAREHOUSE
        )

        self.service.change_status(
            sid,"confirmed",
            actor_type="admin",actor_id=42,source="point9_e2e",
        )
        self.service.change_status(
            sid,"assembled",
            actor_type="admin",actor_id=42,source="point9_e2e",
        )
        self.service.add_tracking(
            sid,"CDEK","P9-FULL-TRACK",
            client_sender=lambda *a:"client-tracking-full",
            actor_type="admin",actor_id=42,source="point9_e2e",
        )
        final=self.service.deliver_supplier_order(
            sid,
            actor_type="admin",actor_id=42,source="point9_e2e",
            client_sender=lambda *a:"client-delivered-full",
        )

        self.assertEqual(final["status"],"delivered")
        self.assertTrue(final["delivered_at"])
        self.assertEqual(
            self.slices(sid)[0]["status"],fs.RF_DELIVERED
        )
        reservation=self.reservation(order_id)[0]
        self.assertEqual(reservation["status"],"fulfilled")
        self.assertTrue(reservation["fulfilled_at"])

        # No active reservation remains, but stale manual stock is protected.
        with sqlite3.connect(self.db) as c:
            active=c.execute(
                """SELECT COUNT(*) FROM warehouse_stock_reservations
                   WHERE order_id=?
                     AND status IN ('hold','reserved','committed')""",
                (order_id,),
            ).fetchone()[0]
        self.assertEqual(active,0)
        stock=stock_engine.get_available_stock(
            self.wid,"OEM-E2E",self.db
        )
        self.assertEqual(stock["raw_quantity"],10)
        self.assertEqual(stock["blocked_quantity"],2)
        self.assertEqual(stock["available_quantity"],8)

        types=[
            x["event_type"] for x in self.fulfillment_events(sid)
        ]
        self.assertEqual(
            types,
            [
                "slice_created",
                "supplier_sent",
                "warehouse_confirmed",
                "warehouse_assembled",
                "shipped_to_client",
                "delivered_to_client",
            ],
        )

        # Terminal operations are idempotent.
        events_before=len(self.fulfillment_events(sid))
        self.service.deliver_supplier_order(
            sid,client_sender=lambda *a:"must-not-send-again"
        )
        self.assertEqual(len(self.fulfillment_events(sid)),events_before)
        self.assertEqual(len(self.finance_rows(order_id)),1)

    def test_shortage_split_checkout_to_delivered_e2e(self):
        order_id="E-P9-SPLIT"
        self.checkout(order_id,2)
        sid=self.supplier_id(order_id)
        supplier_item_id=self.supplier_item_id(sid)

        supplier_delivery.send_one(
            self.service,sid,
            {"telegram":lambda destination,text:"provider:E2E-SPLIT"},
        )
        shortage=self.service.report_item_problem(
            sid,supplier_item_id,1,
            actor_type="admin",actor_id=42,source="point9_e2e",
        )
        self.assertEqual(shortage["shortage_qty"],1)
        reservation=self.reservation(order_id)[0]
        self.assertEqual(reservation["status"],"committed")
        self.assertEqual(reservation["quantity"],1)

        rows=self.slices(sid)
        self.assertEqual(len(rows),2)
        confirmed=next(x for x in rows if x["parent_slice_id"] is None)
        missing=next(x for x in rows if x["parent_slice_id"] is not None)
        self.assertEqual(confirmed["quantity"],1)
        self.assertEqual(
            confirmed["status"],fs.RF_WAREHOUSE_CONFIRMED
        )
        self.assertEqual(missing["quantity"],1)
        self.assertEqual(missing["issue"],fs.ISSUE_SHORTAGE)

        self.service.decide_shortage(
            shortage["id"],"partial_refund",
            actor_type="client",actor_id=User.id,source="point9_e2e",
        )
        rows=self.slices(sid)
        missing=next(x for x in rows if x["parent_slice_id"] is not None)
        self.assertEqual(missing["status"],fs.RF_CANCELLED)
        self.assertIsNone(missing["issue"])

        self.service.change_status(sid,"confirmed",source="point9_e2e")
        self.service.change_status(sid,"assembled",source="point9_e2e")
        self.service.add_tracking(
            sid,"CDEK","P9-SPLIT-TRACK",
            client_sender=lambda *a:"client-tracking-split",
            source="point9_e2e",
        )
        final=self.service.deliver_supplier_order(
            sid,
            source="point9_e2e",
            client_sender=lambda *a:"client-delivered-split",
        )
        self.assertEqual(final["status"],"delivered")

        rows=self.slices(sid)
        confirmed=next(x for x in rows if x["parent_slice_id"] is None)
        missing=next(x for x in rows if x["parent_slice_id"] is not None)
        self.assertEqual(confirmed["status"],fs.RF_DELIVERED)
        self.assertEqual(missing["status"],fs.RF_CANCELLED)

        reservation=self.reservation(order_id)[0]
        self.assertEqual(reservation["status"],"fulfilled")
        self.assertEqual(reservation["quantity"],1)

        stock=stock_engine.get_available_stock(
            self.wid,"OEM-E2E",self.db
        )
        self.assertEqual(stock["available_quantity"],9)

        with sqlite3.connect(self.db) as c:
            obligation=c.execute(
                """SELECT obligation_type,quantity,status
                   FROM supplier_financial_obligations
                   WHERE shortage_id=?""",
                (shortage["id"],),
            ).fetchone()
        self.assertEqual(
            obligation,("refund",1.0,"pending_finance_layer")
        )
        # Original checkout charge stays singular; shortage finance is a
        # separate obligation and does not duplicate checkout.
        self.assertEqual(len(self.finance_rows(order_id)),1)

    def test_checkout_post_commit_hooks_are_idempotent_for_recovery(self):
        order_id="E-P9-RETRY"
        self.checkout(order_id,2)
        sid=self.supplier_id(order_id)

        # Re-running Supplier Order creation must not duplicate the shipment.
        again=self.service.create_from_client_order(
            order_id,
            {
                "name":"Client","phone":"+70000000000",
                "city":"Moscow","method":"CDEK","address":"Test address",
            },
        )
        self.assertEqual(len(again),1)
        self.assertEqual(int(again[0]["id"]),sid)

        # The warehouse reservation hook must also be safe to retry after the
        # core checkout has already committed.
        first=self.reservation(order_id)
        self.assertEqual(len(first),1)
        retry=self.reserve_order(order_id)
        self.assertTrue(retry.get("ok"))
        second=self.reservation(order_id)
        self.assertEqual(len(second),1)
        self.assertEqual(second[0]["id"],first[0]["id"])
        self.assertEqual(second[0]["quantity"],2)

        with sqlite3.connect(self.db) as c:
            supplier_count=c.execute(
                "SELECT COUNT(*) FROM supplier_orders WHERE client_order_id=?",
                (order_id,),
            ).fetchone()[0]
            item_count=c.execute(
                """SELECT COUNT(*) FROM supplier_order_items
                   WHERE supplier_order_id=?""",(sid,)
            ).fetchone()[0]
        self.assertEqual(supplier_count,1)
        self.assertEqual(item_count,1)
        self.assertEqual(len(self.finance_rows(order_id)),1)


if __name__=="__main__":
    unittest.main(verbosity=2)
