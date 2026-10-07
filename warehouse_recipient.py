#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warehouse-recipient checkout flow shared by Telegram/Web handoff."""
FIELDS=("name","phone","city","method","destination")
PROMPTS={
"name":"👤 <b>Получатель</b>\nВведи имя и фамилию получателя.",
"phone":"📞 <b>Телефон</b>\nВведи номер телефона получателя в формате: <code>+79991234567</code>.",
"city":"🏙 <b>Город</b>\nВведи город получения.",
"method":"🚚 <b>Способ получения</b>\nНапиши перевозчика/способ, например: <code>СДЭК ПВЗ</code> или <code>Курьер</code>.",
"destination":"📍 <b>Адрес или ПВЗ</b>\nВведи адрес доставки или адрес/код ПВЗ.",
}
def normalize_phone(value):
 s=str(value or "").strip()
 if not s: raise ValueError("empty")
 if any(ch.isalpha() for ch in s): raise ValueError("invalid")
 cleaned=s.replace(" ","").replace("\u00a0","").replace("(","").replace(")","").replace("-","")
 if cleaned.startswith("+"):
  digits=cleaned[1:]
  if not digits.isdigit(): raise ValueError("invalid")
 else:
  digits=cleaned
  if not digits.isdigit(): raise ValueError("invalid")
 if len(digits)==10:
  national=digits
 elif len(digits)==11 and digits[0] in {"7","8"}:
  national=digits[1:]
 else:
  raise ValueError("invalid")
 if len(national)!=10 or national[0]!="9":
  raise ValueError("invalid")
 return "+7"+national

def has_warehouse(cart): return any(str(x.get("offer_source") or "usa").lower()=="warehouse" for x in cart.values())
def complete(r): return all(str((r or {}).get(k) or "").strip() for k in FIELDS)
def normalize(r):
 r={k:str((r or {}).get(k) or "").strip() for k in FIELDS}
 return {"name":r["name"],"phone":r["phone"],"city":r["city"],"method":r["method"],"delivery_address":r["destination"],"pickup_point":r["destination"]}
def next_field(r):
 for k in FIELDS:
  if not str((r or {}).get(k) or "").strip(): return k
 return None
