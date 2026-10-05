#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warehouse-recipient checkout flow shared by Telegram/Web handoff."""
FIELDS=("name","phone","city","method","destination")
PROMPTS={
"name":"👤 <b>Получатель</b>\nВведи имя и фамилию получателя.",
"phone":"📞 <b>Телефон</b>\nВведи номер телефона получателя.",
"city":"🏙 <b>Город</b>\nВведи город получения.",
"method":"🚚 <b>Способ получения</b>\nНапиши перевозчика/способ, например: <code>СДЭК ПВЗ</code> или <code>Курьер</code>.",
"destination":"📍 <b>Адрес или ПВЗ</b>\nВведи адрес доставки или адрес/код ПВЗ.",
}
def has_warehouse(cart): return any(str(x.get("offer_source") or "usa").lower()=="warehouse" for x in cart.values())
def complete(r): return all(str((r or {}).get(k) or "").strip() for k in FIELDS)
def normalize(r):
 r={k:str((r or {}).get(k) or "").strip() for k in FIELDS}
 return {"name":r["name"],"phone":r["phone"],"city":r["city"],"method":r["method"],"delivery_address":r["destination"],"pickup_point":r["destination"]}
def next_field(r):
 for k in FIELDS:
  if not str((r or {}).get(k) or "").strip(): return k
 return None
