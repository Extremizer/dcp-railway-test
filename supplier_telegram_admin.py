#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Telegram ADMIN adapter. Handlers call the same SupplierOrderService as WEB."""
from __future__ import annotations
from supplier_orders import STATUS_LABELS
import supplier_admin_auth

NEXT = {
 "not_sent":("sent","📤 Отправлен складу"),
 "sent":("confirmed","👍 Подтверждён складом"),
 "confirmed":("assembled","📦 Собран"),
 "shipped":("delivered","✅ Доставлен"),
}

def queue_text(service):
    groups=service.grouped_queue()
    total=sum(g["orders"] for g in groups.values())
    lines=[f"📦 Заказы поставщикам\n\n🔴 Нужно обработать — {total}"]
    for wh,g in groups.items():
        lines.append(f"\n<b>{wh}</b> — {g['orders']} заказ(а) / {g['item_lines']} позиций")
        for x in g["shipments"]:
            lines.append(f"<code>{x['client_order_id']}</code> · {STATUS_LABELS.get(x['status'],x['status'])}")
    return "\n".join(lines)

def card_text(service,supplier_order_id):
    x=service.get(supplier_order_id)
    lines=[f"<b>{x['public_name']} · {x['client_order_id']}</b>",STATUS_LABELS.get(x["status"],x["status"])]
    lines += [f"<code>{i['oem']}</code> × {i['quantity']:g}" for i in x["items"]]
    lines += ["",f"Получатель: {x.get('recipient_name') or '—'}",f"Телефон: {x.get('recipient_phone') or '—'}",
              f"Город: {x.get('recipient_city') or '—'}"]
    if x.get("tracking_number"): lines.append(f"🚚 {x.get('carrier')}: <code>{x['tracking_number']}</code>")
    return "\n".join(lines)

def handle_action(service,supplier_order_id,action,*,admin_user_id=None,carrier=None,tracking=None):
    """Thin handler target: auth + delegation, no duplicated business rules."""
    supplier_admin_auth.require_telegram_admin(admin_user_id)
    if action=="tracking":
        return service.add_tracking(supplier_order_id,carrier,tracking)
    if action=="next":
        current=service.get(supplier_order_id)["status"]
        if current=="assembled":
            raise ValueError("tracking required before shipped")
        target=NEXT.get(current)
        if not target: raise ValueError("no next Telegram action")
        return service.change_status(supplier_order_id,target[0],"telegram_admin")
    raise ValueError("unknown Telegram supplier action")
