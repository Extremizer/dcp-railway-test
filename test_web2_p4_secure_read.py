from __future__ import annotations

from pathlib import Path
import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

import supplier_admin_auth
from internal_order_core_v01 import create_payment_snapshot, init as init_internal
from web2_p4_secure_read_harness import build_test_app


def create_schema(db: str) -> None:
    with sqlite3.connect(db) as c:
        c.executescript(
            """
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,
              status TEXT NOT NULL,
              created_at TEXT NOT NULL,
              telegram_user_id INTEGER NOT NULL
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              order_id TEXT NOT NULL,
              position INTEGER NOT NULL,
              manufacturer TEXT,
              oem TEXT,
              requested_oem TEXT,
              name TEXT,
              offer_source TEXT NOT NULL,
              warehouse_id INTEGER,
              quantity INTEGER NOT NULL,
              customer_unit_rub INTEGER,
              payment_route TEXT NOT NULL
            );
            """
        )


class Web2P4SecureReadProofTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = str(Path(self.tmp.name) / "p4.db")
        create_schema(self.db)
        init_internal(self.db)
        self._add_snapshot_order()
        self.env = patch.dict(
            os.environ,
            {"EXTREMIZER_WEB_ADMIN_TOKEN": "isolated-test-admin-token"},
            clear=False,
        )
        self.env.start()
        self.addCleanup(self.env.stop)
        self.app = build_test_app(
            self.db,
            allowed_admin_warehouse_ids={1},
        )
        self.client = TestClient(self.app)

    def _add_snapshot_order(self):
        with sqlite3.connect(self.db) as c:
            c.execute(
                "INSERT INTO orders VALUES(?,?,?,?)",
                (
                    "I-P4-GET",
                    "assembled_unpaid",
                    "2026-10-14T00:30:00+02:00",
                    7001,
                ),
            )
            c.execute(
                """INSERT INTO order_items(
                     order_id,position,manufacturer,oem,requested_oem,name,
                     offer_source,warehouse_id,quantity,customer_unit_rub,payment_route)
                   VALUES(
                     'I-P4-GET',1,'BRP','417300574','417300574','Part',
                     'warehouse',1,1,19500,'direct_partner'
                   )"""
            )
        create_payment_snapshot(
            self.db,
            order_id="I-P4-GET",
            client_id="client:canonical-7001",
            partner_amount_rub=16575,
            created_by="p4:test",
            warehouse_order_number="W-P4-1",
            expected_warehouse_id=1,
        )

    def _session(self, now=1_000_000):
        with patch.object(supplier_admin_auth.time, "time", return_value=now):
            return supplier_admin_auth.issue_web_admin_session()

    def _get(self, path, *, cookie=None, now=1_000_000, headers=None):
        cookies = {}
        if cookie is not None:
            cookies[supplier_admin_auth.WEB_ADMIN_COOKIE] = cookie
        with patch.object(supplier_admin_auth.time, "time", return_value=now):
            return self.client.get(
                path,
                cookies=cookies,
                headers=headers or {},
            )

    def _dump(self):
        with sqlite3.connect(self.db) as c:
            return "\n".join(c.iterdump())

    def test_existing_signed_admin_session_reaches_p3_adapter_json(self):
        response = self._get(
            "/_test/web2/admin/warehouses/1/history",
            cookie=self._session(),
        )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["contractVersion"], "internal-order-read-v0.1")
        self.assertEqual(
            data["scope"],
            {"warehouseId": 1, "viewerType": "admin"},
        )
        row = data["warehouse"]["rows"][0]
        self.assertEqual(row["extremizerOrderNo"], "I-P4-GET")
        self.assertEqual(row["warehouseOrderNo"], "W-P4-1")
        self.assertEqual(row["customerAmountRub"], 19500)
        self.assertEqual(row["partnerAmountRub"], 16575)
        self.assertEqual(row["paymentStatus"], "DUE")
        self.assertEqual(row["status"], "Собран, не оплачен")
        self.assertEqual(row["oems"], ["417300574"])

    def test_canonical_client_id_is_not_presented_as_display_name(self):
        response = self._get(
            "/_test/web2/admin/warehouses/1/history",
            cookie=self._session(),
        )
        row = response.json()["warehouse"]["rows"][0]
        self.assertEqual(row["clientId"], "client:canonical-7001")
        self.assertIsNone(row["client"])
        self.assertNotEqual(row["client"], row["clientId"])

    def test_missing_session_denied_without_sensitive_data(self):
        response = self._get("/_test/web2/admin/warehouses/1/history")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "unauthorized"})
        body = response.text
        self.assertNotIn("I-P4-GET", body)
        self.assertNotIn("client:canonical-7001", body)
        self.assertNotIn("16575", body)

    def test_invalid_session_denied(self):
        response = self._get(
            "/_test/web2/admin/warehouses/1/history",
            cookie="v1.invalid.session.value",
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "unauthorized"})

    def test_expired_session_denied(self):
        issued = 1_000_000
        cookie = self._session(now=issued)
        response = self._get(
            "/_test/web2/admin/warehouses/1/history",
            cookie=cookie,
            now=issued + supplier_admin_auth.WEB_ADMIN_SESSION_MAX_AGE + 1,
        )
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "unauthorized"})

    def test_cross_warehouse_request_denied_server_side(self):
        response = self._get(
            "/_test/web2/admin/warehouses/2/history",
            cookie=self._session(),
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json(), {"detail": "scope denied"})
        self.assertNotIn("I-P4-GET", response.text)

    def test_forged_browser_role_or_warehouse_headers_are_not_identity(self):
        forged = {
            "x-role": "admin",
            "x-effective-role": "admin",
            "x-warehouse-id": "1",
            "x-actual-actor": "admin:forged",
        }
        denied = self._get(
            "/_test/web2/admin/warehouses/1/history",
            headers=forged,
        )
        self.assertEqual(denied.status_code, 401)

        allowed = self._get(
            "/_test/web2/admin/warehouses/1/history",
            cookie=self._session(),
            headers={
                "x-role": "warehouse",
                "x-warehouse-id": "999",
            },
        )
        self.assertEqual(allowed.status_code, 200)
        self.assertEqual(allowed.json()["scope"]["warehouseId"], 1)

    def test_sklad_is_deny_only_without_real_membership(self):
        response = self._get(
            "/_test/web2/sklad/warehouses/1/history",
            cookie=self._session(),
            headers={"x-role": "rf_warehouse_user"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(
            response.json(),
            {"detail": "warehouse auth unavailable"},
        )

    def test_protected_get_is_database_read_only(self):
        before = self._dump()
        response = self._get(
            "/_test/web2/admin/warehouses/1/history?search=I-P4&limit=1&offset=0",
            cookie=self._session(),
        )
        self.assertEqual(response.status_code, 200)
        after = self._dump()
        self.assertEqual(before, after)

    def test_pagination_transport_does_not_fake_matched_count(self):
        response = self._get(
            "/_test/web2/admin/warehouses/1/history?limit=1&offset=0",
            cookie=self._session(),
        )
        self.assertEqual(response.status_code, 200)
        page = response.json()["page"]
        self.assertEqual(page["limit"], 1)
        self.assertEqual(page["offset"], 0)
        self.assertIsNone(page["matchedCount"])

    def test_query_limits_are_server_enforced(self):
        too_large = self._get(
            "/_test/web2/admin/warehouses/1/history?limit=101",
            cookie=self._session(),
        )
        self.assertEqual(too_large.status_code, 422)

        negative = self._get(
            "/_test/web2/admin/warehouses/1/history?offset=-1",
            cookie=self._session(),
        )
        self.assertEqual(negative.status_code, 422)


if __name__ == "__main__":
    unittest.main(verbosity=2)
