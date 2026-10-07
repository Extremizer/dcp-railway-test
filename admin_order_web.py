#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Read-only WEB ADMIN rendering for client orders."""

from __future__ import annotations

from html import escape
from typing import Any


ORDER_STATUS_LABELS = {
    "new": "Новый",
    "confirmed": "Подтверждён",
    "executing": "В исполнении",
    "completed": "Завершён",
    "cancelled": "Отменён",
}

RESERVATION_STATUS_LABELS = {
    "hold": "HOLD",
    "reserved": "Резерв",
    "committed": "Передан складу",
    "absorbed": "Учтён остатком",
    "released": "Снят",
}

SUPPLIER_STATUS_LABELS = {
    "not_sent": "Не отправлен",
    "sent": "Отправлен складу",
    "confirmed": "Подтверждён складом",
    "assembled": "Собран",
    "shipped": "Отправлен клиенту",
    "delivered": "Доставлен",
}


def _e(value: Any) -> str:
    if value is None or value == "":
        return "—"
    return escape(str(value))


def _money_rub(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "—"
    return f"{number:,.0f}".replace(",", " ") + " ₽"


def _qty(value: Any) -> str:
    try:
        return f"{float(value):g}"
    except (TypeError, ValueError):
        return _e(value)


def _source_label(item: dict[str, Any]) -> str:
    source = str(item.get("offer_source") or "usa").lower()
    if source == "warehouse":
        return _e(item.get("warehouse_public_name") or "Склад")
    return "США"


def _reservation_label(item: dict[str, Any]) -> str:
    rows = item.get("reservations") or []
    if not rows:
        return "—"
    parts = []
    for row in rows:
        status = str(row.get("status") or "")
        label = RESERVATION_STATUS_LABELS.get(status, status or "—")
        parts.append(f"{_e(label)} × {_qty(row.get('quantity'))}")
    return "<br>".join(parts)


def _supplier_label(item: dict[str, Any], supplier_orders: list[dict[str, Any]]) -> str:
    supplier_by_id = {int(x["id"]): x for x in supplier_orders if x.get("id") is not None}
    rows = item.get("supplier_items") or []
    if not rows:
        return "—"
    parts = []
    for row in rows:
        supplier = supplier_by_id.get(int(row.get("supplier_order_id") or 0), {})
        status = str(supplier.get("status") or "")
        label = SUPPLIER_STATUS_LABELS.get(status, status or "—")
        parts.append(f"#{_e(row.get('supplier_order_id'))} · {_e(label)}")
    return "<br>".join(parts)


def render_order_card(snapshot: dict[str, Any]) -> str:
    order = snapshot["order"]
    items = snapshot["items"]
    supplier_orders = snapshot["supplier_orders"]
    readiness = snapshot["execution_readiness"]

    order_status = str(order.get("status") or "")
    status_label = ORDER_STATUS_LABELS.get(order_status, order_status or "—")
    readiness_class = "ok" if readiness.get("ready") else "warn"
    readiness_text = (
        "Готов к принятию в исполнение"
        if readiness.get("ready")
        else "Есть проблемы, мешающие принять заказ в исполнение"
    )

    item_rows = []
    for item in items:
        unit_price = item.get("customer_unit_rub")
        if unit_price is None:
            unit_price = item.get("price_snapshot_rub")
        try:
            line_price = float(unit_price) * float(item.get("quantity") or 0)
        except (TypeError, ValueError):
            line_price = None

        item_rows.append(
            f"""<tr>
              <td>{_e(item.get('position'))}</td>
              <td><b>{_e(item.get('oem'))}</b><small>{_e(item.get('manufacturer'))}</small></td>
              <td>{_e(item.get('name'))}</td>
              <td>{_qty(item.get('quantity'))}</td>
              <td>{_source_label(item)}</td>
              <td>{_money_rub(unit_price)}</td>
              <td>{_money_rub(line_price)}</td>
              <td>{_qty(item.get('available_snapshot')) if item.get('available_snapshot') is not None else '—'}</td>
              <td>{_reservation_label(item)}</td>
              <td>{_supplier_label(item, supplier_orders)}</td>
            </tr>"""
        )

    supplier_blocks = []
    for supplier in supplier_orders:
        supplier_status = str(supplier.get("status") or "")
        supplier_label = SUPPLIER_STATUS_LABELS.get(supplier_status, supplier_status or "—")
        delivery = " · ".join(
            str(x)
            for x in (
                supplier.get("recipient_city"),
                supplier.get("delivery_method"),
                supplier.get("delivery_address") or supplier.get("pickup_point"),
            )
            if x
        )
        tracking = " · ".join(
            str(x)
            for x in (supplier.get("carrier"), supplier.get("tracking_number"))
            if x
        )
        supplier_blocks.append(
            f"""<div class="supplier">
              <div class="supplier-head">
                <b>Supplier Order #{_e(supplier.get('id'))}</b>
                <span>{_e(supplier_label)}</span>
              </div>
              <div class="grid">
                <div><small>Склад</small><strong>{_e(supplier.get('warehouse_public_name_current'))}</strong></div>
                <div><small>Получатель</small><strong>{_e(supplier.get('recipient_name'))}</strong></div>
                <div><small>Телефон</small><strong>{_e(supplier.get('recipient_phone'))}</strong></div>
                <div><small>Доставка</small><strong>{_e(delivery)}</strong></div>
                <div><small>Трекинг</small><strong>{_e(tracking)}</strong></div>
              </div>
            </div>"""
        )

    problems = readiness.get("problems") or []
    problem_html = ""
    if problems:
        rows = "".join(
            f"<li>Позиция {_e(p.get('position'))}, OEM <b>{_e(p.get('oem'))}</b>: "
            f"нужно {_qty(p.get('required_quantity'))}, покрыто {_qty(p.get('covered_quantity'))}</li>"
            for p in problems
        )
        problem_html = f'<ul class="problems">{rows}</ul>'

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(order.get('order_id'))} · WEB ADMIN</title>
<style>
body{{font-family:Arial,sans-serif;background:#f3f5f7;margin:0;color:#18202a}}
main{{max-width:1500px;margin:24px auto;padding:0 18px}}
a{{color:inherit}}
.top{{display:flex;justify-content:space-between;gap:20px;align-items:flex-start;margin-bottom:16px}}
.top h1{{margin:6px 0 4px;font-size:30px}}
.muted{{color:#667085;font-size:13px}}
.badge{{display:inline-block;padding:7px 10px;border-radius:999px;background:#e8edf2;font-weight:700}}
.card{{background:#fff;border-radius:14px;padding:18px;margin:14px 0;box-shadow:0 2px 10px #0000000d}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px}}
.grid div{{padding:10px 0}}
.grid small{{display:block;color:#667085;margin-bottom:4px}}
.grid strong{{display:block;word-break:break-word}}
.ready{{border-left:6px solid #98a2b3}}
.ready.ok{{border-left-color:#12b76a}}
.ready.warn{{border-left-color:#f79009}}
.ready h3{{margin:0 0 6px}}
table{{width:100%;border-collapse:collapse;font-size:14px}}
th,td{{padding:10px 9px;text-align:left;vertical-align:top;border-bottom:1px solid #eaecf0}}
th{{color:#475467;font-size:12px;background:#f9fafb;position:sticky;top:0}}
td small{{display:block;color:#667085;margin-top:3px}}
.table-wrap{{overflow:auto}}
.supplier{{border:1px solid #e4e7ec;border-radius:12px;padding:14px;margin-top:10px}}
.supplier-head{{display:flex;justify-content:space-between;gap:12px}}
.supplier-head span{{font-weight:700}}
.problems{{margin-bottom:0}}
.nav{{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:8px}}
.nav a{{text-decoration:none;font-weight:700}}
@media(max-width:700px){{.top{{display:block}}table{{min-width:1100px}}}}
</style>
</head>
<body>
<main>
  <div class="nav">
    <a href="/admin/supplier-orders">📦 Supplier Orders</a>
    <a href="/admin/supplier-channels">⚙️ Каналы складов</a>
  </div>

  <div class="top">
    <div>
      <div class="muted">Клиентский заказ · только чтение</div>
      <h1>{_e(order.get('order_id'))}</h1>
      <div class="muted">Создан: {_e(order.get('created_at'))} · Источник: {_e(order.get('origin'))}</div>
    </div>
    <span class="badge">{_e(status_label)}</span>
  </div>

  <section class="card">
    <div class="grid">
      <div><small>Клиент</small><strong>{_e(order.get('customer_name'))}</strong></div>
      <div><small>Username</small><strong>@{_e(order.get('username')) if order.get('username') else '—'}</strong></div>
      <div><small>Telegram ID</small><strong>{_e(order.get('telegram_user_id'))}</strong></div>
      <div><small>Сумма клиенту</small><strong>{_money_rub(order.get('customer_total_rub'))}</strong></div>
      <div><small>Позиций</small><strong>{len(items)}</strong></div>
      <div><small>Складских позиций</small><strong>{_e(readiness.get('warehouse_item_count'))}</strong></div>
    </div>
  </section>

  <section class="card ready {readiness_class}">
    <h3>{_e(readiness_text)}</h3>
    <div class="muted">Покрыто резервами: {_e(readiness.get('warehouse_ready_count'))} из {_e(readiness.get('warehouse_item_count'))} складских позиций.</div>
    {problem_html}
  </section>

  <section class="card">
    <h2>Позиции заказа</h2>
    <div class="table-wrap">
      <table>
        <thead><tr>
          <th>№</th><th>OEM</th><th>Название</th><th>Кол-во</th><th>Источник</th>
          <th>Цена / шт.</th><th>Сумма</th><th>Остаток snapshot</th><th>Резерв</th><th>Supplier Order</th>
        </tr></thead>
        <tbody>{''.join(item_rows)}</tbody>
      </table>
    </div>
  </section>

  <section class="card">
    <h2>Supplier Orders</h2>
    {''.join(supplier_blocks) if supplier_blocks else '<div class="muted">Supplier Orders для этого заказа пока нет.</div>'}
  </section>
</main>
</body>
</html>"""
