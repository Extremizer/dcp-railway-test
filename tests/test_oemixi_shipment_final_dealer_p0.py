import os, sqlite3, tempfile, unittest
from unittest.mock import patch
from oemixibot_store import OemixiStore

class DealerFinalRegression(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store=OemixiStore(os.path.join(self.temp.name,"dealer.sqlite"))
        self.store.init()
        self.store.add_dealer("D_FINAL", 93114155, "Dealer Final")
        self.store.create_order("ORDER_FINAL","D_FINAL")
        self.root=self.store.add_item("ORDER_FINAL","OEM-A",3,status="Принята",unit_price_usd=12.5,line_total_usd=37.5)
        self.shipment=self.store.create_shipment("USA-FINAL","КОМФОРТ")

    def rows(self,sql,args=()):
        with sqlite3.connect(self.store.path) as db:
            return db.execute(sql,args).fetchall()

    def ledger(self):
        return self.rows("SELECT * FROM finance_ledger ORDER BY id")

    def test_existing_dealer_lifecycle_and_ledger_isolation(self):
        before=self.ledger()
        tracking=self.store.create_tracking("SUP-FINAL-1")
        self.store.assign_tracking(self.root,tracking)
        self.assertEqual(self.ledger(),before)
        self.store.mark_tracking_received_us(tracking)
        self.assertEqual(self.rows("SELECT status FROM item_slices WHERE id=?",(self.root,)),[("На складе США",)])
        part=self.store.add_to_shipment(self.shipment,self.root,2)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.rows("SELECT qty,line_total_usd FROM item_slices WHERE order_id='ORDER_FINAL' ORDER BY id"),[(1,12.5),(2,25.0)])
        self.store.depart_shipment(self.shipment)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.rows("SELECT status FROM usa_shipments WHERE id=?",(self.shipment,)),[("in_transit",)])
        self.assertEqual(self.rows("SELECT status FROM item_slices WHERE id=?",(part,)),[("Едет в Москву",)])
        receipt=self.store.receive_moscow_item(self.shipment,part,actual_weight=1.5,length=20,width=10,height=10)
        self.assertEqual(receipt["received_qty"],2)
        self.assertEqual(self.rows("SELECT status FROM item_slices WHERE id=?",(part,)),[("На складе в Москве",)])
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.rows("SELECT SUM(qty) FROM item_slices WHERE order_id='ORDER_FINAL'"),[(3,)])
        self.assertGreaterEqual(self.rows("SELECT COUNT(*) FROM item_status_history WHERE item_slice_id=?",(part,))[0][0],3)

    def test_ledger_unchanged_after_sql_faults(self):
        before=self.ledger()
        tracking=self.store.create_tracking("SUP-FAIL")
        original=self.store._set_status_conn
        def fault_status(conn,*args,**kwargs):
            original(conn,*args,**kwargs)
            raise RuntimeError("post-SQL tracking fault")
        with patch.object(self.store,"_set_status_conn",side_effect=fault_status):
            with self.assertRaises(RuntimeError):
                self.store.assign_tracking(self.root,tracking)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.rows("SELECT supplier_tracking_id FROM item_slices WHERE id=?",(self.root,)),[(None,)])
        self.store.set_status(self.root,"На складе США")
        original_split=self.store._split_item_conn
        def fault_split(conn,*args,**kwargs):
            original_split(conn,*args,**kwargs)
            raise RuntimeError("post-SQL allocation fault")
        with patch.object(self.store,"_split_item_conn",side_effect=fault_split):
            with self.assertRaises(RuntimeError):
                self.store.add_to_shipment(self.shipment,self.root,1)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.rows("SELECT qty FROM item_slices WHERE order_id='ORDER_FINAL'"),[(3,)])
        self.store.add_to_shipment(self.shipment,self.root,3)
        with patch.object(self.store,"_set_status_conn",side_effect=fault_status):
            with self.assertRaises(RuntimeError):
                self.store.depart_shipment(self.shipment)
        self.assertEqual(self.ledger(),before)
        self.assertEqual(self.rows("SELECT status FROM usa_shipments WHERE id=?",(self.shipment,)),[("draft",)])

if __name__=="__main__": unittest.main()
