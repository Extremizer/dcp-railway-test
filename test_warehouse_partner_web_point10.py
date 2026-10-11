#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
import sys
import types

# web_app imports OEM maintenance, which imports the optional S3 backup client.
# The settlement HTTP regression never calls that subsystem, so isolate it
# instead of installing unrelated boto3 into the local test environment.
_backup_stub=types.ModuleType("backup_object_store")
_backup_stub.backup_sqlite_to_bucket=lambda *a,**k: {
    "status":"not_called_in_point10_test"
}
sys.modules.setdefault("backup_object_store",_backup_stub)

TMP_ROOT=Path(tempfile.mkdtemp(prefix="warehouse_partner_web_p10_"))
DB=TMP_ROOT/"orders.db"
os.environ["EXTREMIZER_ORDERS_DB_FILE"]=str(DB)
os.environ["EXTREMIZER_WEB_ADMIN_TOKEN"]="point10-test-admin-token"

from fastapi.testclient import TestClient

import supplier_admin_auth
import warehouse_partner_settlements as settlements
import warehouse_store
import web_app


class WarehousePartnerWebPoint10Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        web_app.core.init_orders_db()
        warehouse_store.init_warehouse_db(DB)
        cls.wid=warehouse_store.add_warehouse(
            "P10 WEB","Moscow","P10W","Склад P10",
            priority=1,db_file=DB,
        )
        with sqlite3.connect(DB) as c:
            c.execute(
                """INSERT INTO orders(
                     order_id,created_at,telegram_user_id,customer_name,
                     total_usd,usd_rub_rate,status,origin)
                   VALUES('P10-WEB','now',100,'Client',0,100,'confirmed','web')"""
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,manufacturer,oem,requested_oem,name,
                     quantity,price_usd,customer_unit_rub,offer_source,
                     payment_route,fulfillment_route,warehouse_id,
                     warehouse_public_name,price_snapshot_rub)
                   VALUES(
                     'P10-WEB',1,'BRP','P10-OEM','P10-OEM','Part',
                     1,NULL,5000,'warehouse','extremizer_balance',
                     'through_extremizer',?,'Склад P10',5000)""",
                (cls.wid,),
            )
        settlements.init(DB)

    def setUp(self):
        with sqlite3.connect(DB) as c:
            c.execute("DELETE FROM warehouse_partner_ledger")
            c.execute("DELETE FROM warehouse_partner_settlement_events")
            c.execute("DELETE FROM warehouse_partner_settlements")
        self.client=TestClient(
            web_app.app,
            base_url="https://testserver",
            follow_redirects=False,
        )
        self.session=supplier_admin_auth.issue_web_admin_session()
        self.client.cookies.set(
            supplier_admin_auth.WEB_ADMIN_COOKIE,
            self.session,
        )

    def csrf(self,scope,subject):
        return supplier_admin_auth.issue_scoped_admin_csrf_token(
            self.session,scope,str(subject)
        )

    def test_unauthorized_page_redirects_to_login(self):
        anonymous=TestClient(
            web_app.app,
            base_url="https://testserver",
            follow_redirects=False,
        )
        response=anonymous.get("/admin/warehouse-settlements")
        self.assertEqual(response.status_code,303)
        self.assertIn("/admin/login",response.headers["location"])

    def test_get_is_read_only_and_renders_section(self):
        before=settlements.list_settlements(DB)
        response=self.client.get("/admin/warehouse-settlements")
        after=settlements.list_settlements(DB)
        self.assertEqual(response.status_code,200)
        self.assertIn("Мои расчёты со складами",response.text)
        self.assertEqual(before,[])
        self.assertEqual(after,[])

    def test_sync_requires_scoped_csrf(self):
        bad=self.client.post(
            "/admin/warehouse-settlements/sync",
            data={"csrf_token":"0"*64},
        )
        self.assertEqual(bad.status_code,403)
        self.assertEqual(settlements.list_settlements(DB),[])

        good=self.client.post(
            "/admin/warehouse-settlements/sync",
            data={
                "csrf_token":self.csrf(
                    "warehouse-settlements-sync","all"
                )
            },
        )
        self.assertEqual(good.status_code,303)
        rows=settlements.list_settlements(DB)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["status"],"pending_terms")
        self.assertIsNone(rows[0]["partner_amount_rub"])

    def test_full_web_terms_accrual_payment_flow(self):
        settlements.sync_pending_from_orders(DB,actor="test")
        row=settlements.list_settlements(DB)[0]
        sid=int(row["id"])

        terms=self.client.post(
            f"/admin/warehouse-settlements/{sid}/terms",
            data={
                "csrf_token":self.csrf(
                    "warehouse-settlement-terms",sid
                ),
                "mode":"payable",
                "partner_amount_rub":"3500",
                "terms_source":"agreement-test",
                "notes":"web flow",
            },
        )
        self.assertEqual(terms.status_code,303)
        row=settlements.get(DB,sid)
        self.assertEqual(row["status"],"ready")
        self.assertEqual(row["partner_amount_rub"],3500)

        accrued=self.client.post(
            f"/admin/warehouse-settlements/{sid}/accrue",
            data={
                "csrf_token":self.csrf(
                    "warehouse-settlement-accrue",sid
                )
            },
        )
        self.assertEqual(accrued.status_code,303)
        row=settlements.get(DB,sid)
        self.assertEqual(row["balance_rub"],-3500)

        paid=self.client.post(
            f"/admin/warehouse-settlements/{sid}/payment",
            data={
                "csrf_token":self.csrf(
                    "warehouse-settlement-payment",sid
                ),
                "direction":"to_partner",
                "amount_rub":"3500",
                "payment_ref":"WEB-PAY-1",
            },
        )
        self.assertEqual(paid.status_code,303)
        row=settlements.get(DB,sid)
        self.assertEqual(row["status"],"settled")
        self.assertEqual(row["balance_rub"],0)

        page=self.client.get("/admin/warehouse-settlements")
        self.assertEqual(page.status_code,200)
        self.assertIn("P10-WEB",page.text)
        self.assertIn("Склад P10",page.text)
        self.assertIn("✅ Закрыт",page.text)

    def test_csrf_is_bound_to_action_and_subject(self):
        settlements.sync_pending_from_orders(DB,actor="test")
        sid=int(settlements.list_settlements(DB)[0]["id"])
        wrong_scope=self.csrf("warehouse-settlement-accrue",sid)
        response=self.client.post(
            f"/admin/warehouse-settlements/{sid}/terms",
            data={
                "csrf_token":wrong_scope,
                "mode":"payable",
                "partner_amount_rub":"1000",
                "terms_source":"bad",
            },
        )
        self.assertEqual(response.status_code,403)
        self.assertEqual(
            settlements.get(DB,sid)["status"],"pending_terms"
        )


if __name__=="__main__":
    unittest.main(verbosity=2)

