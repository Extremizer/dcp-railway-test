#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Interface-neutral Supplier Orders service layer."""
from __future__ import annotations

import sqlite3
from collections import defaultdict
from datetime import datetime

import fulfillment_slices
import stock_engine
import supplier_customer_notify
import supplier_orders as repo
import supplier_shortages
import warehouse_stock_service


class SupplierOrderService:
    def __init__(self, db_file):
        self.db_file = db_file
        repo.init_supplier_orders_db(db_file)
        supplier_shortages.init(db_file)
        fulfillment_slices.init(db_file)

    def create_from_client_order(self, client_order_id, recipient):
        """Create one supplier shipment per warehouse. USA lines are excluded."""
        created = []
        with sqlite3.connect(self.db_file) as c:
            c.row_factory = sqlite3.Row
            items = c.execute(
                """SELECT id,manufacturer,oem,name,quantity,offer_source,warehouse_id
                   FROM order_items
                   WHERE order_id=?
                   ORDER BY position,id""",
                (client_order_id,),
            ).fetchall()
        groups = defaultdict(list)
        for x in items:
            if str(x["offer_source"] or "usa").lower() != "warehouse":
                continue
            if x["warehouse_id"] is None:
                raise ValueError(f"warehouse item {x['id']} has no warehouse")
            groups[int(x["warehouse_id"])].append(x)

        for warehouse_id, lines in groups.items():
            sid = repo.ensure_supplier_order(
                self.db_file, client_order_id, warehouse_id, recipient
            )
            for x in lines:
                repo.add_item(
                    self.db_file,
                    sid,
                    order_item_id=x["id"],
                    manufacturer=x["manufacturer"],
                    oem=x["oem"],
                    name=x["name"],
                    quantity=x["quantity"],
                )
            fulfillment_slices.ensure_supplier_order_roots(
                self.db_file,
                sid,
                actor_type="system",
                source="supplier_order_create",
            )
            created.append(self.get(sid))
        return created

    def get(self, supplier_order_id):
        x = repo.supplier_payload(self.db_file, supplier_order_id)
        guard = supplier_shortages.lifecycle_state(
            self.db_file, supplier_order_id
        )
        x["shortage_state"] = guard["label"]
        x["has_unresolved_shortage"] = guard["blocked"]
        x["fulfillment_slices"] = fulfillment_slices.list_for_supplier_order(
            self.db_file, supplier_order_id
        )
        return x

    def list_work_queue(self):
        return repo.pending_summary(self.db_file)

    @staticmethod
    def _parse_dt(value):
        if not value:
            return None
        try:
            return datetime.fromisoformat(str(value))
        except (TypeError, ValueError):
            return None

    def _send_reservations(self, c, supplier_order_id):
        c.row_factory = sqlite3.Row
        order = c.execute(
            """SELECT id,warehouse_id,status
               FROM supplier_orders WHERE id=?""",
            (supplier_order_id,),
        ).fetchone()
        if not order:
            raise KeyError("supplier order not found")
        if order["status"] != "not_sent":
            raise ValueError("supplier order is not ready to send")

        items = c.execute(
            """SELECT order_item_id,oem,quantity
               FROM supplier_order_items
               WHERE supplier_order_id=?
               ORDER BY id""",
            (supplier_order_id,),
        ).fetchall()
        if not items:
            raise ValueError("supplier order has no items")

        resolved = []
        now = datetime.now().astimezone()
        for item in items:
            if item["order_item_id"] is None:
                raise ValueError(
                    "supplier order item is not linked to client order item"
                )
            matches = c.execute(
                """SELECT *
                   FROM warehouse_stock_reservations
                   WHERE order_item_id=?
                     AND status IN ('hold','reserved','committed')
                   ORDER BY id""",
                (item["order_item_id"],),
            ).fetchall()
            if len(matches) != 1:
                raise ValueError(
                    f"warehouse reservation mismatch for order item "
                    f"{item['order_item_id']}"
                )
            reservation = matches[0]
            if int(reservation["warehouse_id"]) != int(order["warehouse_id"]):
                raise ValueError(
                    "warehouse reservation points to another warehouse "
                    f"for order item {item['order_item_id']}"
                )
            if str(reservation["oem"] or "").strip() != str(
                item["oem"] or ""
            ).strip():
                raise ValueError(
                    "warehouse reservation OEM mismatch for order item "
                    f"{item['order_item_id']}"
                )
            if float(reservation["quantity"] or 0) < float(
                item["quantity"] or 0
            ):
                raise ValueError(
                    "warehouse reservation quantity is insufficient "
                    f"for order item {item['order_item_id']}"
                )
            if reservation["status"] == "hold":
                expires = self._parse_dt(reservation["expires_at"])
                if expires is not None and expires <= now:
                    raise ValueError(
                        "warehouse reservation hold expired for order item "
                        f"{item['order_item_id']}"
                    )
            resolved.append(dict(reservation))
        return dict(order), resolved

    def begin_send_attempt(self, supplier_order_id, channel):
        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            self._send_reservations(c, supplier_order_id)
            return repo.begin_delivery_attempt(
                self.db_file,
                supplier_order_id,
                channel,
                conn=c,
            )

    def fail_send_attempt(self, attempt_id, error):
        repo.mark_delivery_attempt_failed(self.db_file, attempt_id, error)

    def accept_send_receipt(self, attempt_id, receipt):
        repo.mark_delivery_attempt_accepted(
            self.db_file, attempt_id, receipt
        )

    def finalize_send_attempt(self, supplier_order_id, attempt_id):
        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            attempt = c.execute(
                """SELECT *
                   FROM supplier_delivery_attempts
                   WHERE id=? AND supplier_order_id=?""",
                (attempt_id, supplier_order_id),
            ).fetchone()
            if not attempt:
                raise KeyError("supplier delivery attempt not found")
            if attempt["state"] != "accepted" or not str(
                attempt["provider_receipt"] or ""
            ).strip():
                raise ValueError(
                    "supplier delivery attempt is not accepted"
                )

            _order, reservations = self._send_reservations(
                c, supplier_order_id
            )
            for reservation in reservations:
                if reservation["status"] == "committed":
                    continue
                if not stock_engine.commit_reservation_in_transaction(
                    c, int(reservation["id"])
                ):
                    raise ValueError(
                        "failed to commit warehouse reservation "
                        f"{reservation['id']}"
                    )

            details = (
                f"channel={attempt['channel']}; "
                f"receipt={attempt['provider_receipt']}"
            )
            fulfillment_slices.mark_supplier_sent_conn(
                c,
                supplier_order_id,
                actor_type="system",
                source="supplier_delivery",
                details={
                    "channel": attempt["channel"],
                    "provider_receipt": attempt["provider_receipt"],
                },
            )
            repo.transition(
                self.db_file,
                supplier_order_id,
                "sent",
                details,
                conn=c,
            )
            repo.mark_delivery_attempt_sent_conn(c, attempt_id)
            c.commit()
        return self.get(supplier_order_id)

    def get_send_attempt(self, attempt_id):
        return repo.get_delivery_attempt(self.db_file, attempt_id)

    def confirm_supplier_order(
        self,
        supplier_order_id,
        *,
        actor_type="admin",
        actor_id=None,
        source="supplier_admin",
    ):
        guard = supplier_shortages.lifecycle_state(
            self.db_file, supplier_order_id
        )
        if guard["blocked"]:
            raise ValueError(
                "⚠️ Есть недопоставка — ожидается решение клиента"
            )

        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT status FROM supplier_orders WHERE id=?",
                (supplier_order_id,),
            ).fetchone()
            if not row:
                raise KeyError("supplier order not found")
            if row["status"] == "confirmed":
                c.rollback()
                return self.get(supplier_order_id)
            if row["status"] != "sent":
                raise ValueError(
                    "supplier order is not ready for warehouse confirmation"
                )

            fulfillment_slices.ensure_supplier_order_roots_conn(
                c,
                supplier_order_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )
            active = fulfillment_slices.active_rf_count_conn(
                c, supplier_order_id
            )
            if active <= 0:
                raise ValueError(
                    "supplier order has no active RF fulfillment"
                )
            fulfillment_slices.confirm_supplier_order_conn(
                c,
                supplier_order_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )
            repo.transition(
                self.db_file,
                supplier_order_id,
                "confirmed",
                source,
                conn=c,
            )
            c.commit()
        return self.get(supplier_order_id)

    def assemble_supplier_order(
        self,
        supplier_order_id,
        *,
        actor_type="admin",
        actor_id=None,
        source="supplier_admin",
    ):
        guard = supplier_shortages.lifecycle_state(
            self.db_file, supplier_order_id
        )
        if guard["blocked"]:
            raise ValueError(
                "⚠️ Есть недопоставка — ожидается решение клиента"
            )

        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT status FROM supplier_orders WHERE id=?",
                (supplier_order_id,),
            ).fetchone()
            if not row:
                raise KeyError("supplier order not found")
            if row["status"] == "assembled":
                c.rollback()
                return self.get(supplier_order_id)
            if row["status"] != "confirmed":
                raise ValueError(
                    "supplier order is not ready for assembly"
                )

            active = fulfillment_slices.active_rf_count_conn(
                c, supplier_order_id
            )
            if active <= 0:
                raise ValueError(
                    "supplier order has no active RF fulfillment"
                )
            fulfillment_slices.assemble_supplier_order_conn(
                c,
                supplier_order_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )
            repo.transition(
                self.db_file,
                supplier_order_id,
                "assembled",
                source,
                conn=c,
            )
            c.commit()
        return self.get(supplier_order_id)

    def change_status(
        self,
        supplier_order_id,
        status,
        details=None,
        *,
        actor_type="admin",
        actor_id=None,
        source=None,
    ):
        source = source or str(details or "supplier_admin")

        if status == "sent":
            raise ValueError(
                "supplier order may become sent only through delivery"
            )
        if status == "confirmed":
            return self.confirm_supplier_order(
                supplier_order_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )
        if status == "assembled":
            return self.assemble_supplier_order(
                supplier_order_id,
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )

        if status in ("shipped", "delivered"):
            guard = supplier_shortages.lifecycle_state(
                self.db_file, supplier_order_id
            )
            if guard["blocked"]:
                raise ValueError(
                    "⚠️ Есть недопоставка — ожидается решение клиента"
                )
        repo.transition(
            self.db_file, supplier_order_id, status, details
        )
        return self.get(supplier_order_id)

    def _reservation_for_item_conn(self, c, order_item_id):
        c.row_factory = sqlite3.Row
        rows = c.execute(
            """SELECT *
               FROM warehouse_stock_reservations
               WHERE order_item_id=?
                 AND status IN ('reserved','committed','absorbed')
               ORDER BY id""",
            (order_item_id,),
        ).fetchall()
        if len(rows) != 1:
            raise ValueError(
                f"warehouse reservation mismatch for order item "
                f"{order_item_id}"
            )
        return rows[0]

    def report_item_problem(
        self,
        supplier_order_id,
        item_id,
        confirmed_qty,
        *,
        actor_type="admin",
        actor_id=None,
        source="supplier_admin",
    ):
        supplier_shortages.init(self.db_file)
        fulfillment_slices.init(self.db_file)

        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            item = c.execute(
                """SELECT soi.id,soi.order_item_id,soi.manufacturer,soi.oem,
                          soi.name,soi.quantity,
                          so.client_order_id,so.warehouse_id,so.status
                   FROM supplier_order_items soi
                   JOIN supplier_orders so
                     ON so.id=soi.supplier_order_id
                   WHERE soi.id=? AND soi.supplier_order_id=?""",
                (item_id, supplier_order_id),
            ).fetchone()
            if not item:
                raise ValueError(
                    "position does not belong to supplier order"
                )
            if item["status"] != "sent":
                raise ValueError(
                    "warehouse shortage may be recorded only after send"
                )

            existing = c.execute(
                """SELECT id,confirmed_qty
                   FROM supplier_shortages
                   WHERE supplier_order_item_id=?""",
                (item_id,),
            ).fetchone()
            if existing:
                if abs(
                    float(existing["confirmed_qty"])
                    - float(confirmed_qty)
                ) < 1e-9:
                    shortage_id = int(existing["id"])
                    c.rollback()
                    return supplier_shortages.get(
                        self.db_file, shortage_id
                    )
                raise ValueError(
                    "shortage already recorded with another "
                    "confirmed quantity"
                )

            reservation = self._reservation_for_item_conn(
                c, int(item["order_item_id"])
            )
            fulfillment_slices.split_shortage_conn(
                c,
                supplier_order_id,
                int(item_id),
                float(confirmed_qty),
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )
            stock_engine.reconcile_supplier_confirmation_in_transaction(
                c,
                int(reservation["id"]),
                float(confirmed_qty),
            )
            enriched = dict(item)
            shortage_id = supplier_shortages.create_in_transaction(
                c,
                supplier_order_id,
                enriched,
                float(confirmed_qty),
            )
            c.commit()

        order = self.get(supplier_order_id)
        shortage = supplier_shortages.get(
            self.db_file, shortage_id
        )
        try:
            all_stock = warehouse_stock_service.client_stock_summary(
                shortage["oem"], self.db_file
            )
        except Exception:
            all_stock = []
        alternatives = [
            x
            for x in all_stock
            if int(x["warehouse_id"])
            != int(shortage["source_warehouse_id"])
        ]
        shortage = supplier_shortages.update_context(
            self.db_file,
            shortage["id"],
            alternatives=alternatives,
        )
        cover = sum(
            float(x.get("available_quantity") or 0)
            for x in alternatives
            if x.get("status") == "in_stock"
            and x.get("is_fresh")
        )
        if cover < float(shortage["shortage_qty"]):
            shortage = supplier_shortages.update_context(
                self.db_file,
                shortage["id"],
                status="waiting_customer",
            )
            try:
                receipt = supplier_customer_notify.notify_shortage(
                    self, shortage, order["public_name"]
                )
                repo.add_event(
                    self.db_file,
                    supplier_order_id,
                    "shortage_client_notified",
                    details=str(receipt),
                )
            except Exception as exc:
                repo.add_event(
                    self.db_file,
                    supplier_order_id,
                    "shortage_client_notify_failed",
                    details=str(exc),
                )
        return supplier_shortages.get(
            self.db_file, shortage["id"]
        )

    def _cancel_supplier_if_no_rf_conn(
        self,
        c,
        supplier_order_id,
        *,
        details="no active RF fulfillment",
    ):
        if fulfillment_slices.active_rf_count_conn(
            c, supplier_order_id
        ) > 0:
            return False
        row = c.execute(
            "SELECT status FROM supplier_orders WHERE id=?",
            (supplier_order_id,),
        ).fetchone()
        if not row:
            raise KeyError("supplier order not found")
        if row[0] == "cancelled":
            return False
        if row[0] not in {"not_sent", "sent", "confirmed"}:
            raise ValueError(
                f"cannot close supplier order from {row[0]}"
            )
        repo.transition(
            self.db_file,
            supplier_order_id,
            "cancelled",
            details,
            conn=c,
        )
        return True

    def decide_shortage(
        self,
        shortage_id,
        decision,
        *,
        actor_type="client",
        actor_id=None,
        source="customer_decision",
    ):
        supplier_shortages.init(self.db_file)
        fulfillment_slices.init(self.db_file)
        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            raw = c.execute(
                "SELECT * FROM supplier_shortages WHERE id=?",
                (shortage_id,),
            ).fetchone()
            if not raw:
                raise KeyError(shortage_id)
            previous_decision = raw["customer_decision"]
            if previous_decision:
                if previous_decision == decision:
                    c.rollback()
                    return supplier_shortages.get(
                        self.db_file, shortage_id
                    )
                raise ValueError("shortage decision already recorded")

            link = c.execute(
                """SELECT order_item_id
                   FROM supplier_order_items
                   WHERE id=?""",
                (raw["supplier_order_item_id"],),
            ).fetchone()
            if not link:
                raise ValueError(
                    "supplier shortage is not linked to an order item"
                )

            supplier_shortages.decide_in_transaction(
                c, shortage_id, decision
            )
            if decision == "partial_refund":
                fulfillment_slices.resolve_partial_refund_conn(
                    c,
                    int(raw["supplier_order_item_id"]),
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source=source,
                )
            elif decision == "cancel_all":
                fulfillment_slices.resolve_cancel_all_conn(
                    c,
                    int(raw["supplier_order_item_id"]),
                    actor_type=actor_type,
                    actor_id=actor_id,
                    source=source,
                )
                reservation = c.execute(
                    """SELECT *
                       FROM warehouse_stock_reservations
                       WHERE order_item_id=?
                       ORDER BY id DESC LIMIT 1""",
                    (int(link["order_item_id"]),),
                ).fetchone()
                if reservation and reservation["status"] in {
                    "reserved","committed","absorbed"
                }:
                    stock_engine.reconcile_supplier_confirmation_in_transaction(
                        c, int(reservation["id"]), 0.0
                    )

            if decision in {"partial_refund", "cancel_all"}:
                self._cancel_supplier_if_no_rf_conn(
                    c,
                    int(raw["supplier_order_id"]),
                    details=f"shortage decision: {decision}",
                )
            c.commit()
        return supplier_shortages.get(
            self.db_file, shortage_id
        )

    def decide_usa_shortage_quote(
        self,
        shortage_id,
        accept,
        *,
        actor_type="client",
        actor_id=None,
        source="customer_decision",
    ):
        supplier_shortages.init(self.db_file)
        fulfillment_slices.init(self.db_file)
        with sqlite3.connect(self.db_file, timeout=30) as c:
            c.row_factory = sqlite3.Row
            c.execute("BEGIN IMMEDIATE")
            raw = c.execute(
                "SELECT * FROM supplier_shortages WHERE id=?",
                (shortage_id,),
            ).fetchone()
            if not raw:
                raise KeyError(shortage_id)
            previous_usa = str(raw["usa_request_status"] or "")
            expected_previous = "accepted" if accept else "declined"
            if previous_usa in {"accepted","declined"}:
                if previous_usa == expected_previous:
                    c.rollback()
                    return supplier_shortages.get(
                        self.db_file, shortage_id
                    )
                raise ValueError("USA shortage quote decision already recorded")

            supplier_shortages.decide_usa_quote_in_transaction(
                c, shortage_id, bool(accept)
            )
            fulfillment_slices.resolve_usa_quote_conn(
                c,
                int(raw["supplier_order_item_id"]),
                bool(accept),
                actor_type=actor_type,
                actor_id=actor_id,
                source=source,
            )
            self._cancel_supplier_if_no_rf_conn(
                c,
                int(raw["supplier_order_id"]),
                details=(
                    "shortage rerouted to USA"
                    if accept
                    else "USA shortage quote declined"
                ),
            )
            c.commit()
        return supplier_shortages.get(
            self.db_file, shortage_id
        )

    def add_tracking(
        self,
        supplier_order_id,
        carrier,
        tracking_number,
        client_sender=None,
    ):
        repo.set_tracking(
            self.db_file,
            supplier_order_id,
            carrier,
            tracking_number,
        )
        order = self.get(supplier_order_id)
        try:
            receipt = supplier_customer_notify.notify(
                self, order, client_sender
            )
            repo.add_event(
                self.db_file,
                supplier_order_id,
                "client_tracking_notified",
                details=str(receipt),
            )
        except Exception as exc:
            repo.add_event(
                self.db_file,
                supplier_order_id,
                "client_tracking_notify_failed",
                details=str(exc),
            )
        return self.get(supplier_order_id)

    def batch_send(self, supplier_order_ids):
        raise ValueError(
            "use supplier_delivery.send_batch for real supplier delivery"
        )

    def grouped_queue(self):
        groups = defaultdict(
            lambda: {
                "orders": 0,
                "item_lines": 0,
                "units": 0.0,
                "shipments": [],
            }
        )
        for row in self.list_work_queue():
            key = row["public_name"]
            g = groups[key]
            g["orders"] += 1
            g["item_lines"] += int(row["item_lines"])
            g["units"] += float(row["units"])
            g["shipments"].append(row)
        return dict(groups)
