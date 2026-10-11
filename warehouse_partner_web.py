#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WEB ADMIN renderer for warehouse partner settlements."""
from __future__ import annotations

from html import escape

import supplier_admin_auth
import warehouse_partner_settlements as settlements


STATUS_LABELS = {
    "pending_terms": "🟡 Нужны условия",
    "ready": "🟠 Готово к начислению",
    "open": "🔴 Открыт расчёт",
    "settled": "✅ Закрыт",
    "no_settlement": "⚪ Без взаиморасчёта",
    "cancelled": "❌ Отменён",
}

MODE_LABELS = {
    "pending_terms": "Не задано",
    "payable": "Мы должны складу",
    "receivable": "Склад должен нам",
    "none": "Нет взаиморасчёта",
}

PAYMENT_LABELS = {
    "extremizer_balance": "Клиент платит Extremizer",
    "direct_partner": "Клиент платит складу",
}

FULFILLMENT_LABELS = {
    "through_extremizer": "Через Extremizer",
    "dropship": "Dropshipping",
}


def _e(v):
    return escape(str(v if v is not None else ""))


def _money(v):
    if v is None:
        return "—"
    return f"{int(v):,} ₽".replace(",", " ")


def _balance_text(value):
    value = int(value or 0)
    if value < 0:
        return f"Мы должны: {_money(abs(value))}"
    if value > 0:
        return f"Нам должны: {_money(value)}"
    return "Баланс: 0 ₽"


def _csrf(session_cookie, scope, subject):
    return supplier_admin_auth.issue_scoped_admin_csrf_token(
        session_cookie, scope, str(subject)
    )


def _terms_form(row, session_cookie):
    if row["status"] not in {"pending_terms", "ready"}:
        return ""
    sid = int(row["id"])
    token = _csrf(session_cookie, "warehouse-settlement-terms", sid)
    mode = row["settlement_mode"]
    amount = "" if row["partner_amount_rub"] is None else int(row["partner_amount_rub"])
    source = row.get("terms_source") or ""
    notes = row.get("notes") or ""
    options=[]
    for value,label in (
        ("payable","Мы должны складу"),
        ("receivable","Склад должен нам"),
        ("none","Без взаиморасчёта"),
    ):
        selected=" selected" if mode==value else ""
        options.append(f'<option value="{value}"{selected}>{label}</option>')
    return f"""
    <details>
      <summary>Условия расчёта</summary>
      <form method="post" action="/admin/warehouse-settlements/{sid}/terms">
        <input type="hidden" name="csrf_token" value="{_e(token)}">
        <label>Тип
          <select name="mode">{''.join(options)}</select>
        </label>
        <label>Сумма партнёра, ₽
          <input name="partner_amount_rub" type="number" min="0" step="1" value="{_e(amount)}">
        </label>
        <label>Источник условий
          <input name="terms_source" value="{_e(source)}" required>
        </label>
        <label>Комментарий
          <input name="notes" value="{_e(notes)}">
        </label>
        <button type="submit">Сохранить условия</button>
      </form>
    </details>
    """


def _accrue_form(row, session_cookie):
    if row["status"] != "ready":
        return ""
    sid=int(row["id"])
    token=_csrf(session_cookie,"warehouse-settlement-accrue",sid)
    return f"""
    <form method="post" action="/admin/warehouse-settlements/{sid}/accrue">
      <input type="hidden" name="csrf_token" value="{_e(token)}">
      <button type="submit">Начислить взаиморасчёт</button>
    </form>
    """


def _payment_form(row, session_cookie):
    if row["status"] != "open":
        return ""
    sid=int(row["id"])
    balance=int(row.get("balance_rub") or 0)
    if balance < 0:
        direction="to_partner"
        label="Оплата складу"
        max_amount=abs(balance)
    elif balance > 0:
        direction="from_partner"
        label="Получено от склада"
        max_amount=balance
    else:
        return ""
    token=_csrf(session_cookie,"warehouse-settlement-payment",sid)
    return f"""
    <details>
      <summary>{label}</summary>
      <form method="post" action="/admin/warehouse-settlements/{sid}/payment">
        <input type="hidden" name="csrf_token" value="{_e(token)}">
        <input type="hidden" name="direction" value="{direction}">
        <label>Сумма, ₽
          <input name="amount_rub" type="number" min="1" max="{max_amount}" required>
        </label>
        <label>Номер/ссылка платежа
          <input name="payment_ref" required>
        </label>
        <button type="submit">{label}</button>
      </form>
    </details>
    """


def _adjustment_form(row, session_cookie):
    if row["status"] not in {"open","settled"}:
        return ""
    sid=int(row["id"])
    token=_csrf(session_cookie,"warehouse-settlement-adjustment",sid)
    return f"""
    <details>
      <summary>Корректировка</summary>
      <form method="post" action="/admin/warehouse-settlements/{sid}/adjustment">
        <input type="hidden" name="csrf_token" value="{_e(token)}">
        <label>Сумма со знаком, ₽
          <input name="signed_amount_rub" type="number" step="1" required>
        </label>
        <label>Причина
          <input name="reason" required>
        </label>
        <label>Уникальный номер корректировки
          <input name="reference_id" required>
        </label>
        <button type="submit">Записать корректировку</button>
      </form>
    </details>
    """


