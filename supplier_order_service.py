#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interface-neutral Supplier Orders service layer."""
from __future__ import annotations
import sqlite3
from collections import defaultdict
from datetime import datetime
import supplier_orders as repo
import supplier_customer_notify
import supplier_shortages
import warehouse_stock_service
import stock_engine

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

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError,ValueError):
            return None

    def _send_reservations(self,c,supplier_order_id):
        c.row_factory=sqlite3.Row
        order=c.execute("""SELECT id,warehouse_id,status FROM supplier_orders
          WHERE id=?""",(supplier_order_id,)).fetchone()
        if not order:
            raise KeyError("supplier order not found")
        if order["status"]!="not_sent":
            raise ValueError("supplier order is not ready to send")
        items=c.execute("""SELECT order_item_id,oem,quantity FROM supplier_order_items
          WHERE supplier_order_id=? ORDER BY id""",(supplier_order_id,)).fetchall()
        if not items:
            raise ValueError("supplier order has no items")
        resolved=[]
        now=datetime.now().astimezone()
        for item in items:
            if item["order_item_id"] is None:
                raise ValueError("supplier order item is not linked to client order item")
            matches=c.execute("""SELECT * FROM warehouse_stock_reservations
              WHERE order_item_id=?
                AND status IN ('hold','reserved','committed')
              ORDER BY id""",(item["order_item_id"],)).fetchall()
            if len(matches)!=1:
                raise ValueError(
                    f"warehouse reservation mismatch for order item {item['order_item_id']}"
                )
            reservation=matches[0]
            if int(reservation["warehouse_id"])!=int(order["warehouse_id"]):
                raise ValueError(
                    f"warehouse reservation points to another warehouse for order item {item['order_item_id']}"
                )
            if str(reservation["oem"] or "").strip()!=str(item["oem"] or "").strip():
                raise ValueError(
                    f"warehouse reservation OEM mismatch for order item {item['order_item_id']}"
                )
            if float(reservation["quantity"] or 0)<float(item["quantity"] or 0):
                raise ValueError(
                    f"warehouse reservation quantity is insufficient for order item {item['order_item_id']}"
                )
            if reservation["status"]=="hold":
                expires=self._parse_dt(reservation["expires_at"])
                if expires is not None and expires<=now:
                    raise ValueError(
                        f"warehouse reservation hold expired for order item {item['order_item_id']}"
                    )
            resolved.append(dict(reservation))
        return dict(order),resolved

    def begin_send_attempt(self,supplier_order_id,channel):
        with sqlite3.connect(self.db_file,timeout=30) as c:
            c.row_factory=sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            self._send_reservations(c,supplier_order_id)
            return repo.begin_delivery_attempt(
                self.db_file,supplier_order_id,channel,conn=c
            )

    def fail_send_attempt(self,attempt_id,error):
        repo.mark_delivery_attempt_failed(self.db_file,attempt_id,error)

    def accept_send_receipt(self,attempt_id,receipt):
        repo.mark_delivery_attempt_accepted(self.db_file,attempt_id,receipt)

    def finalize_send_attempt(self,supplier_order_id,attempt_id):
        with sqlite3.connect(self.db_file,timeout=30) as c:
            c.row_factory=sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            attempt=c.execute("""SELECT * FROM supplier_delivery_attempts
              WHERE id=? AND supplier_order_id=?""",(attempt_id,supplier_order_id)).fetchone()
            if not attempt:
                raise KeyError("supplier delivery attempt not found")
            if attempt["state"]!="accepted" or not str(attempt["provider_receipt"] or "").strip():
                raise ValueError("supplier delivery attempt is not accepted")
            order,reservations=self._send_reservations(c,supplier_order_id)
            for reservation in reservations:
                if reservation["status"]=="committed":
                    continue
                if not stock_engine.commit_reservation_in_transaction(
                    c,int(reservation["id"])
                ):
                    raise ValueError(
                        f"failed to commit warehouse reservation {reservation['id']}"
                    )
            details=(
                f"channel={attempt['channel']}; "
                f"receipt={attempt['provider_receipt']}"
            )
            repo.transition(
                self.db_file,supplier_order_id,"sent",details,conn=c
            )
            repo.mark_delivery_attempt_sent_conn(c,attempt_id)
            c.commit()
        return self.get(supplier_order_id)

    def get_send_attempt(self,attempt_id):
        return repo.get_delivery_attempt(self.db_file,attempt_id)

    def change_status(self, supplier_order_id, status, details=None):
        # SENT is not a generic status transition. It is proof that an external
        # supplier channel accepted the message and reservations were committed.
        if status=="sent":
            raise ValueError("supplier order may become sent only through delivery")
        if status in ("confirmed","assembled","shipped","delivered"):
            guard=supplier_shortages.lifecycle_state(self.db_file,supplier_order_id)
            if guard["blocked"]:
                raise ValueError("⚠️ Есть недопоставка — ожидается решение клиента")
        repo.transition(self.db_file,supplier_order_id,status,details)
        return self.get(supplier_order_id)

    def report_item_problem(self,supplier_order_id,item_id,confirmed_qty):
        order=self.get(supplier_order_id)
        item=next((x for x in order["items"] if int(x["id"])==int(item_id)),None)
        if not item:
            raise ValueError("position does not belong to supplier order")
        enriched=dict(item,client_order_id=order["client_order_id"],warehouse_id=order["warehouse_id"])
        shortage=supplier_shortages.create(self.db_file,supplier_order_id,enriched,confirmed_qty)
        try:
            all_stock=warehouse_stock_service.client_stock_summary(item["oem"],self.db_file)
        except Exception:
            all_stock=[]
        alternatives=[x for x in all_stock if int(x["warehouse_id"])!=int(order["warehouse_id"])]
        shortage=supplier_shortages.update_context(self.db_file,shortage["id"],alternatives=alternatives)
        cover=sum(float(x.get("available_quantity") or 0) for x in alternatives if x.get("status")=="in_stock" and x.get("is_fresh"))
        if cover < float(shortage["shortage_qty"]):
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
        repo.set_tracking(self.db_file,supplier_order_id,carrier,tracking_number)
        order=self.get(supplier_order_id)
        try:
            receipt=supplier_customer_notify.notify(self,order,client_sender)
            repo.add_event(self.db_file,supplier_order_id,"client_tracking_notified",details=str(receipt))
        except Exception as exc:
            repo.add_event(self.db_file,supplier_order_id,"client_tracking_notify_failed",details=str(exc))
        return self.get(supplier_order_id)

    def batch_send(self, supplier_order_ids):
        raise ValueError("use supplier_delivery.send_batch for real supplier delivery")

    def grouped_queue(self):
        groups=defaultdict(lambda:{"orders":0,"item_lines":0,"units":0.0,"shipments":[]})
        for row in self.list_work_queue():
            key=row["public_name"]
            g=groups[key]; g["orders"]+=1; g["item_lines"]+=int(row["item_lines"])
            g["units"]+=float(row["units"]); g["shipments"].append(row)
        return dict(groups)
