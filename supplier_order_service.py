#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interface-neutral Supplier Orders service layer."""
from __future__ import annotations
import sqlite3
from collections import defaultdict
import supplier_orders as repo
import supplier_customer_notify
import supplier_shortages
import warehouse_stock_service

class SupplierOrderService:
    def __init__(self, db_file):
        self.db_file=db_file
        repo.init_supplier_orders_db(db_file)

    def create_from_client_order(self, client_order_id, recipient):
        """Create one supplier shipment per warehouse. USA lines are excluded."""
        created=[]
        with sqlite3.connect(self.db_file) as c:
            c.row_factory=sqlite3.Row
            items=c.execute("""SELECT id,manufacturer,oem,name,quantity,offer_source,warehouse_id
              FROM order_items WHERE order_id=? ORDER BY position,id""",(client_order_id,)).fetchall()
        groups=defaultdict(list)
        for x in items:
            if str(x["offer_source"] or "usa").lower()!="warehouse":
                continue
            if x["warehouse_id"] is None:
                raise ValueError(f"warehouse item {x['id']} has no warehouse")
            groups[int(x["warehouse_id"])].append(x)
        for warehouse_id, lines in groups.items():
            sid=repo.ensure_supplier_order(self.db_file,client_order_id,warehouse_id,recipient)
            for x in lines:
                repo.add_item(self.db_file,sid,order_item_id=x["id"],manufacturer=x["manufacturer"],
                              oem=x["oem"],name=x["name"],quantity=x["quantity"])
            created.append(repo.supplier_payload(self.db_file,sid))
        return created

    def get(self, supplier_order_id):
        x=repo.supplier_payload(self.db_file,supplier_order_id)
        guard=supplier_shortages.lifecycle_state(self.db_file,supplier_order_id)
        x["shortage_state"]=guard["label"]
        x["has_unresolved_shortage"]=guard["blocked"]
        return x

    def list_work_queue(self):
        return repo.pending_summary(self.db_file)

    def change_status(self, supplier_order_id, status, details=None):
        # Supplier business lifecycle cannot advance past a quantity dispute
        # until the customer has resolved it. Stock Engine lifecycle is untouched.
        if status in ("confirmed","assembled","shipped","delivered"):
            guard=supplier_shortages.lifecycle_state(self.db_file,supplier_order_id)
            if guard["blocked"]:
                raise ValueError("⚠️ Есть недопоставка — ожидается решение клиента")
        repo.transition(self.db_file,supplier_order_id,status,details)
        return self.get(supplier_order_id)

    def report_item_problem(self,supplier_order_id,item_id,confirmed_qty):
        order=self.get(supplier_order_id)
        item=next((x for x in order["items"] if int(x["id"])==int(item_id)),None)
        if not item: raise ValueError("position does not belong to supplier order")
        enriched=dict(item,client_order_id=order["client_order_id"],warehouse_id=order["warehouse_id"])
        shortage=supplier_shortages.create(self.db_file,supplier_order_id,enriched,confirmed_qty)
        # Automatic read-only check of every other active warehouse; no admin search button.
        try:
            all_stock=warehouse_stock_service.client_stock_summary(item["oem"],self.db_file)
        except Exception:
            # Legacy/minimal DBs may not yet have the full Warehouse schema.
            all_stock=[]
        alternatives=[x for x in all_stock if int(x["warehouse_id"])!=int(order["warehouse_id"])]
        shortage=supplier_shortages.update_context(self.db_file,shortage["id"],alternatives=alternatives)
        # Notify only when the missing quantity cannot be covered by any other Russian warehouse.
        cover=sum(float(x.get("available_quantity") or 0) for x in alternatives if x.get("status")=="in_stock" and x.get("is_fresh"))
        if cover < float(shortage["shortage_qty"]):
            # The problematic quantity is explicitly waiting for the customer.
            shortage=supplier_shortages.update_context(self.db_file,shortage["id"],status="waiting_customer")
            try:
                receipt=supplier_customer_notify.notify_shortage(self,shortage,order["public_name"])
                shortage=supplier_shortages.update_context(self.db_file,shortage["id"],status="waiting_customer")
                repo.add_event(self.db_file,supplier_order_id,"shortage_client_notified",details=str(receipt))
            except Exception as exc:
                repo.add_event(self.db_file,supplier_order_id,"shortage_client_notify_failed",details=str(exc))
        return shortage

    def decide_shortage(self,shortage_id,decision):
        return supplier_shortages.decide(self.db_file,shortage_id,decision)

    def add_tracking(self, supplier_order_id, carrier, tracking_number, client_sender=None):
        # Persist track + shipped status first; then notify the client from the same source of truth.
        repo.set_tracking(self.db_file,supplier_order_id,carrier,tracking_number)
        order=self.get(supplier_order_id)
        try:
            receipt=supplier_customer_notify.notify(self,order,client_sender)
            repo.add_event(self.db_file,supplier_order_id,"client_tracking_notified",details=str(receipt))
        except Exception as exc:
            # Shipment is already real; never roll status back. Record notification failure for retry.
            repo.add_event(self.db_file,supplier_order_id,"client_tracking_notify_failed",details=str(exc))
        return self.get(supplier_order_id)

    def batch_send(self, supplier_order_ids):
        result=[]
        for sid in supplier_order_ids:
            order=self.get(int(sid))
            if order["status"]!="not_sent":
                raise ValueError(f"supplier order {sid} is not ready to send")
        for sid in supplier_order_ids:
            result.append(self.change_status(int(sid),"sent","batch_send"))
        return result

    def grouped_queue(self):
        groups=defaultdict(lambda:{"orders":0,"item_lines":0,"units":0.0,"shipments":[]})
        for row in self.list_work_queue():
            key=row["public_name"]
            g=groups[key]; g["orders"]+=1; g["item_lines"]+=int(row["item_lines"])
            g["units"]+=float(row["units"]); g["shipments"].append(row)
        return dict(groups)
