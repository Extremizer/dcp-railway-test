"""Actual client checkout orchestration: RUB, atomic rollback, postcommit retry."""
import asyncio,sqlite3,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from oemixibot_finance import FinanceEngine
from client_finance_adapter import ClientFinanceAdapter
import client_checkout_orchestrator as core
import client_checkout_callback_adapter as callback

class ClientCheckoutRegression(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.db=str(Path(self.tmp.name)/"client.sqlite")
        with sqlite3.connect(self.db) as c:
            c.execute("CREATE TABLE synthetic_orders(id TEXT PRIMARY KEY, currency TEXT)")
            c.execute("CREATE TABLE synthetic_charges(order_id TEXT PRIMARY KEY, currency TEXT)")
        self.finance=ClientFinanceAdapter(FinanceEngine(self.db))
        self.user=SimpleNamespace(id=123)
        self.cart={"3211070":{"qty":1}}
    def tearDown(self):self.tmp.cleanup()
    def save(self,order_id,user,cart,*,delivery_preference,origin,conn):
        conn.execute("INSERT INTO synthetic_orders VALUES(?,?)",(order_id,"RUB"))
    def charge(self,adapter,*,conn,client_id,order_id,actor):
        self.assertIs(adapter,self.finance)
        conn.execute("INSERT INTO synthetic_charges VALUES(?,?)",(order_id,"RUB"))
        return 7
    def count(self,table):
        with sqlite3.connect(self.db) as c:
            return c.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
    def checkout(self,order):
        return core.persist_client_checkout_atomic(db_path=self.db,save_order_fn=self.save,
            finance_adapter=self.finance,order_id=order,user=self.user,cart=self.cart)
    def test_client_rub_atomic_success(self):
        with patch.object(core,"post_persisted_order_charge",side_effect=self.charge):
            result=self.checkout("C-1")
        self.assertEqual(result["finance_event_id"],7)
        self.assertEqual((self.count("synthetic_orders"),self.count("synthetic_charges")),(1,1))
        with sqlite3.connect(self.db) as c:
            self.assertEqual(c.execute("SELECT currency FROM synthetic_orders").fetchone()[0],"RUB")
            self.assertEqual(c.execute("SELECT currency FROM synthetic_charges").fetchone()[0],"RUB")
    def test_finance_failure_rolls_back_order(self):
        with patch.object(core,"post_persisted_order_charge",side_effect=ValueError("RUB capacity")):
            with self.assertRaisesRegex(ValueError,"RUB capacity"):self.checkout("C-FAILED")
        self.assertEqual((self.count("synthetic_orders"),self.count("synthetic_charges")),(0,0))
    def test_postcommit_failure_does_not_repeat_business_checkout(self):
        with patch.object(core,"post_persisted_order_charge",side_effect=self.charge):
            with self.assertRaises(callback.CheckoutPostCommitError) as caught:
                asyncio.run(callback.execute_prepared_checkout(db_path=self.db,
                    save_order_fn=self.save,finance_adapter=self.finance,order_id="C-POST",
                    user=self.user,cart=self.cart,
                    manager_notify=lambda: (_ for _ in ()).throw(RuntimeError("notify down"))))
        self.assertTrue(caught.exception.core_committed)
        self.assertEqual((self.count("synthetic_orders"),self.count("synthetic_charges")),(1,1))
        with patch.object(core,"post_persisted_order_charge",side_effect=self.charge):
            with self.assertRaises(sqlite3.IntegrityError):self.checkout("C-POST")
        self.assertEqual((self.count("synthetic_orders"),self.count("synthetic_charges")),(1,1))
if __name__=="__main__":unittest.main()
