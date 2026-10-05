#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Full WEB ADMIN UI for Supplier Orders. Business rules stay in service."""
from __future__ import annotations
from html import escape
from supplier_orders import STATUS_LABELS

FILTERS=(("all","Все"),("need_send","Нужно отправить"),("working","В работе"),("shipped","Отправлено"),("delivered","Доставлено"))

def _e(v): return escape(str(v or ""))
def _filter_status(name):
    return {"need_send":{"not_sent"},"working":{"sent","confirmed","assembled"},"shipped":{"shipped"},"delivered":{"delivered"}}.get(name)

def render_queue(service,filter_name="all"):
    statuses=_filter_status(filter_name)
    rows=service.list_work_queue()
    if statuses is not None: rows=[x for x in rows if x["status"] in statuses]
    groups={}
    for x in rows:
        g=groups.setdefault(x["public_name"],[])
        g.append(x)
    tabs="".join(f'<a class="tab {"active" if k==filter_name else ""}" href="/admin/supplier-orders?filter={k}">{v}</a>' for k,v in FILTERS)
    out=[f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Заказы поставщикам</title><link rel="stylesheet" href="/static/supplier-admin.css"></head><body><main><header><h1>📦 Заказы поставщикам</h1><nav>{tabs}<a class="tab" href="/admin/supplier-channels">⚙️ Каналы складов</a></nav></header>''']
    out.append(f'<div class="counter">Найдено: <b>{len(rows)}</b></div>')
    for wh,shipments in groups.items():
        lines=sum(int(x["item_lines"]) for x in shipments)
        out.append(f'<section class="warehouse"><div class="wh-head"><h2>{_e(wh)}</h2><span>{len(shipments)} заказ(а) / {lines} позиций</span></div>')
        ids=[str(x["id"]) for x in shipments if x["status"]=="not_sent"]
        if ids: out.append(f'<button class="primary batch" data-ids="{",".join(ids)}">📤 Отправить все {_e(wh)}</button>')
        for x in shipments:
            out.append(f'''<a class="order" href="/admin/supplier-orders/{x["id"]}">
              <div><b>{_e(x["client_order_id"])}</b><small>{_e(x.get("recipient_name"))} · {_e(x.get("recipient_city"))}</small></div>
              <div><span>{_e(STATUS_LABELS.get(x["status"],x["status"]))}</span><small>{x["item_lines"]} поз. · {x["units"]:g} шт.</small></div></a>''')
        out.append("</section>")
    if not rows: out.append('<div class="empty">В этом разделе заказов пока нет.</div>')
    out.append('<div id="msg"></div></main><script src="/static/supplier-admin.js"></script></body></html>')
    return "".join(out)

def render_card(order):
    label=STATUS_LABELS.get(order["status"],order["status"])
    items="".join(f'<tr><td><b>{_e(x["oem"])}</b><small>{_e(x.get("manufacturer"))}</small></td><td>{_e(x.get("name"))}</td><td>{x["quantity"]:g}</td><td><button class="problem" data-order="{order["id"]}" data-item="{x["id"]}" data-oem="{_e(x["oem"])}" data-qty="{x["quantity"]:g}">⚠️ Проблема с позицией</button></td></tr>' for x in order["items"])
    delivery=" · ".join(filter(None,[order.get("recipient_city"),order.get("delivery_method"),order.get("delivery_address") or order.get("pickup_point")]))
    next_button={"not_sent":("sent","📤 Отправлен складу"),"sent":("confirmed","👍 Подтверждён складом"),"confirmed":("assembled","📦 Собран"),"shipped":("delivered","✅ Доставлен")}.get(order["status"])
    action=f'<button class="primary status" data-id="{order["id"]}" data-status="{next_button[0]}">{next_button[1]}</button>' if next_button else ""
    tracking=""
    if order["status"]=="assembled":
        tracking=f'''<div class="tracking"><h3>Отправка клиенту</h3><input id="carrier" placeholder="Перевозчик, например СДЭК"><input id="tracking" placeholder="Трек-номер"><button class="primary track" data-id="{order["id"]}">🚚 Сохранить трек и отметить отправленным</button></div>'''
    elif order.get("tracking_number"):
        tracking=f'<div class="tracking done">🚚 {_e(order.get("carrier"))}: <b>{_e(order["tracking_number"])}</b></div>'
    return f'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>{_e(order["client_order_id"])}</title><link rel="stylesheet" href="/static/supplier-admin.css"></head><body><main>
      <a class="back" href="/admin/supplier-orders">← Заказы поставщикам</a>
      <section class="card"><div class="wh-head"><h1>{_e(order["public_name"])}</h1><span>{_e(label)}</span></div>
      <h2>{_e(order["client_order_id"])}</h2>
      <div class="recipient"><p><b>Получатель:</b> {_e(order.get("recipient_name"))}</p><p><b>Телефон:</b> {_e(order.get("recipient_phone"))}</p><p><b>Доставка:</b> {_e(delivery or "—")}</p></div>
      <table><thead><tr><th>OEM</th><th>Позиция</th><th>Кол-во</th><th></th></tr></thead><tbody>{items}</tbody></table>
      <div class="actions">{action}</div>{tracking}<div id="msg"></div></section>
      </main><script src="/static/supplier-admin.js"></script></body></html>'''
