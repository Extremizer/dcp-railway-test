from __future__ import annotations
import json,os,smtplib,urllib.request
from email.message import EmailMessage
import supplier_channels

def format_message(order):
 lines=[f"📦 ЗАКАЗ {order['client_order_id']}",f"Склад: {order['public_name']}","","ПОЗИЦИИ:"]
 for i in order["items"]: lines.append(f"• {i['oem']} × {float(i['quantity']):g} — {i.get('name') or ''}")
 lines += ["","ПОЛУЧАТЕЛЬ:",str(order.get("recipient_name") or "—"),str(order.get("recipient_phone") or "—"),str(order.get("recipient_city") or "—"),str(order.get("delivery_method") or "—"),str(order.get("delivery_address") or order.get("pickup_point") or "—")]
 return "\n".join(lines)

def telegram_sender(destination,text):
 token=os.getenv("TELEGRAM_BOT_TOKEN","").strip()
 if not token: raise RuntimeError("Telegram bot token is not configured")
 req=urllib.request.Request("https://api.telegram.org/bot"+token+"/sendMessage",data=json.dumps({"chat_id":destination,"text":text}).encode(),headers={"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=15) as r:data=json.loads(r.read())
 if not data.get("ok"):raise RuntimeError("Telegram delivery failed")
 return "telegram:"+str(data["result"]["message_id"])

def email_sender(destination,text):
 host=os.getenv("SUPPLIER_SMTP_HOST","").strip(); port=int(os.getenv("SUPPLIER_SMTP_PORT","587"))
 user=os.getenv("SUPPLIER_SMTP_USER","").strip(); password=os.getenv("SUPPLIER_SMTP_PASSWORD","")
 sender=os.getenv("SUPPLIER_SMTP_FROM",user).strip()
 if not host or not sender:raise RuntimeError("SMTP is not configured")
 m=EmailMessage();m["From"]=sender;m["To"]=destination;m["Subject"]="Новый заказ EXTREMIZER";m.set_content(text)
 with smtplib.SMTP(host,port,timeout=20) as s:
  if os.getenv("SUPPLIER_SMTP_STARTTLS","1")!="0":s.starttls()
  if user:s.login(user,password)
  s.send_message(m)
 return "email:accepted"

def whatsapp_sender(destination,text):
 token=os.getenv("WHATSAPP_ACCESS_TOKEN","").strip(); phone_id=os.getenv("WHATSAPP_PHONE_NUMBER_ID","").strip()
 if not token or not phone_id:raise RuntimeError("WhatsApp Business API is not configured")
 url=f"https://graph.facebook.com/v23.0/{phone_id}/messages"
 body={"messaging_product":"whatsapp","to":destination,"type":"text","text":{"body":text}}
 req=urllib.request.Request(url,data=json.dumps(body).encode(),headers={"Authorization":"Bearer "+token,"Content-Type":"application/json"})
 with urllib.request.urlopen(req,timeout=20) as r:data=json.loads(r.read())
 msgs=data.get("messages") or []
 if not msgs:raise RuntimeError("WhatsApp delivery failed")
 return "whatsapp:"+str(msgs[0].get("id","accepted"))

def custom_sender(destination,text):
 # Universal webhook: destination is an HTTPS endpoint controlled/configured by admin.
 if not str(destination).lower().startswith("https://"):raise RuntimeError("Custom channel requires HTTPS webhook URL")
 secret=os.getenv("SUPPLIER_CUSTOM_WEBHOOK_TOKEN","").strip()
 headers={"Content-Type":"application/json","User-Agent":"Extremizer-SupplierOrders/1.0"}
 if secret:headers["Authorization"]="Bearer "+secret
 req=urllib.request.Request(destination,data=json.dumps({"event":"supplier_order","text":text}).encode(),headers=headers)
 with urllib.request.urlopen(req,timeout=20) as r:
  if not 200<=r.status<300:raise RuntimeError("Custom webhook delivery failed")
  receipt=r.headers.get("X-Message-Id") or str(r.status)
 return "custom:"+receipt

DEFAULT_SENDERS={"telegram":telegram_sender,"email":email_sender,"whatsapp":whatsapp_sender,"custom":custom_sender}

def send_one(service,supplier_order_id,senders=None):
 order=service.get(supplier_order_id)
 if order["status"]!="not_sent":raise ValueError("supplier order is not ready to send")
 cfg=supplier_channels.get(service.db_file,order["warehouse_id"])
 if not cfg:raise ValueError("Канал связи для склада не настроен")
 sender=(senders or DEFAULT_SENDERS).get(cfg["channel"])
 if sender is None:raise ValueError(f"Канал {cfg['channel']} не поддерживается")
 # External provider must accept first. Only then business status may change.
 receipt=sender(cfg["recipient"],format_message(order))
 return service.change_status(supplier_order_id,"sent",f"channel={cfg['channel']}; receipt={receipt}")

def send_batch(service,ids,senders=None):
 results=[]
 for sid in ids:
  try:results.append({"id":sid,"ok":True,"order":send_one(service,sid,senders)})
  except Exception as e:results.append({"id":sid,"ok":False,"error":str(e)})
 return results
