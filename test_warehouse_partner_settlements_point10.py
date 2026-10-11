#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sqlite3
import tempfile
import unittest
from pathlib import Path

import supplier_orders
import warehouse_partner_settlements as settlements
import warehouse_store


class WarehousePartnerSettlementsPoint10Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=Path(tempfile.mkdtemp(prefix="partner_settlement_p10_"))
        self.db=self.tmp/"orders.db"
        warehouse_store.init_warehouse_db(self.db)
        self.w1=warehouse_store.add_warehouse(
            "WH1","Moscow","W1","Склад 1",priority=10,db_file=self.db
        )
        self.w2=warehouse_store.add_warehouse(
            "WH2","Yaroslavl","W2","Склад 2",priority=20,db_file=self.db
        )
        supplier_orders.init_supplier_orders_db(self.db)
        settlements.init(self.db)
        with sqlite3.connect(self.db) as c:
            c.executescript("""
            CREATE TABLE orders(
              order_id TEXT PRIMARY KEY,
              telegram_user_id INTEGER,
              status TEXT
            );
            CREATE TABLE order_items(
              id INTEGER PRIMARY KEY,
              order_id TEXT NOT NULL,
              offer_source TEXT NOT NULL,
              warehouse_id INTEGER,
              payment_route TEXT NOT NULL,
              fulfillment_route TEXT NOT NULL,
              quantity INTEGER NOT NULL,
              customer_unit_rub INTEGER,
              price_snapshot_rub REAL,
              oem TEXT,
              name TEXT
            );
            """)
            c.execute("INSERT INTO orders VALUES('P10-A',1,'confirmed')")
            c.execute("INSERT INTO orders VALUES('P10-B',2,'confirmed')")
            c.execute("INSERT INTO orders VALUES('P10-USA',3,'confirmed')")
            c.execute(
                """INSERT INTO order_items VALUES(
                  1,'P10-A','warehouse',?,'extremizer_balance',
                  'through_extremizer',2,5000,5000,'OEM-A','Part A')""",
                (self.w1,),
            )
            c.execute(
                """INSERT INTO order_items VALUES(
                  2,'P10-B','warehouse',?,'direct_partner',
                  'dropship',1,12000,12000,'OEM-B','Part B')""",
                (self.w2,),
            )
            c.execute(
                """INSERT INTO order_items VALUES(
                  3,'P10-USA','usa',NULL,'extremizer_balance',
                  'through_extremizer',1,15000,15000,'OEM-USA','USA Part')"""
            )

    def test_ensure_is_pending_and_does_not_invent_partner_cost(self):
        s=settlements.ensure_for_order_item(self.db,1,actor="test")
        self.assertEqual(s["status"],"pending_terms")
        self.assertEqual(s["settlement_mode"],"pending_terms")
        self.assertIsNone(s["partner_amount_rub"])
        self.assertEqual(s["customer_total_rub"],10000)
        self.assertEqual(s["payment_route"],"extremizer_balance")
        self.assertEqual(s["fulfillment_route"],"through_extremizer")
        self.assertEqual(s["balance_rub"],0)

    def test_ensure_is_idempotent(self):
        a=settlements.ensure_for_order_item(self.db,1,actor="test")
        b=settlements.ensure_for_order_item(self.db,1,actor="again")
        self.assertEqual(a["id"],b["id"])
        with sqlite3.connect(self.db) as c:
            count=c.execute(
                "SELECT COUNT(*) FROM warehouse_partner_settlements WHERE order_item_id=1"
            ).fetchone()[0]
            events=c.execute(
                "SELECT COUNT(*) FROM warehouse_partner_settlement_events WHERE settlement_id=?",
                (a["id"],),
            ).fetchone()[0]
        self.assertEqual(count,1)
        self.assertEqual(events,1)

    def test_payable_accrual_and_payments(self):
        s=settlements.ensure_for_order_item(self.db,1)
        s=settlements.set_terms(
            self.db,s["id"],mode="payable",partner_amount_rub=7000,
            terms_source="agreement-2026",actor="admin"
        )
        self.assertEqual(s["status"],"ready")
        self.assertEqual(s["partner_amount_rub"],7000)

        s=settlements.accrue(self.db,s["id"],actor="admin")
        self.assertEqual(s["status"],"open")
        self.assertEqual(s["balance_rub"],-7000)

        s=settlements.record_payment(
            self.db,s["id"],direction="to_partner",amount_rub=3000,
            payment_ref="pay-1",actor="admin"
        )
        self.assertEqual(s["balance_rub"],-4000)
        self.assertEqual(s["status"],"open")

        s=settlements.record_payment(
            self.db,s["id"],direction="to_partner",amount_rub=4000,
            payment_ref="pay-2",actor="admin"
        )
        self.assertEqual(s["balance_rub"],0)
        self.assertEqual(s["status"],"settled")
        self.assertTrue(s["settled_at"])

    def test_direct_partner_receivable_and_dropship(self):
        s=settlements.ensure_for_order_item(self.db,2)
        self.assertEqual(s["payment_route"],"direct_partner")
        self.assertEqual(s["fulfillment_route"],"dropship")
        self.assertEqual(s["customer_total_rub"],12000)

        s=settlements.set_terms(
            self.db,s["id"],mode="receivable",partner_amount_rub=1500,
            terms_source="commission-terms",actor="admin"
        )
        s=settlements.accrue(self.db,s["id"],actor="admin")
        self.assertEqual(s["balance_rub"],1500)
        self.assertEqual(s["status"],"open")

        s=settlements.record_payment(
            self.db,s["id"],direction="from_partner",amount_rub=1500,
            payment_ref="commission-payout-1",actor="admin"
        )
        self.assertEqual(s["balance_rub"],0)
        self.assertEqual(s["status"],"settled")

    def test_wrong_financial_direction_is_blocked(self):
        s=settlements.ensure_for_order_item(self.db,1)
        with self.assertRaisesRegex(ValueError,"requires direct"):
            settlements.set_terms(
                self.db,s["id"],mode="receivable",partner_amount_rub=1000,
                terms_source="bad",actor="admin"
            )

        s2=settlements.ensure_for_order_item(self.db,2)
        with self.assertRaisesRegex(ValueError,"requires client payment"):
            settlements.set_terms(
                self.db,s2["id"],mode="payable",partner_amount_rub=1000,
                terms_source="bad",actor="admin"
            )

    def test_none_requires_explicit_terms_but_no_ledger(self):
        s=settlements.ensure_for_order_item(self.db,1)
        s=settlements.set_terms(
            self.db,s["id"],mode="none",partner_amount_rub=None,
            terms_source="explicit-no-settlement",actor="admin"
        )
        self.assertEqual(s["status"],"no_settlement")
        self.assertEqual(s["partner_amount_rub"],0)
        self.assertEqual(s["balance_rub"],0)
        with sqlite3.connect(self.db) as c:
            count=c.execute(
                "SELECT COUNT(*) FROM warehouse_partner_ledger WHERE settlement_id=?",
                (s["id"],),
            ).fetchone()[0]
        self.assertEqual(count,0)

    def test_accrual_is_idempotent(self):
        s=settlements.ensure_for_order_item(self.db,1)
        s=settlements.set_terms(
            self.db,s["id"],mode="payable",partner_amount_rub=7000,
            terms_source="agreement",actor="admin"
        )
        a=settlements.accrue(self.db,s["id"],actor="admin")
        b=settlements.accrue(self.db,s["id"],actor="admin")
        self.assertEqual(a["balance_rub"],-7000)
        self.assertEqual(b["balance_rub"],-7000)
        with sqlite3.connect(self.db) as c:
            count=c.execute(
                "SELECT COUNT(*) FROM warehouse_partner_ledger WHERE settlement_id=?",
                (s["id"],),
            ).fetchone()[0]
        self.assertEqual(count,1)

    def test_payment_idempotence_and_overpayment_guard(self):
        s=settlements.ensure_for_order_item(self.db,1)
        settlements.set_terms(
            self.db,s["id"],mode="payable",partner_amount_rub=5000,
            terms_source="agreement",actor="admin"
        )
        settlements.accrue(self.db,s["id"],actor="admin")

        a=settlements.record_payment(
            self.db,s["id"],direction="to_partner",amount_rub=2000,
            payment_ref="same-ref",actor="admin"
        )
        b=settlements.record_payment(
            self.db,s["id"],direction="to_partner",amount_rub=2000,
            payment_ref="same-ref",actor="admin"
        )
        self.assertEqual(a["balance_rub"],-3000)
        self.assertEqual(b["balance_rub"],-3000)
        with self.assertRaisesRegex(ValueError,"exceeds"):
            settlements.record_payment(
                self.db,s["id"],direction="to_partner",amount_rub=4000,
                payment_ref="too-much",actor="admin"
            )

    def test_adjustment_is_append_only(self):
        s=settlements.ensure_for_order_item(self.db,1)
        settlements.set_terms(
            self.db,s["id"],mode="payable",partner_amount_rub=5000,
            terms_source="agreement",actor="admin"
        )
        settlements.accrue(self.db,s["id"],actor="admin")
        s=settlements.record_adjustment(
            self.db,s["id"],signed_amount_rub=500,
            reason="Скидка партнёра",reference_id="adj-1",actor="admin"
        )
        self.assertEqual(s["balance_rub"],-4500)
        s2=settlements.record_adjustment(
            self.db,s["id"],signed_amount_rub=500,
            reason="Скидка партнёра",reference_id="adj-1",actor="admin"
        )
        self.assertEqual(s2["balance_rub"],-4500)

    def test_sync_registers_only_warehouse_items(self):
        result=settlements.sync_pending_from_orders(self.db,actor="admin")
        self.assertEqual(result["found"],2)
        self.assertEqual(result["created"],2)
        self.assertEqual(result["errors"],[])
        with sqlite3.connect(self.db) as c:
            rows=c.execute(
                "SELECT order_item_id FROM warehouse_partner_settlements ORDER BY order_item_id"
            ).fetchall()
        self.assertEqual(rows,[(1,),(2,)])

    def test_usa_item_is_rejected(self):
        with self.assertRaisesRegex(ValueError,"only to warehouse"):
            settlements.ensure_for_order_item(self.db,3)

    def test_warehouse_summary_sign_semantics(self):
        a=settlements.ensure_for_order_item(self.db,1)
        settlements.set_terms(
            self.db,a["id"],mode="payable",partner_amount_rub=7000,
            terms_source="A",actor="admin"
        )
        settlements.accrue(self.db,a["id"],actor="admin")

        b=settlements.ensure_for_order_item(self.db,2)
        settlements.set_terms(
            self.db,b["id"],mode="receivable",partner_amount_rub=1500,
            terms_source="B",actor="admin"
        )
        settlements.accrue(self.db,b["id"],actor="admin")

        summary={x["warehouse_id"]:x for x in settlements.warehouse_summary(self.db)}
        self.assertEqual(summary[self.w1]["balance_rub"],-7000)
        self.assertEqual(summary[self.w2]["balance_rub"],1500)


if __name__=="__main__":
    unittest.main(verbosity=2)