def render(db_file, session_cookie: str):
    summary=settlements.warehouse_summary(db_file)
    rows=settlements.list_settlements(db_file)
    sync_token=_csrf(session_cookie,"warehouse-settlements-sync","all")

    cards=[]
    for x in summary:
        balance=int(x["balance_rub"] or 0)
        cls="debt" if balance<0 else ("receivable" if balance>0 else "zero")
        cards.append(
            f"""<div class="summary-card {cls}">
              <strong>{_e(x['public_name'])}</strong>
              <div>{_e(_balance_text(balance))}</div>
              <small>Нужны условия: {int(x['pending_terms'])} · Открыто: {int(x['open_settlements'])}</small>
            </div>"""
        )

    body=[]
    for row in rows:
        balance=int(row.get("balance_rub") or 0)
        actions=(
            _terms_form(row,session_cookie)
            +_accrue_form(row,session_cookie)
            +_payment_form(row,session_cookie)
            +_adjustment_form(row,session_cookie)
        )
        body.append(
            f"""<tr>
              <td><b>{_e(row['order_id'])}</b><small>item #{int(row['order_item_id'])}</small></td>
              <td>{_e(row.get('warehouse_public_name') or row['warehouse_id'])}</td>
              <td><code>{_e(row.get('oem') or '—')}</code><small>{_e(row.get('name') or '')}</small></td>
              <td>{_e(PAYMENT_LABELS.get(row['payment_route'],row['payment_route']))}</td>
              <td>{_e(FULFILLMENT_LABELS.get(row['fulfillment_route'],row['fulfillment_route']))}</td>
              <td>{_money(row['customer_total_rub'])}</td>
              <td>{_e(MODE_LABELS.get(row['settlement_mode'],row['settlement_mode']))}<small>{_money(row.get('partner_amount_rub'))}</small></td>
              <td><b>{_e(_balance_text(balance))}</b></td>
              <td>{_e(STATUS_LABELS.get(row['status'],row['status']))}</td>
              <td class="actions">{actions}</td>
            </tr>"""
        )

    empty=(
        """<div class="empty">Карточек взаиморасчётов пока нет. Нажми «Синхронизировать позиции складов».</div>"""
        if not rows else ""
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Мои расчёты со складами — WEB ADMIN</title>
<style>
body{{font-family:Arial,sans-serif;background:#f4f6f8;margin:0;color:#101828}}
.wrap{{max-width:1500px;margin:0 auto;padding:24px}}
a{{color:inherit}}
.nav{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:18px}}
.nav a{{font-weight:700;text-decoration:none}}
h1{{margin:0 0 6px}}
.muted{{color:#667085;font-size:13px}}
.summary{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:18px 0}}
.summary-card{{background:#fff;border-radius:12px;padding:16px;border-left:6px solid #98a2b3;box-shadow:0 2px 8px #0000000b}}
.summary-card.debt{{border-left-color:#f04438}}
.summary-card.receivable{{border-left-color:#12b76a}}
.summary-card strong{{display:block;margin-bottom:8px}}
.summary-card small,td small{{display:block;color:#667085;margin-top:5px}}
.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:14px 0}}
button{{cursor:pointer;font-weight:700;border:1px solid #d0d5dd;background:#fff;border-radius:8px;padding:8px 11px}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;font-size:13px}}
th,td{{padding:10px;vertical-align:top;border-bottom:1px solid #eaecf0;text-align:left}}
th{{background:#f9fafb;color:#475467;position:sticky;top:0}}
.table-wrap{{overflow:auto;border-radius:12px;box-shadow:0 2px 8px #0000000b}}
.actions form{{display:grid;gap:5px;margin:7px 0}}
.actions input,.actions select{{box-sizing:border-box;width:100%;padding:6px;border:1px solid #d0d5dd;border-radius:6px}}
.actions label{{display:block;font-size:12px;color:#475467}}
details{{margin:4px 0;min-width:210px}}
summary{{cursor:pointer;font-weight:700}}
.empty{{background:#fff;padding:20px;border-radius:12px}}
@media(max-width:800px){{table{{min-width:1450px}}}}
</style>
</head>
<body>
<div class="wrap">
  <div class="nav">
    <a href="/admin/orders">Заказы</a>
    <a href="/admin/supplier-orders">Supplier Orders</a>
    <a href="/admin/warehouse-settlements">💱 Мои расчёты со складами</a>
    <a href="/admin/supplier-channels">Каналы складов</a>
  </div>
  <h1>💱 Мои расчёты со складами</h1>
  <div class="muted">
    Минус — мы должны складу. Плюс — склад должен нам.
    Цена клиенту никогда не используется как закупочная автоматически.
  </div>

  <div class="summary">{''.join(cards)}</div>

  <div class="toolbar">
    <form method="post" action="/admin/warehouse-settlements/sync">
      <input type="hidden" name="csrf_token" value="{_e(sync_token)}">
      <button type="submit">Синхронизировать позиции складов</button>
    </form>
  </div>

  {empty}
  <div class="table-wrap">
    <table>
      <thead>
        <tr>
          <th>Заказ</th><th>Склад</th><th>Позиция</th>
          <th>Оплата клиента</th><th>Исполнение</th>
          <th>Цена клиенту</th><th>Условия партнёра</th>
          <th>Баланс</th><th>Статус</th><th>Действия</th>
        </tr>
      </thead>
      <tbody>{''.join(body)}</tbody>
    </table>
  </div>
</div>
</body>
</html>"""
