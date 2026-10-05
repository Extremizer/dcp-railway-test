#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Real Telegram ADMIN handlers for Supplier Orders; delegates all rules to service."""
from html import escape
from telegram import InlineKeyboardButton,InlineKeyboardMarkup
from telegram.constants import ParseMode
import supplier_admin_auth
import supplier_delivery
import supplier_shortages
from supplier_orders import STATUS_LABELS

NEXT={"not_sent":("sent","📤 Отправлен складу"),"sent":("confirmed","👍 Подтверждён складом"),"confirmed":("assembled","📦 Собран"),"shipped":("delivered","✅ Доставлен")}
def _auth(update): supplier_admin_auth.require_telegram_admin(update.effective_user.id if update.effective_user else None)
def _qty(v): return f"{float(v):g}"
def queue_screen(service):
    rows=service.list_work_queue(); groups={}
    for x in rows: groups.setdefault(x["public_name"],[]).append(x)
    lines=["📦 <b>Заказы поставщикам</b>","",f"Активных отправлений: <b>{len(rows)}</b>"]; kb=[]
    for wh,xs in groups.items():
        lines+=["",f"<b>{escape(wh)}</b> — {len(xs)} заказ(а)"]
        unsent=[]
        for x in xs:
            lines.append(f"<code>{escape(x['client_order_id'])}</code> · {STATUS_LABELS.get(x['status'],x['status'])}")
            kb.append([InlineKeyboardButton(f"{x['client_order_id']} · {STATUS_LABELS.get(x['status'],x['status'])}",callback_data=f"admin:supplier:{x['id']}")])
            if x["status"]=="not_sent": unsent.append(str(x["id"]))
        if unsent: kb.append([InlineKeyboardButton(f"📤 Отправить все {wh}",callback_data=f"admin:supplierbatch:{','.join(unsent)}")])
    kb.append([InlineKeyboardButton("⬅️ Назад в админку",callback_data="admin:home")])
    return "\n".join(lines),InlineKeyboardMarkup(kb)
def card_screen(service,sid):
    x=service.get(sid); lines=[f"📦 <b>{escape(x['public_name'])}</b>",f"Запрос: <code>{escape(x['client_order_id'])}</code>",STATUS_LABELS.get(x["status"],x["status"]),"",f"<b>Получатель:</b> {escape(x.get('recipient_name') or '—')}",f"<b>Телефон:</b> {escape(x.get('recipient_phone') or '—')}",f"<b>Город:</b> {escape(x.get('recipient_city') or '—')}","", "<b>Позиции:</b>"]
    lines += [f"• <code>{escape(i['oem'])}</code> × {_qty(i['quantity'])}" for i in x["items"]]
    if x.get("tracking_number"): lines+=["",f"🚚 {escape(x.get('carrier') or '')}: <code>{escape(x['tracking_number'])}</code>"]
    kb=[[InlineKeyboardButton(f"⚠️ {i['oem']} — проблема",callback_data=f"admin:supplierproblem:{sid}:{i['id']}")] for i in x["items"]]
    nxt=NEXT.get(x["status"])
    if nxt: kb.append([InlineKeyboardButton(nxt[1],callback_data=f"admin:supplierstatus:{sid}:{nxt[0]}")])
    if x["status"]=="assembled": kb.append([InlineKeyboardButton("🚚 Ввести трек",callback_data=f"admin:suppliertrack:{sid}")])
    kb += [[InlineKeyboardButton("⬅️ К заказам поставщикам",callback_data="admin:suppliers")],[InlineKeyboardButton("⚙️ В админку",callback_data="admin:home")]]
    return "\n".join(lines),InlineKeyboardMarkup(kb)
async def handle_callback(update,context,service,data):
    _auth(update); q=update.callback_query
    if data=="admin:suppliers":
        text,kb=queue_screen(service); await q.edit_message_text(text,parse_mode=ParseMode.HTML,reply_markup=kb); return
    if data.startswith("admin:supplierbatch:"):
        ids=[int(x) for x in data.rsplit(":",1)[1].split(",") if x]
        supplier_delivery.send_batch(service,ids); text,kb=queue_screen(service); await q.edit_message_text(text,parse_mode=ParseMode.HTML,reply_markup=kb); return
    if data.startswith("admin:supplierstatus:"):
        _,_,sid,status=data.split(":",3); supplier_delivery.send_one(service,int(sid)) if status=="sent" else service.change_status(int(sid),status,"telegram_admin")
        text,kb=card_screen(service,int(sid)); await q.edit_message_text(text,parse_mode=ParseMode.HTML,reply_markup=kb); return
    if data.startswith("admin:supplierproblem:"):
        _,_,sid,item_id=data.split(":",3); sid=int(sid); item_id=int(item_id); order=service.get(sid); item=next(i for i in order["items"] if int(i["id"])==item_id)
        context.user_data["supplier_problem"]={"sid":sid,"item_id":item_id}
        await q.edit_message_text(f"⚠️ <b>Проблема с позицией</b>\n\nOEM: <code>{escape(item['oem'])}</code>\nЗаказано: <b>{_qty(item['quantity'])} шт.</b>\n\nВведи только количество, которое склад реально подтвердил.",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К отправлению",callback_data=f"admin:supplier:{sid}")]])); return
    if data.startswith("admin:suppliertrack:"):
        sid=int(data.rsplit(":",1)[1]); context.user_data["supplier_tracking_order_id"]=sid
        await q.edit_message_text("🚚 <b>Введи перевозчика и трек одним сообщением.</b>\n\nНапример: <code>СДЭК 123456789</code>",parse_mode=ParseMode.HTML,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ К отправлению",callback_data=f"admin:supplier:{sid}")]])); return
    if data.startswith("admin:supplier:"):
        sid=int(data.rsplit(":",1)[1]); text,kb=card_screen(service,sid); await q.edit_message_text(text,parse_mode=ParseMode.HTML,reply_markup=kb); return
async def handle_tracking_text(update,context,service,raw_text):
    _auth(update)
    problem=context.user_data.get("supplier_problem")
    if problem:
        try:q=float(raw_text.strip().replace(",","."))
        except ValueError:
            await update.effective_message.reply_text("⚠️ Введи только количество числом."); return True
        try:x=service.report_item_problem(problem["sid"],problem["item_id"],q)
        except ValueError as e:
            await update.effective_message.reply_text("⚠️ "+str(e)); return True
        context.user_data.pop("supplier_problem",None)
        text,kb=card_screen(service,problem["sid"]); await update.effective_message.reply_text(supplier_shortages.admin_summary(x)+"\n\n"+text,parse_mode=ParseMode.HTML,reply_markup=kb); return True
    sid=context.user_data.get("supplier_tracking_order_id")
    if not sid:return False
    parts=raw_text.strip().split(maxsplit=1)
    if len(parts)!=2:
        await update.effective_message.reply_text("⚠️ Формат: <code>СДЭК 123456789</code>",parse_mode=ParseMode.HTML); return True
    service.add_tracking(int(sid),parts[0],parts[1]); context.user_data.pop("supplier_tracking_order_id",None)
    text,kb=card_screen(service,int(sid)); await update.effective_message.reply_text(text,parse_mode=ParseMode.HTML,reply_markup=kb); return True
