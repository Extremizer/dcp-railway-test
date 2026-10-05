from __future__ import annotations
import json,os,sqlite3,urllib.request
def client_chat_id(db,client_order_id):
 try:
  with sqlite3.connect(db) as c:
   r=c.execute("SELECT telegram_user_id FROM orders WHERE order_id=?",(client_order_id,)).fetchone()
 except sqlite3.OperationalError:
  return None
 return int(r[0]) if r and r[0] is not None else None
def text(order):
 return f"📦 Ваш заказ отправлен со склада {order['public_name']}.\n{order['carrier']}\nТрек-номер: {order['tracking_number']}"
def telegram(chat_id,message):
 token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
 if not token:raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
 req=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=json.dumps({"chat_id":chat_id,"text":message}).encode(),headers={"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read())
 if not data.get("ok"):raise RuntimeError("client Telegram notification failed")
 return str(data["result"]["message_id"])
def notify(service,order,sender=None):
 chat=client_chat_id(service.db_file,order["client_order_id"])
 if chat is None:raise RuntimeError("client Telegram ID is missing")
 return (sender or telegram)(chat,text(order))

def shortage_text(x,public_name):
 q=x["ordered_qty"]; c=x["confirmed_qty"]; s=x["shortage_qty"]
 return (f"⚠️ По позиции {x['oem']} склад {public_name} подтвердил наличие только {_q(c)} из {_q(q)} шт.\n\n"
         f"Мы проверили остальные склады — ещё {_q(s)} шт. сейчас нет в наличии.\n\n"
         "Что сделать с заказом?")
def _q(v):
 v=float(v); return str(int(v)) if v.is_integer() else ("%g"%v)
def shortage_buttons(x):
 q=_q(x["ordered_qty"]);c=_q(x["confirmed_qty"]);s=_q(x["shortage_qty"]);sid=x["id"]
 return {"inline_keyboard":[
  [{"text":f"✅ Отгрузить {c} шт. и вернуть деньги за {s} шт.","callback_data":f"shortage:partial_refund:{sid}"}],
  [{"text":f"↗️ Отгрузить {c} шт. и запросить цену из США на {s} шт.","callback_data":f"shortage:usa_quote:{sid}"}],
  [{"text":f"❌ Отказаться от {q} шт.","callback_data":f"shortage:cancel_all:{sid}"}]
 ]}
def notify_shortage(service,x,public_name,sender=None):
 chat=client_chat_id(service.db_file,x["client_order_id"])
 if chat is None:raise RuntimeError("client Telegram ID is missing")
 message=shortage_text(x,public_name); markup=shortage_buttons(x)
 if sender:return sender(chat,message,markup)
 token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
 if not token:raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured")
 req=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=json.dumps({"chat_id":chat,"text":message,"reply_markup":markup}).encode(),headers={"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read())
 if not data.get("ok"):raise RuntimeError("client shortage notification failed")
 return str(data["result"]["message_id"])
