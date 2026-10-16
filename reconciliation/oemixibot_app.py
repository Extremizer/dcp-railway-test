"""OEMixiBOT Telegram application. Isolated: not wired into current production runtime."""
import os, secrets, logging, asyncio, asyncio, asyncio, asyncio
from datetime import datetime
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters
from oemixibot_store import OemixiStore
from oemixibot_telegram import DealerAccess, ACCESS_DENIED, WELCOME, price_card, cart_message, normalize_oem
from oemixibot_catalog import resolve_offer
from oemixibot_finance import FinanceEngine
from supplier_admin_auth import is_telegram_admin, telegram_admin_ids
from oemixibot_analytics_client import send_oem_request_async
from oemixibot_identity_client import lookup_shared_identity
import oem_identity_service
from oemixibot_identity_client import lookup_shared_identity
import oem_identity_service
from oemixibot_identity_client import lookup_shared_identity
import oem_identity_service
from oemixibot_identity_client import lookup_shared_identity
import oem_identity_service

STORE=OemixiStore()
ACCESS=DealerAccess(STORE)
FINANCE=FinanceEngine(STORE.path)

def _dealer(update):
    u=update.effective_user
    return ACCESS.dealer_for_telegram(u.id) if u else None

def _cart_keyboard(cart):
    rows=[]
    for r in cart["items"]:
        o=r["oem"]
        rows.append([
            InlineKeyboardButton("➖",callback_data="q-:"+o),
            InlineKeyboardButton(str(r["qty"])+" шт.",callback_data="noop"),
            InlineKeyboardButton("➕",callback_data="q+:"+o),
            InlineKeyboardButton("🗑",callback_data="del:"+o)])
    if cart["items"]: rows.append([InlineKeyboardButton("✅ Подтвердить заказ",callback_data="checkout")])
    return InlineKeyboardMarkup(rows)

def _balance_text(dealer):
    buyer_id=dealer["id"]
    balance=FINANCE.balance(buyer_id,"dealer","USD")
    credit=FINANCE.credit_limit(buyer_id,"dealer","USD")
    available=FINANCE.available_to_order(buyer_id,"dealer","USD")
    def usd(v):
        sign="−" if v < 0 else ""
        return f"{sign}${abs(v):,.2f}"
    return "\n".join(["💵 Мой баланс","",f"Баланс: {usd(balance)}",f"Кредитный лимит: {usd(credit)}",f"Доступно для заказа: {usd(available)}"])

def _finance_filters_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("7 дней",callback_data="finhist:7"),InlineKeyboardButton("30 дней",callback_data="finhist:30")],
        [InlineKeyboardButton("Свой период",callback_data="finhist:custom"),InlineKeyboardButton("Всё время",callback_data="finhist:all")],
        [InlineKeyboardButton("🟢 Пополнить баланс [+]",callback_data="paymethods")],
        [InlineKeyboardButton("📦 Мои заказы",callback_data="orders")],
        [InlineKeyboardButton("← Главное меню",callback_data="finhome")],
    ])

FINANCE_EVENT_LABELS={
    "ORDER_CHARGE":"Оплата заказа",
    "PAYMENT":"Пополнение баланса",
    "REFUND":"Возврат",
    "AUTO_REFUND":"Автоматический возврат",
    "ADJUSTMENT_PLUS":"Корректировка баланса",
    "ADJUSTMENT_MINUS":"Корректировка баланса",
    "DELIVERY_CHARGE":"Оплата доставки",
}

def _finance_event_label(event_type):
    return FINANCE_EVENT_LABELS.get(event_type,event_type)

def _finance_event_display(event):
    label=_finance_event_label(event["event_type"])
    if event["event_type"]=="ORDER_CHARGE" and event.get("order_id"):
        return f"{label} {event['order_id']}"
    return label

def _finance_event_text(event):
    ts=(event["created_at"] or "").replace("T"," ")[:16]
    v=event["amount"]; sign="−" if v<0 else "+"
    if event.get("order_id") and event["event_type"]=="ORDER_CHARGE": purpose=f"Заказ {event['order_id']}"
    elif event["event_type"] in {"ADJUSTMENT_PLUS","ADJUSTMENT_MINUS"}: purpose=event.get("reason") or event["description"]
    else: purpose=event["description"]
    lines=["💵 Финансовая операция","",f"Дата: {ts} UTC",f"Операция: {_finance_event_label(event['event_type'])}",f"Сумма: {sign}${abs(v):,.2f}",f"Основание: {purpose}"]
    if event.get("oem"): lines.append(f"OEM: {event['oem']}")
    if event.get("arrival_id") is not None: lines.append(f"Поставка: #{event['arrival_id']}")
    return "\n".join(lines)

def _finance_history_buttons(dealer,period):
    from datetime import timezone,timedelta
    now=datetime.now(timezone.utc); from_at=None
    if period in {"7","30"}: from_at=(now-timedelta(days=int(period))).isoformat()
    rows=FINANCE.history(dealer["id"],"dealer","USD",from_at=from_at)
    buttons=[]
    for r in rows:
        ts=(r["created_at"] or "").replace("T"," ")[5:16]
        v=r["amount"]; sign="−" if v<0 else "+"
        buttons.append([InlineKeyboardButton(f"{ts} · {_finance_event_display(r)} · {sign}${abs(v):,.2f}",callback_data=f"finevent:{r['id']}:{period}")])
    buttons.append([InlineKeyboardButton("← Мой баланс",callback_data="balance"),InlineKeyboardButton("📦 Мои заказы",callback_data="orders")])
    return InlineKeyboardMarkup(buttons)

def _finance_history_text(dealer,period):
    from datetime import timezone,timedelta
    now=datetime.now(timezone.utc)
    from_at=None
    title={"7":"7 дней","30":"30 дней","all":"Всё время"}[period]
    if period in {"7","30"}: from_at=(now-timedelta(days=int(period))).isoformat()
    rows=FINANCE.history(dealer["id"],"dealer","USD",from_at=from_at)
    lines=["💵 История операций",f"Период: {title}",""]
    if not rows: lines.append("Операций за выбранный период нет.")
    for r in rows:
        ts=(r["created_at"] or "").replace("T"," ")[:16]
        v=r["amount"]; sign="−" if v<0 else "+"
        lines.append(f"{ts} UTC · {_finance_event_display(r)} · {sign}${abs(v):,.2f}")
    return "\n".join(lines)

async def start(update:Update,context:ContextTypes.DEFAULT_TYPE):
    dealer=_dealer(update)
    if not dealer:
        await update.effective_message.reply_text(ACCESS_DENIED); return
    kb=InlineKeyboardMarkup([
        [InlineKeyboardButton("💵 Мой баланс",callback_data="balance")],
        [InlineKeyboardButton("📦 Мои заказы",callback_data="orders")],
    ])
    balance=FINANCE.balance(dealer["id"],"dealer","USD")
    available=FINANCE.available_to_order(dealer["id"],"dealer","USD")
    def usd(v): return ("−" if v<0 else "")+f"${abs(v):,.2f}"
    start_text="\n".join(["OEMixiBOT","","Дилерский OEM-поиск и заказы.","",f"💵 Баланс: {usd(balance)}",f"Доступно для заказа: {usd(available)}"])
    await update.effective_message.reply_text(start_text,reply_markup=kb)

async def balance_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    dealer=_dealer(update)
    if not dealer:
        await update.effective_message.reply_text(ACCESS_DENIED); return
    await update.effective_message.reply_text(_balance_text(dealer),reply_markup=_finance_filters_keyboard())

def _finance_custom_history_text(dealer,date_from,date_to):
    from datetime import datetime as dt,timezone,timedelta
    start=dt.strptime(date_from,"%d.%m.%Y").replace(tzinfo=timezone.utc)
    end=dt.strptime(date_to,"%d.%m.%Y").replace(tzinfo=timezone.utc)+timedelta(days=1)-timedelta(microseconds=1)
    rows=FINANCE.history(dealer["id"],"dealer","USD",from_at=start.isoformat(),to_at=end.isoformat())
    lines=["💵 История операций",f"Период: {date_from} — {date_to}",""]
    if not rows: lines.append("Операций за выбранный период нет.")
    for r in rows:
        ts=(r["created_at"] or "").replace("T"," ")[:16]
        v=r["amount"]; sign="−" if v<0 else "+"
        lines.append(f"{ts} UTC · {_finance_event_display(r)} · {sign}${abs(v):,.2f}")
    return "\n".join(lines),rows

def _finance_custom_keyboard(rows):
    buttons=[]
    for r in rows:
        ts=(r["created_at"] or "").replace("T"," ")[5:16]
        v=r["amount"]; sign="−" if v<0 else "+"
        buttons.append([InlineKeyboardButton(f"{ts} · {_finance_event_display(r)} · {sign}${abs(v):,.2f}",callback_data=f"finevent:{r['id']}:all")])
    buttons.append([InlineKeyboardButton("← Мой баланс",callback_data="balance"),InlineKeyboardButton("📦 Мои заказы",callback_data="orders")])
    return InlineKeyboardMarkup(buttons)

_MONTHS=["Январь","Февраль","Март","Апрель","Май","Июнь","Июль","Август","Сентябрь","Октябрь","Ноябрь","Декабрь"]

def _fcp_prompt(state):
    side="начала" if state["side"]=="from" else "окончания"
    step=state["step"]
    chosen=[]
    if state.get("year"): chosen.append(str(state["year"]))
    if state.get("month"): chosen.append(_MONTHS[state["month"]-1])
    return f"💵 Свой период\n\nВыберите {step} {side} периода."+("\n\nВыбрано: "+" · ".join(chosen) if chosen else "")

def _fcp_keyboard(state):
    import calendar
    step=state["step"]
    if step=="год":
        y=datetime.now().year; vals=list(range(y,y-6,-1)); rows=[[InlineKeyboardButton(str(v),callback_data=f"fcp:y:{v}") for v in vals[i:i+3]] for i in range(0,len(vals),3)]
    elif step=="месяц":
        rows=[[InlineKeyboardButton(_MONTHS[v-1],callback_data=f"fcp:m:{v}") for v in range(i,min(i+3,13))] for i in range(1,13,3)]
    else:
        maxd=calendar.monthrange(state["year"],state["month"])[1]; vals=list(range(1,maxd+1)); rows=[[InlineKeyboardButton(str(v),callback_data=f"fcp:d:{v}") for v in vals[i:i+7]] for i in range(0,len(vals),7)]
    if step!="год": rows.append([InlineKeyboardButton("← Назад",callback_data="fcp:back")])
    rows.append([InlineKeyboardButton("Отмена",callback_data="balance")])
    return InlineKeyboardMarkup(rows)

async def oem_message(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if context.user_data.get("admin_oem_search"):
        if not u or not is_telegram_admin(u.id):
            context.user_data.pop("admin_oem_search",None)
            await update.effective_message.reply_text(ACCESS_DENIED)
            return
        oem=normalize_oem(update.effective_message.text)
        if not oem:
            await update.effective_message.reply_text("Введите один OEM-номер.",reply_markup=_admin_search_keyboard())
            return
        offer=await _resolve_admin_oem_offer(oem,context)
        await update.effective_message.reply_text(_admin_offer_text(oem,offer),reply_markup=_admin_search_keyboard())
        return
    state=context.user_data.get("admin_topup")
    if state and u and is_telegram_admin(u.id):
        raw=(update.effective_message.text or "").strip().replace(",",".")
        if state["step"]=="amount":
            from decimal import Decimal,InvalidOperation
            try: amount=Decimal(raw)
            except InvalidOperation: await update.effective_message.reply_text("Введите сумму числом, например 1000."); return
            if amount<=0: await update.effective_message.reply_text("Сумма должна быть больше нуля."); return
            state["amount"]=str(amount); state["step"]="reason"
            try: credited,fx_code,rate=_admin_topup_preview(state)
            except ValueError as e:
                if str(e).startswith("FX_NOT_CONFIGURED:"):
                    code=str(e).split(":",1)[1]; context.user_data.pop("admin_topup",None)
                    await update.effective_message.reply_text(f"⚠️ Пополнение пока невозможно: курс {code} не настроен в админке.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{state['dealer_id']}")]])); return
                raise
            preview=f"Получено: {state['amount']} {state['method']['payment_currency']}\n"
            if fx_code: preview+=f"Курс: {fx_code} = {rate}\n"
            preview+=f"Будет зачислено: ${credited:,.2f}\n\nВведите обязательное основание пополнения."
            await update.effective_message.reply_text(preview); return
        if state["step"]=="reason":
            if not raw: await update.effective_message.reply_text("Основание обязательно."); return
            state["reason"]=raw; state["step"]="confirm"
            state.setdefault("payment_id",f"ADMIN-{state['dealer_id']}-{secrets.token_hex(8)}")
            await update.effective_message.reply_text(_admin_topup_confirm_text(state),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Зачислить",callback_data=f"admtopup:confirm:{state['dealer_id']}")],[InlineKeyboardButton("Отмена",callback_data=f"admfin:view:{state['dealer_id']}")]])); return
    debit=context.user_data.get("admin_debit")
    if debit and u and is_telegram_admin(u.id):
        raw=(update.effective_message.text or "").strip().replace(",",".")
        if debit["step"]=="amount":
            from decimal import Decimal,InvalidOperation
            try: amount=Decimal(raw)
            except InvalidOperation: await update.effective_message.reply_text("Введите сумму числом, например 100."); return
            if amount<=0: await update.effective_message.reply_text("Сумма должна быть больше нуля."); return
            debit["amount"]=str(amount); debit["step"]="reason"
            await update.effective_message.reply_text(f"Сумма списания: ${amount:,.2f}\n\nВведите обязательное основание списания."); return
        if debit["step"]=="reason":
            if not raw: await update.effective_message.reply_text("Основание обязательно."); return
            import uuid
            debit["reason"]=raw; debit["step"]="preview"; debit.setdefault("adjustment_id",uuid.uuid4().hex)
            await update.effective_message.reply_text(_admin_debit_confirm_text(debit),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Списать",callback_data="admdebit:confirm")],[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{debit['dealer_id']}")]])); return
    credit=context.user_data.get("admin_credit")
    if credit and u and is_telegram_admin(u.id):
        raw=(update.effective_message.text or "").strip().replace(",",".")
        if credit["step"]=="limit":
            from decimal import Decimal,InvalidOperation
            try: new_limit=Decimal(raw)
            except InvalidOperation: await update.effective_message.reply_text("Введите кредитный лимит числом, например 500."); return
            if new_limit<0: await update.effective_message.reply_text("Кредитный лимит не может быть отрицательным."); return
            if new_limit==credit["current_limit"]: await update.effective_message.reply_text("Новый кредитный лимит совпадает с текущим. Введите другое значение."); return
            credit["new_limit"]=new_limit; credit["step"]="reason"
            await update.effective_message.reply_text(f"Новый кредитный лимит: ${new_limit:,.2f}\n\nВведите обязательное основание изменения."); return
        if credit["step"]=="reason":
            if not raw: await update.effective_message.reply_text("Основание обязательно."); return
            credit["reason"]=raw; credit["step"]="preview"; credit.setdefault("credit_change_id",f"ADMIN-CREDIT-{credit['dealer_id']}-{secrets.token_hex(16)}")
            text="\n".join(["⚙️ Кредитный лимит","",f"Дилер: {credit['dealer_name']}",f"Текущий лимит: ${credit['current_limit']:,.2f}",f"Новый лимит: ${credit['new_limit']:,.2f}","",f"Основание: {credit['reason']}","","⚠️ Проверьте данные. Изменение будет выполнено только после нажатия «✅ Установить». "])
            await update.effective_message.reply_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Установить",callback_data=f"admfin:creditconfirm:{credit['dealer_id']}")],[InlineKeyboardButton("← Изменить лимит",callback_data=f"admfin:credit:{credit['dealer_id']}")],[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{credit['dealer_id']}")]])); return
    due=context.user_data.get("admin_due")
    if due and u and is_telegram_admin(u.id):
        raw=(update.effective_message.text or "").strip()
        if due["step"]=="date":
            from datetime import date
            try: parsed=date.fromisoformat(raw)
            except ValueError: await update.effective_message.reply_text("Введите дату в формате YYYY-MM-DD, например 2026-10-20."); return
            if parsed.isoformat()!=raw: await update.effective_message.reply_text("Введите дату в формате YYYY-MM-DD, например 2026-10-20."); return
            if raw==due.get("current_due"): await update.effective_message.reply_text("Новая дата совпадает с текущей. Введите другую дату."); return
            due["due_date"]=raw; due["step"]="reason"
            await update.effective_message.reply_text(f"Новый срок оплаты: {raw}\n\nВведите обязательное основание изменения срока."); return
        if due["step"]=="reason":
            if not raw: await update.effective_message.reply_text("Основание обязательно."); return
            due["reason"]=raw; due["step"]="preview"; due.setdefault("due_change_id",f"ADMIN-DUE-{due['source_event_id']}-{secrets.token_hex(16)}")
            old=due.get("current_due") or "не установлен"
            text="\n".join(["📅 Срок оплаты","",f"Дилер: {due['dealer_name']}",f"Обязательство: {due['source_label']}",f"Остаток: ${due['outstanding']:,.2f}",f"Текущий срок: {old}",f"Новый срок: {due['due_date']}","",f"Основание: {due['reason']}","","⚠️ Проверьте данные. Срок изменится только после нажатия «✅ Установить». "])
            await update.effective_message.reply_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("✅ Установить",callback_data=f"admaging:confirm:{due['dealer_id']}")],[InlineKeyboardButton("← Aging задолженности",callback_data=f"admaging:view:{due['dealer_id']}:0")]])); return
        if due["step"]=="preview":
            await update.effective_message.reply_text("Используйте кнопки под preview: «✅ Установить» или «← Aging задолженности»."); return
    dealer=_dealer(update)
    if not dealer:
        await update.effective_message.reply_text(ACCESS_DENIED); return
    oem=normalize_oem(update.effective_message.text)
    if not oem:
        await update.effective_message.reply_text("Введите OEM одним сообщением."); return
    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")=="IDENTITY_REQUIRED":
        try:
            shared_identity=await asyncio.to_thread(lookup_shared_identity,oem)
        except Exception:
            logging.exception("OEMixiBOT shared identity lookup failed oem=%s",oem)
            shared_identity=None
        if shared_identity:
            try:
                saved=oem_identity_service.remember(
                    oem,
                    shared_identity.get("manufacturer"),
                    item_type=shared_identity.get("item_type"),
                    source="shared_production_identity",
                )
            except Exception:
                logging.exception("OEMixiBOT shared identity save failed oem=%s",oem)
            else:
                if saved:
                    offer=resolve_offer(oem)
    if offer.get("status")!="FOUND" or offer.get("dl_usd") is None:
        if u:
            context.application.create_task(send_oem_request_async(
                telegram_user_id=u.id,
                username=u.username,
                first_name=u.first_name,
                last_name=u.last_name,
                oem=offer.get("oem") or oem,
                manufacturer=offer.get("manufacturer"),
                result_status=offer.get("status") or "NOT_FOUND",
                price_status="NOT_FOUND",
                display_price_amount=None,
                display_price_currency="USD",
            ))
        if offer.get("status")=="IDENTITY_REQUIRED":
            await update.effective_message.reply_text("OEM найден не полностью. Требуется уточнение производителя администратором.")
        else:
            await update.effective_message.reply_text("Цена из США сейчас недоступна. Попробуйте повторить запрос позже.")
        return
    context.user_data["offer:"+oem]=offer
    display_price_usd=round(float(offer["dl_usd"])*float(dealer["price_coefficient"])+1e-9,2)
    text=price_card(offer.get("manufacturer"),offer.get("oem") or oem,offer.get("name"),
                    offer["dl_usd"],dealer["price_coefficient"],offer.get("item_type"),
                    offer.get("actual_weight_kg"),offer.get("volume_weight_kg"))
    cart=STORE.cart_view(dealer["id"])
    cart_qty=sum(int(r["qty"]) for r in cart["items"])
    rows=[[InlineKeyboardButton("➕ В корзину",callback_data="add:"+oem)]]
    if cart_qty:
        rows.append([InlineKeyboardButton(f"🛒 Корзина · {cart_qty} шт. · ${cart['total_usd']:,.2f}",callback_data="cart")])
    kb=InlineKeyboardMarkup(rows)
    await update.effective_message.reply_text(text,reply_markup=kb)
    if u:
        context.application.create_task(send_oem_request_async(
            telegram_user_id=u.id,
            username=u.username,
            first_name=u.first_name,
            last_name=u.last_name,
            oem=offer.get("oem") or oem,
            manufacturer=offer.get("manufacturer"),
            result_status="FOUND",
            price_status="FOUND",
            display_price_amount=display_price_usd,
            display_price_currency="USD",
        ))

def _orders_text(orders):
    if not orders: return "\U0001f4e6 \u0423 \u0432\u0430\u0441 \u043f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0437\u0430\u043a\u0430\u0437\u043e\u0432."
    lines=["\U0001f4e6 \u041c\u043e\u0438 \u0437\u0430\u043a\u0430\u0437\u044b",""]
    for o in orders:
        lines.append(f"{o['id']} \u00b7 {o['total_qty']} \u0448\u0442. \u00b7 ${o['sell_total']:,.2f}")
    return "\n".join(lines)

def _orders_keyboard(orders):
    return InlineKeyboardMarkup([[InlineKeyboardButton(
        f"{o['id']} \u00b7 ${o['sell_total']:,.2f}",callback_data="order:"+o["id"])] for o in orders])

def _order_text(order):
    lines=[f"\U0001f4e6 \u0417\u0430\u043a\u0430\u0437 {order['id']}",f"\u0421\u0443\u043c\u043c\u0430: ${order['sell_total']:,.2f}",""]
    for r in order["items"]:
        lines.append(f"{r['oem']} \u00d7 {r['qty']} \u2014 {r['status']}")
    return "\n".join(lines)

def _history_text(history):
    lines=["\U0001f4dc \u0418\u0441\u0442\u043e\u0440\u0438\u044f \u0437\u0430\u043a\u0430\u0437\u0430 "+history["id"]]
    for item in history["items"]:
        lines.extend(["",f"{item['oem']} \u00d7 {item['qty']}"])
        for e in item["events"]:
            ts=(e["created_at"] or "").replace("T"," ")[:16]
            display_status={"Принята":"Принят"}.get(e["to_status"],e["to_status"])
            lines.append(f"{ts} UTC \u2014 {display_status}")
    return "\n".join(lines)

async def orders_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    dealer=_dealer(update)
    if not dealer:
        await update.effective_message.reply_text(ACCESS_DENIED); return
    orders=STORE.orders_for_buyer(dealer["id"],"dealer","telegram")
    await update.effective_message.reply_text(_orders_text(orders),reply_markup=_orders_keyboard(orders) if orders else None)

async def cart_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    dealer=_dealer(update)
    if not dealer:
        await update.effective_message.reply_text(ACCESS_DENIED); return
    cart=STORE.cart_view(dealer["id"])
    await update.effective_message.reply_text(cart_message(cart) if cart["items"] else "🛒 Корзина пуста.",reply_markup=_cart_keyboard(cart))

def _admin_dealers():
    with STORE.db() as c:
        return [dict(r) for r in c.execute("SELECT id,name,telegram_id,active FROM dealers ORDER BY name,id")]

def _admin_root_keyboard(dealers):
    rows=[
        [InlineKeyboardButton("🔎 Поиск по OEM",callback_data="admsearch")],
        [InlineKeyboardButton("💳 Задолженность дилеров",callback_data="admdebt")],
        [InlineKeyboardButton("⏳ Aging по дилерам",callback_data="admagingsum:all:0")],
    ]
    rows += [[InlineKeyboardButton(f"👤 {d['name']}",callback_data=f"admdealer:{d['id']}")] for d in dealers]
    return InlineKeyboardMarkup(rows) if rows else None

def _clear_admin_input_modes(user_data):
    for key in ("admin_oem_search","admin_topup","admin_debit","admin_credit","admin_due"):
        user_data.pop(key,None)


def _admin_search_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Искать ещё",callback_data="admsearch")],
        [InlineKeyboardButton("← В ADMIN",callback_data="admhome")],
    ])

def _admin_offer_text(oem, offer):
    from oemixi_admin_polaris_cart_price import format_admin_price
    return format_admin_price(oem,offer)

async def _resolve_admin_oem_offer(oem,context):
    from oemixi_admin_polaris_cart_price import admin_cart_price
    # Only an explicitly installed trusted server SQLite connection can
    # supply Category Authority. Never accept category data from Telegram.
    # Trusted server-side configuration only; connection opens inside worker thread.
    return await asyncio.to_thread(admin_cart_price,oem)


def _admin_debt_rows():
    rows=[FINANCE.dealer_debt_status(d["id"],"USD") for d in _admin_dealers()]
    priority={"over":0,"full":1,"credit":2,"normal":3}
    return sorted(rows,key=lambda r:(priority[_admin_risk_status(r)[0]],-float(r["over_limit"]),-float(r["credit_used"]),r["dealer_name"].lower()))

def _admin_debt_filter(rows,mode):
    mode=mode if mode in {"all","credit","over"} else "all"
    if mode=="credit": return [r for r in rows if r["credit_used"]>0],mode
    if mode=="over": return [r for r in rows if r["over_limit"]>0],mode
    return list(rows),mode

def _admin_debt_summary(rows):
    from decimal import Decimal
    z=Decimal("0.00")
    return {
        "debt":sum((max(-r["balance"],z) for r in rows),z),
        "credit_limit":sum((r["credit_limit"] for r in rows),z),
        "credit_used":sum((r["credit_used"] for r in rows),z),
        "credit_remaining":sum((r["credit_remaining"] for r in rows),z),
        "available":sum((max(r["available_to_order"],z) for r in rows),z),
        "over_limit":sum((r["over_limit"] for r in rows),z),
    }

def _admin_risk_status(row):
    if row["over_limit"]>0:
        return "over","🔴","Превышение лимита"
    if row["credit_limit"]>0 and row["available_to_order"]==0 and row["credit_used"]>=row["credit_limit"]:
        return "full","🟠","Лимит исчерпан"
    if row["credit_used"]>0:
        return "credit","🟡","Кредит используется"
    return "normal","🟢","Норма"

def _admin_risk_counts(rows):
    counts={"normal":0,"credit":0,"full":0,"over":0}
    for r in rows:
        counts[_admin_risk_status(r)[0]]+=1
    return counts

def _admin_debt_page(rows,page,page_size=6):
    pages=max(1,(len(rows)+page_size-1)//page_size)
    page=max(0,min(int(page),pages-1))
    return rows[page*page_size:(page+1)*page_size],page,pages

def _admin_debt_text(rows,page=0,mode="all"):
    filtered,mode=_admin_debt_filter(rows,mode)
    page_rows,page,pages=_admin_debt_page(filtered,page)
    s=_admin_debt_summary(filtered); risks=_admin_risk_counts(filtered)
    usd=lambda v:("−" if v<0 else "")+f"${abs(v):,.2f}"
    labels={"all":"Все","credit":"Используют кредит","over":"Превышение лимита"}
    lines=["💳 Риски / задолженность","",f"Фильтр: {labels[mode]}",f"Дилеров: {len(filtered)}",f"🟢 Норма: {risks['normal']} · 🟡 Кредит: {risks['credit']} · 🟠 Лимит: {risks['full']} · 🔴 Превышение: {risks['over']}","",f"Общая задолженность: {usd(s['debt'])}",f"Кредитные лимиты: {usd(s['credit_limit'])}",f"Использовано кредита: {usd(s['credit_used'])}",f"Остаток кредитного лимита: {usd(s['credit_remaining'])}",f"Доступно для заказа: {usd(s['available'])}",f"Превышение лимита: {usd(s['over_limit'])}"]
    if s["over_limit"]>0: lines += ["","⚠️ Есть дилеры с отрицательным доступным остатком."]
    if pages>1: lines += ["",f"Страница {page+1}/{pages}"]
    for r in page_rows:
        _,icon,risk_label=_admin_risk_status(r)
        lines += ["",f"{icon} {r['dealer_name']}",f"Риск: {risk_label}",f"Баланс: {usd(r['balance'])}",f"Кредитный лимит: {usd(r['credit_limit'])}",f"Использовано кредита: {usd(r['credit_used'])}",f"Остаток кредитного лимита: {usd(r['credit_remaining'])}",f"Доступно для заказа: {usd(r['available_to_order'])}"]
        if r["over_limit"]>0: lines.append(f"⚠️ Превышение лимита: ${r['over_limit']:,.2f}")
        if not r["active"]: lines.append("Статус: отключён")
    if not filtered: lines += ["","По выбранному фильтру дилеров нет."]
    return "\n".join(lines)

def _admin_debt_keyboard(rows,page=0,mode="all"):
    filtered,mode=_admin_debt_filter(rows,mode)
    page_rows,page,pages=_admin_debt_page(filtered,page)
    buttons=[[InlineKeyboardButton("Все"+(" ✓" if mode=="all" else ""),callback_data="admdebt:all:0"),InlineKeyboardButton("Кредит"+(" ✓" if mode=="credit" else ""),callback_data="admdebt:credit:0"),InlineKeyboardButton("Превышение"+(" ✓" if mode=="over" else ""),callback_data="admdebt:over:0")]]
    for r in page_rows:
        _,icon,_=_admin_risk_status(r)
        buttons.append([InlineKeyboardButton(f"{icon} {r['dealer_name']}",callback_data=f"admdealer:{r['dealer_id']}"),InlineKeyboardButton("📜 История",callback_data=f"admfin:history:{r['dealer_id']}")])
    if pages>1:
        nav=[]
        if page>0: nav.append(InlineKeyboardButton("←",callback_data=f"admdebt:{mode}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}",callback_data="noop"))
        if page<pages-1: nav.append(InlineKeyboardButton("→",callback_data=f"admdebt:{mode}:{page+1}"))
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("← ADMIN",callback_data="admdealers")])
    return InlineKeyboardMarkup(buttons)

def _admin_aging_all_rows(as_of=None):
    rows=[]
    for d in _admin_dealers():
        for r in FINANCE.receivable_aging(d["id"],"USD",as_of=as_of):
            if r["outstanding"]<=0: continue
            x=dict(r); x["dealer_id"]=d["id"]; x["dealer_name"]=d["name"]; x["dealer_active"]=bool(d["active"])
            rows.append(x)
    priority={"overdue_30_plus":0,"overdue_8_30":1,"overdue_1_7":2,"no_due":3,"current":4}
    return sorted(rows,key=lambda r:(priority.get(r["aging_code"],9),-int(r.get("days_overdue") or 0),-float(r["outstanding"]),r["dealer_name"].lower(),r["source_event_id"]))

def _admin_aging_summary_filter(rows,mode):
    allowed={"all","no_due","overdue_1_7","overdue_8_30","overdue_30_plus"}
    mode=mode if mode in allowed else "all"
    return (list(rows) if mode=="all" else [r for r in rows if r["aging_code"]==mode]),mode

def _admin_aging_global_stats(rows):
    from decimal import Decimal
    codes=["no_due","current","overdue_1_7","overdue_8_30","overdue_30_plus"]
    out={c:{"count":0,"amount":Decimal("0.00")} for c in codes}
    for r in rows:
        code=r["aging_code"]
        if code not in out: continue
        out[code]["count"]+=1
        out[code]["amount"]+=r["outstanding"]
    out["total"]={"count":len(rows),"amount":sum((r["outstanding"] for r in rows),Decimal("0.00"))}
    return out

def _admin_aging_sum_icon(code):
    return {"no_due":"⚪","current":"🟢","overdue_1_7":"🟡","overdue_8_30":"🟠","overdue_30_plus":"🔴"}.get(code,"⚪")

def _admin_aging_sum_page(rows,page,page_size=5):
    pages=max(1,(len(rows)+page_size-1)//page_size); page=max(0,min(int(page),pages-1))
    return rows[page*page_size:(page+1)*page_size],page,pages

def _admin_aging_sum_text(rows,page=0,mode="all"):
    filtered,mode=_admin_aging_summary_filter(rows,mode)
    page_rows,page,pages=_admin_aging_sum_page(filtered,page)
    st=_admin_aging_global_stats(rows)
    from decimal import Decimal
    selected=sum((r["outstanding"] for r in filtered),Decimal("0.00"))
    labels={"all":"Все","no_due":"Без срока","overdue_1_7":"1–7 дней","overdue_8_30":"8–30 дней","overdue_30_plus":"30+ дней"}
    lines=["⏳ Aging по всем дилерам","",f"Всего открыто: {st['total']['count']} · ${st['total']['amount']:,.2f}",
           f"⚪ Без срока: {st['no_due']['count']} · ${st['no_due']['amount']:,.2f}",
           f"🟢 Не просрочено: {st['current']['count']} · ${st['current']['amount']:,.2f}",
           f"🟡 1–7 дней: {st['overdue_1_7']['count']} · ${st['overdue_1_7']['amount']:,.2f}",
           f"🟠 8–30 дней: {st['overdue_8_30']['count']} · ${st['overdue_8_30']['amount']:,.2f}",
           f"🔴 30+ дней: {st['overdue_30_plus']['count']} · ${st['overdue_30_plus']['amount']:,.2f}",
           "",f"Фильтр: {labels[mode]} · {len(filtered)} · ${selected:,.2f}"]
    if pages>1: lines += ["",f"Страница {page+1}/{pages}"]
    for r in page_rows:
        due=r["due_date"] or "не установлен"
        lines += ["",f"{_admin_aging_sum_icon(r['aging_code'])} {r['dealer_name']} · {_admin_aging_source_label(r)}",
                  f"Остаток: ${r['outstanding']:,.2f} · срок: {due}",f"Статус: {r['aging_label']}"]
        if not r["dealer_active"]: lines.append("Дилер: отключён")
    if not filtered: lines += ["","По выбранному фильтру обязательств нет."]
    return "\n".join(lines)

def _admin_aging_sum_keyboard(rows,page=0,mode="all"):
    filtered,mode=_admin_aging_summary_filter(rows,mode)
    page_rows,page,pages=_admin_aging_sum_page(filtered,page)
    buttons=[
        [InlineKeyboardButton("Все"+(" ✓" if mode=="all" else ""),callback_data="admagingsum:all:0"),
         InlineKeyboardButton("Без срока"+(" ✓" if mode=="no_due" else ""),callback_data="admagingsum:no_due:0")],
        [InlineKeyboardButton("1–7"+(" ✓" if mode=="overdue_1_7" else ""),callback_data="admagingsum:overdue_1_7:0"),
         InlineKeyboardButton("8–30"+(" ✓" if mode=="overdue_8_30" else ""),callback_data="admagingsum:overdue_8_30:0"),
         InlineKeyboardButton("30+"+(" ✓" if mode=="overdue_30_plus" else ""),callback_data="admagingsum:overdue_30_plus:0")],
    ]
    for r in page_rows:
        buttons.append([InlineKeyboardButton(f"{_admin_aging_sum_icon(r['aging_code'])} {r['dealer_name']} · ${r['outstanding']:,.2f}",callback_data=f"admagingsum:item:{r['source_event_id']}:{r['dealer_id']}:{mode}:{page}")])
    if pages>1:
        nav=[]
        if page>0: nav.append(InlineKeyboardButton("←",callback_data=f"admagingsum:{mode}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}",callback_data="noop"))
        if page<pages-1: nav.append(InlineKeyboardButton("→",callback_data=f"admagingsum:{mode}:{page+1}"))
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("← ADMIN",callback_data="admdealers")])
    return InlineKeyboardMarkup(buttons)

def _admin_aging_item_text(dealer_id,source_event_id,as_of=None):
    row=next((r for r in _admin_aging_all_rows(as_of) if r["dealer_id"]==dealer_id and r["source_event_id"]==int(source_event_id)),None)
    if not row: return None
    due=row["due_date"] or "не установлен"
    lines=["⏳ Обязательство","",f"Дилер: {row['dealer_name']}",f"Источник: {_admin_aging_source_label(row)}",
           f"Исходная сумма: ${row['original_amount']:,.2f}",f"Открытый остаток: ${row['outstanding']:,.2f}",
           f"Срок оплаты: {due}",f"Статус: {row['aging_label']}"]
    if row["days_overdue"]>0: lines.append(f"Дней просрочки: {row['days_overdue']}")
    return "\n".join(lines)

def _admin_aging_source_label(r):
    if r["event_type"]=="ORDER_CHARGE": return f"Заказ {r['order_id'] or r['reference_id'] or '#'+str(r['source_event_id'])}"
    if r["event_type"]=="DELIVERY_CHARGE": return f"Доставка · операция #{r['source_event_id']}"
    return f"Корректировка · операция #{r['source_event_id']}"

def _admin_aging_open_rows(dealer_id):
    return [r for r in FINANCE.receivable_aging(dealer_id,"USD") if r["outstanding"]>0]

def _admin_aging_page(rows,page,page_size=5):
    pages=max(1,(len(rows)+page_size-1)//page_size); page=max(0,min(int(page),pages-1))
    return rows[page*page_size:(page+1)*page_size],page,pages

def _admin_aging_text(dealer_id,page=0):
    d=next((x for x in _admin_dealers() if x["id"]==dealer_id),None)
    all_rows=FINANCE.receivable_aging(dealer_id,"USD"); rows=[r for r in all_rows if r["outstanding"]>0]
    page_rows,page,pages=_admin_aging_page(rows,page)
    summary=FINANCE.aging_summary(dealer_id,"USD")
    lines=["⏳ Aging задолженности","",f"Дилер: {d['name'] if d else dealer_id}",f"Открыто: ${summary['open_total']:,.2f}","",f"Без срока: {summary['no_due']['count']} · ${summary['no_due']['amount']:,.2f}",f"Не просрочено: {summary['current']['count']} · ${summary['current']['amount']:,.2f}",f"1–7 дней: {summary['overdue_1_7']['count']} · ${summary['overdue_1_7']['amount']:,.2f}",f"8–30 дней: {summary['overdue_8_30']['count']} · ${summary['overdue_8_30']['amount']:,.2f}",f"30+ дней: {summary['overdue_30_plus']['count']} · ${summary['overdue_30_plus']['amount']:,.2f}"]
    if pages>1: lines += ["",f"Страница {page+1}/{pages}"]
    for r in page_rows:
        due=r["due_date"] or "не установлен"
        lines += ["",f"#{r['source_event_id']} · {_admin_aging_source_label(r)} · ${r['outstanding']:,.2f}",f"Срок: {due}",f"Статус: {r['aging_label']}"]
    if not rows: lines += ["","Открытых обязательств нет."]
    return "\n".join(lines)

def _admin_aging_keyboard(dealer_id,page=0):
    rows=_admin_aging_open_rows(dealer_id); page_rows,page,pages=_admin_aging_page(rows,page); buttons=[]
    for r in page_rows:
        due=r["due_date"] or "без срока"
        buttons.append([InlineKeyboardButton(f"📅 #{r['source_event_id']} · {due} · ${r['outstanding']:,.2f}",callback_data=f"admaging:due:{r['source_event_id']}:{dealer_id}")])
    if pages>1:
        nav=[]
        if page>0: nav.append(InlineKeyboardButton("←",callback_data=f"admaging:view:{dealer_id}:{page-1}"))
        nav.append(InlineKeyboardButton(f"{page+1}/{pages}",callback_data="noop"))
        if page<pages-1: nav.append(InlineKeyboardButton("→",callback_data=f"admaging:view:{dealer_id}:{page+1}"))
        buttons.append(nav)
    buttons.append([InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{dealer_id}")])
    return InlineKeyboardMarkup(buttons)

def _admin_finance_text(dealer_id):
    b=FINANCE.balance(dealer_id,"dealer","USD"); cr=FINANCE.credit_limit(dealer_id,"dealer","USD"); av=FINANCE.available_to_order(dealer_id,"dealer","USD")
    usd=lambda v:("−" if v<0 else "")+f"${abs(v):,.2f}"
    return "\n".join(["💵 Финансы дилера","",f"Баланс: {usd(b)}",f"Кредитный лимит: {usd(cr)}",f"Доступно для заказа: {usd(av)}"])

def _admin_finance_keyboard(dealer_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🟢 Пополнить баланс [+]",callback_data=f"admfin:topup:{dealer_id}")],
        [InlineKeyboardButton("🔴 Списать с баланса [−]",callback_data=f"admfin:debit:{dealer_id}")],
        [InlineKeyboardButton("📜 История операций",callback_data=f"admfin:history:{dealer_id}")],
        [InlineKeyboardButton("⚙️ Кредитный лимит",callback_data=f"admfin:credit:{dealer_id}")],
        [InlineKeyboardButton("📜 История кредитного лимита",callback_data=f"admfin:credithistory:{dealer_id}")],
        [InlineKeyboardButton("⏳ Aging задолженности",callback_data=f"admaging:view:{dealer_id}:0")],
        [InlineKeyboardButton("← К дилеру",callback_data=f"admdealer:{dealer_id}")],
    ])

def _admin_topup_methods_keyboard(dealer_id):
    methods=FINANCE.payment_methods(dealer_id,"dealer")
    rows=[[InlineKeyboardButton(m["name"],callback_data=f"admtopup:method:{dealer_id}:{m['code']}")] for m in methods]
    rows.append([InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{dealer_id}")])
    return InlineKeyboardMarkup(rows)

def _admin_fx_rate(code):
    if not code: return None
    with STORE.db() as c:
        r=c.execute("SELECT rate,active FROM fx_rates WHERE code=?",(code,)).fetchone()
    if not r or not r["active"] or r["rate"] is None: return None
    from decimal import Decimal
    return Decimal(str(r["rate"]))

def _admin_topup_preview(state):
    from decimal import Decimal,ROUND_HALF_UP
    amount=Decimal(state["amount"]); method=state["method"]; currency=method["payment_currency"]
    if currency=="USD": credited=amount; fx_code=None; rate=None
    else:
        fx_code=method.get("fx_code_primary") or method.get("fx_code")
        rate=_admin_fx_rate(fx_code)
        if rate is None: raise ValueError(f"FX_NOT_CONFIGURED:{fx_code or currency}")
        credited=(amount*rate).quantize(Decimal("0.01"),rounding=ROUND_HALF_UP)
    return credited,fx_code,rate

def _admin_topup_confirm_text(state):
    credited,fx_code,rate=_admin_topup_preview(state); m=state["method"]
    lines=["💵 Подтверждение пополнения","",f"Дилер: {state['dealer_name']}",f"Способ: {m['name']}",f"Получено: {state['amount']} {m['payment_currency']}"]
    if fx_code: lines += [f"Курс: {fx_code} = {rate}",f"Зачислить: ${credited:,.2f}"]
    else: lines += [f"Зачислить: ${credited:,.2f}"]
    lines += [f"Основание: {state['reason']}","","⚠️ Preview-only: финансовая запись ещё не будет создана."]
    return "\n".join(lines)

def _admin_debit_confirm_text(state):
    from decimal import Decimal
    current=FINANCE.balance(state["dealer_id"],"dealer","USD")
    amount=Decimal(state["amount"])
    after=current-amount
    def usd(v): return ("−" if v<0 else "")+f"${abs(v):,.2f}"
    return "\n".join(["🔴 Списание с баланса","",f"Дилер: {state['dealer_name']}",f"Текущий баланс: {usd(current)}",f"Сумма списания: ${amount:,.2f}",f"Баланс после списания: {usd(after)}","",f"Основание: {state['reason']}","","⚠️ Preview-only: финансовая запись НЕ будет создана."])

async def admin_command(update:Update,context:ContextTypes.DEFAULT_TYPE):
    u=update.effective_user
    if not u or not is_telegram_admin(u.id):
        await update.effective_message.reply_text(ACCESS_DENIED); return
    _clear_admin_input_modes(context.user_data)
    dealers=_admin_dealers()
    await update.effective_message.reply_text("⚙️ OEMixiBOT ADMIN\n\n👥 Дилеры",reply_markup=_admin_root_keyboard(dealers))

async def callback(update:Update,context:ContextTypes.DEFAULT_TYPE):
    q=update.callback_query; await q.answer()
    data=q.data or ""
    if data.startswith("adm"):
        u=update.effective_user
        if not u or not is_telegram_admin(u.id): await q.edit_message_text(ACCESS_DENIED); return
        if data=="admsearch":
            _clear_admin_input_modes(context.user_data)
            context.user_data["admin_oem_search"]=True
            await q.edit_message_text("🔎 Поиск по OEM\n\nВведите один OEM-номер.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← В ADMIN",callback_data="admhome")]]))
            return
        if data=="admhome":
            _clear_admin_input_modes(context.user_data)
            await q.edit_message_text("⚙️ OEMixiBOT ADMIN",reply_markup=_admin_root_keyboard(_admin_dealers()))
            return
        if data != "admsearch":
            context.user_data.pop("admin_oem_search",None)
        if data.startswith("admdealer:"):
            did=data.split(":",1)[1]; d=next((x for x in _admin_dealers() if x["id"]==did),None)
            if not d: await q.edit_message_text("Дилер не найден."); return
            await q.edit_message_text(f"👤 Дилер: {d['name']}\nСтатус: {'активен' if d['active'] else 'отключён'}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💵 Финансы",callback_data=f"admfin:view:{did}")],[InlineKeyboardButton("← Дилеры",callback_data="admdealers")]])); return
        if data=="admdealers":
            dealers=_admin_dealers()
            await q.edit_message_text("⚙️ OEMixiBOT ADMIN\n\n👥 Дилеры",reply_markup=_admin_root_keyboard(dealers)); return
        if data=="admdebt" or data.startswith("admdebt:"):
            mode="all"; page=0
            parts=data.split(":")
            if len(parts)==2:
                if parts[1] in {"all","credit","over"}: mode=parts[1]
                else:
                    try: page=int(parts[1])
                    except ValueError: page=0
            elif len(parts)>=3:
                mode=parts[1] if parts[1] in {"all","credit","over"} else "all"
                try: page=int(parts[2])
                except ValueError: page=0
            rows=_admin_debt_rows()
            await q.edit_message_text(_admin_debt_text(rows,page,mode),reply_markup=_admin_debt_keyboard(rows,page,mode)); return
        if data.startswith("admagingsum:"):
            parts=data.split(":")
            if len(parts)>=2 and parts[1]=="item":
                if len(parts)!=6:
                    await q.edit_message_text("Обязательство не найдено."); return
                try: eid=int(parts[2]); page=int(parts[5])
                except ValueError: await q.edit_message_text("Обязательство не найдено."); return
                did=parts[3]; mode=parts[4]
                text=_admin_aging_item_text(did,eid)
                if not text: await q.edit_message_text("Обязательство не найдено."); return
                await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("📅 Изменить срок",callback_data=f"admaging:due:{eid}:{did}")],
                    [InlineKeyboardButton("← Aging-сводка",callback_data=f"admagingsum:{mode}:{page}")],
                    [InlineKeyboardButton("← К дилеру",callback_data=f"admdealer:{did}")],
                ])); return
            mode=parts[1] if len(parts)>=2 else "all"; page=0
            if len(parts)>=3:
                try: page=int(parts[2])
                except ValueError: page=0
            rows=_admin_aging_all_rows()
            await q.edit_message_text(_admin_aging_sum_text(rows,page,mode),reply_markup=_admin_aging_sum_keyboard(rows,page,mode)); return
        if data.startswith("admaging:"):
            parts=data.split(":")
            action=parts[1] if len(parts)>1 else ""
            if action=="view" and len(parts)>=3:
                did=parts[2]; page=0
                if len(parts)>=4:
                    try: page=int(parts[3])
                    except ValueError: page=0
                if not any(x["id"]==did for x in _admin_dealers()): await q.edit_message_text("Дилер не найден."); return
                context.user_data.pop("admin_due",None)
                await q.edit_message_text(_admin_aging_text(did,page),reply_markup=_admin_aging_keyboard(did,page)); return
            if action=="due" and len(parts)==4:
                try: eid=int(parts[2])
                except ValueError: await q.answer("Некорректное обязательство",show_alert=True); return
                did=parts[3]; row=next((r for r in FINANCE.receivable_aging(did,"USD") if r["source_event_id"]==eid and r["outstanding"]>0),None)
                if not row: await q.answer("Открытое обязательство не найдено",show_alert=True); return
                d=next((x for x in _admin_dealers() if x["id"]==did),None)
                context.user_data["admin_due"]={"dealer_id":did,"dealer_name":d["name"] if d else did,"source_event_id":eid,"source_label":_admin_aging_source_label(row),"outstanding":row["outstanding"],"current_due":row["due_date"],"step":"date"}
                current=row["due_date"] or "не установлен"
                await q.edit_message_text("\n".join(["📅 Срок оплаты","",f"Дилер: {d['name'] if d else did}",f"Обязательство: {_admin_aging_source_label(row)}",f"Остаток: ${row['outstanding']:,.2f}",f"Текущий срок: {current}","","Введите новый срок оплаты в формате YYYY-MM-DD.","Например: 2026-10-20"]),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена",callback_data=f"admaging:view:{did}:0")]])); return
            if action=="confirm" and len(parts)==3:
                did=parts[2]; st=context.user_data.get("admin_due")
                if not st or st.get("step")!="preview" or st.get("dealer_id")!=did or not st.get("due_change_id"):
                    await q.answer("Сессия изменения срока устарела. Начните заново.",show_alert=True); return
                try:
                    FINANCE.set_receivable_due_date(source_event_id=st["source_event_id"],due_date=st["due_date"],actor=f"telegram_admin:{u.id}",reason=st["reason"],idempotency_key=st["due_change_id"])
                except ValueError as e:
                    if "duplicate idempotency_key" in str(e):
                        context.user_data.pop("admin_due",None); await q.answer("Это изменение срока уже было выполнено.",show_alert=True); return
                    await q.answer(f"Срок не изменён: {e}",show_alert=True); return
                due_date=st["due_date"]; reason=st["reason"]; source_label=st["source_label"]; context.user_data.pop("admin_due",None)
                await q.edit_message_text("\n".join(["✅ Срок оплаты установлен","",f"Обязательство: {source_label}",f"Срок: {due_date}",f"Основание: {reason}"]),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Aging задолженности",callback_data=f"admaging:view:{did}:0")]])); return
            await q.edit_message_text("Aging-раздел не найден."); return
        if data=="admdebit:confirm":
            st=context.user_data.get("admin_debit")
            if not st or st.get("step")!="preview" or not st.get("adjustment_id"):
                await q.answer("Сессия списания устарела. Начните заново.",show_alert=True); return
            try:
                FINANCE.adjustment_minus(buyer_type="dealer",buyer_id=st["dealer_id"],currency="USD",amount=st["amount"],adjustment_id=st["adjustment_id"],actor=f"telegram_admin:{u.id}",reason=st["reason"])
            except ValueError as e:
                if "duplicate idempotency_key" in str(e):
                    context.user_data.pop("admin_debit",None); await q.answer("Это списание уже было выполнено.",show_alert=True); return
                raise
            did=st["dealer_id"]; amount=st["amount"]; reason=st["reason"]; context.user_data.pop("admin_debit",None)
            await q.edit_message_text(f"✅ С баланса списано ${float(amount):,.2f}\n\nОснование: {reason}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{did}")]])); return
        if data.startswith("admfinevent:"):
            _,eid,did=data.split(":",2)
            event=FINANCE.event(int(eid),did,"dealer","USD")
            if not event:
                await q.answer("Операция не найдена",show_alert=True); return
            await q.edit_message_text(_finance_event_text(event),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← История операций",callback_data=f"admfin:history:{did}")],[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{did}")]])); return
        parts=data.split(":",2)
        if len(parts)==3 and parts[0]=="admfin":
            action,did=parts[1],parts[2]
            if action=="view": context.user_data.pop("admin_debit",None); context.user_data.pop("admin_credit",None); context.user_data.pop("admin_due",None); await q.edit_message_text(_admin_finance_text(did),reply_markup=_admin_finance_keyboard(did)); return
            if action=="history":
                rows=FINANCE.history(did,"dealer","USD"); lines=["📜 История операций",""]
                if not rows: lines.append("Операций пока нет.")
                buttons=[]
                for r in rows:
                    ts=(r["created_at"] or "").replace("T"," ")[5:16]; v=r["amount"]; sign="−" if v<0 else "+"
                    buttons.append([InlineKeyboardButton(f"{ts} · {_finance_event_display(r)} · {sign}${abs(v):,.2f}",callback_data=f"admfinevent:{r['id']}:{did}")])
                buttons.append([InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{did}")])
                await q.edit_message_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons)); return
            if action=="credithistory":
                rows=FINANCE.credit_limit_history(did,100); lines=["📜 История кредитного лимита",""]
                if not rows: lines.append("Изменений пока нет.")
                buttons=[]
                for r in rows:
                    ts=(r["created_at"] or "").replace("T"," ")[5:16]
                    buttons.append([InlineKeyboardButton(f"{ts} · ${r['old_limit']:,.2f} → ${r['new_limit']:,.2f}",callback_data=f"admfin:creditevent:{r['id']}:{did}")])
                buttons.append([InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{did}")])
                await q.edit_message_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons)); return
            if action=="creditevent":
                try: eid,dealer_id=did.split(":",1)
                except ValueError: await q.answer("Некорректная запись кредитного лимита",show_alert=True); return
                event=FINANCE.credit_limit_event(eid,dealer_id)
                if not event: await q.answer("Изменение кредитного лимита не найдено",show_alert=True); return
                d=next((x for x in _admin_dealers() if x["id"]==dealer_id),None)
                created=(event["created_at"] or "").replace("T"," "); created=(created[:16]+" UTC") if created else "—"
                who=event["actor"] or "—"
                text="\n".join(["⚙️ Изменение кредитного лимита","",f"Дилер: {d['name'] if d else dealer_id}",f"Когда: {created}",f"Было: ${event['old_limit']:,.2f}",f"Стало: ${event['new_limit']:,.2f}",f"Кто: {who}",f"Основание: {event['reason']}"])
                await q.edit_message_text(text,reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← История кредитного лимита",callback_data=f"admfin:credithistory:{dealer_id}")],[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{dealer_id}")]])); return
            if action=="topup":
                context.user_data.pop("admin_topup",None)
                await q.edit_message_text("🟢 Пополнить баланс [+]\n\nВыберите способ фактически полученной оплаты:",reply_markup=_admin_topup_methods_keyboard(did)); return
            if action=="debit":
                context.user_data.pop("admin_topup",None)
                d=next((x for x in _admin_dealers() if x["id"]==did),None)
                context.user_data["admin_debit"]={"dealer_id":did,"dealer_name":d["name"] if d else did,"step":"amount"}
                await q.edit_message_text("🔴 Списать с баланса [−]\n\nВведите сумму списания в USD.\nНапример: 100",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена",callback_data=f"admfin:view:{did}")]])); return
            if action=="creditconfirm":
                st=context.user_data.get("admin_credit")
                if not st or st.get("step")!="preview" or st.get("dealer_id")!=did or not st.get("credit_change_id"):
                    await q.answer("Сессия изменения кредитного лимита устарела. Начните заново.",show_alert=True); return
                try:
                    FINANCE.set_credit_limit(dealer_id=did,new_limit=st["new_limit"],actor=f"telegram_admin:{u.id}",reason=st["reason"],idempotency_key=st["credit_change_id"])
                except ValueError as e:
                    if "duplicate idempotency_key" in str(e):
                        context.user_data.pop("admin_credit",None); await q.answer("Это изменение кредитного лимита уже было выполнено.",show_alert=True); return
                    await q.answer(f"Кредитный лимит не изменён: {e}",show_alert=True); return
                old_limit=st["current_limit"]; new_limit=st["new_limit"]; reason=st["reason"]; context.user_data.pop("admin_credit",None)
                await q.edit_message_text("\n".join(["✅ Кредитный лимит установлен","",f"Было: ${old_limit:,.2f}",f"Стало: ${new_limit:,.2f}",f"Основание: {reason}"]),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{did}")]])); return
            if action=="credit":
                context.user_data.pop("admin_credit",None)
                d=next((x for x in _admin_dealers() if x["id"]==did),None)
                current=FINANCE.credit_limit(did,"dealer","USD")
                context.user_data["admin_credit"]={"dealer_id":did,"dealer_name":d["name"] if d else did,"current_limit":current,"step":"limit"}
                await q.edit_message_text(f"⚙️ Кредитный лимит\n\nДилер: {d['name'] if d else did}\nТекущий лимит: ${current:,.2f}\n\nВведите новый кредитный лимит в USD.\nНапример: 500",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена",callback_data=f"admfin:view:{did}")]])); return
        if data.startswith("admtopup:"):
            parts=data.split(":")
            if len(parts)>=3 and parts[1]=="method":
                did,code=parts[2],parts[3]; method=next((m for m in FINANCE.payment_methods(did,"dealer") if m["code"]==code),None)
                if not method: await q.edit_message_text("Этот способ пополнения недоступен дилеру."); return
                d=next((x for x in _admin_dealers() if x["id"]==did),None); context.user_data["admin_topup"]={"dealer_id":did,"dealer_name":d["name"] if d else did,"method":method,"step":"amount"}
                await q.edit_message_text(f"🟢 Пополнить баланс [+]\n\nСпособ: {method['name']}\n\nВведите фактически полученную сумму в {method['payment_currency']}.\nНапример: 1000",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Отмена",callback_data=f"admfin:view:{did}")]])); return
            if len(parts)>=3 and parts[1]=="confirm":
                did=parts[2]; state=context.user_data.get("admin_topup")
                if not state or state.get("step")!="confirm" or state.get("dealer_id")!=did or not state.get("payment_id"):
                    await q.answer("Сессия пополнения устарела. Начните заново.",show_alert=True); return
                method=next((m for m in FINANCE.payment_methods(did,"dealer") if m["code"]==state["method"]["code"]),None)
                if not method:
                    await q.answer("Этот способ пополнения больше недоступен.",show_alert=True); return
                credited,fx_code,rate=_admin_topup_preview(state)
                try:
                    FINANCE.payment(buyer_type="dealer",buyer_id=did,currency="USD",amount=str(credited),payment_id=state["payment_id"],actor=f"telegram_admin:{u.id}",reason=state["reason"],payment_method=method["code"],source_amount=state["amount"],source_currency=method["payment_currency"],fx_code=fx_code,fx_rate=str(rate) if rate is not None else None)
                except ValueError as e:
                    if "duplicate idempotency_key" in str(e):
                        context.user_data.pop("admin_topup",None); await q.answer("Это пополнение уже было зачислено.",show_alert=True); return
                    raise
                context.user_data.pop("admin_topup",None)
                await q.edit_message_text(f"✅ Баланс пополнен на ${credited:,.2f}\n\nСпособ: {method['name']}\nОснование: {state['reason']}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← Баланс дилера",callback_data=f"admfin:view:{did}")]])); return
        await q.edit_message_text("ADMIN-раздел не найден."); return
    dealer=_dealer(update)
    if not dealer:
        await q.edit_message_text(ACCESS_DENIED); return
    if data=="noop": return
    if data=="balance":
        context.user_data.pop("finance_custom_wizard",None)
        await q.edit_message_text(_balance_text(dealer),reply_markup=_finance_filters_keyboard()); return
    if data=="finhome":
        context.user_data.pop("finance_custom_wizard",None)
        balance=FINANCE.balance(dealer["id"],"dealer","USD"); available=FINANCE.available_to_order(dealer["id"],"dealer","USD")
        def usd(v): return ("−" if v<0 else "")+f"${abs(v):,.2f}"
        text="\n".join(["OEMixiBOT","","Дилерский OEM-поиск и заказы.","",f"💵 Баланс: {usd(balance)}",f"Доступно для заказа: {usd(available)}"])
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("💵 Мой баланс",callback_data="balance")],[InlineKeyboardButton("📦 Мои заказы",callback_data="orders")]])
        await q.edit_message_text(text,reply_markup=kb); return
    if data=="paymethods":
        methods=FINANCE.payment_methods(dealer["id"],"dealer")
        lines=["➕ Пополнить баланс",""]
        buttons=[]
        if not methods: lines.append("Доступных способов пополнения сейчас нет.")
        for m in methods:
            fx=m.get("fx_code_primary")
            detail=f" · курс {fx}" if fx else ""
            lines.append(f"• {m['name']} · {m['payment_currency']}{detail}")
            buttons.append([InlineKeyboardButton(m['name'],callback_data="paymethod:"+m['code'])])
        buttons.append([InlineKeyboardButton("← Мой баланс",callback_data="balance"),InlineKeyboardButton("📦 Мои заказы",callback_data="orders")])
        await q.edit_message_text("\n".join(lines),reply_markup=InlineKeyboardMarkup(buttons)); return
    if data.startswith("paymethod:"):
        code=data.split(":",1)[1]
        method=next((m for m in FINANCE.payment_methods(dealer["id"],"dealer") if m['code']==code),None)
        if not method:
            await q.edit_message_text("Этот способ пополнения сейчас недоступен.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← К способам пополнения",callback_data="paymethods")]])); return
        await q.edit_message_text(f"{method['name']}\n\nСпособ доступен для вашего аккаунта. Оформление пополнения будет подключено следующим финансовым этапом.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← К способам пополнения",callback_data="paymethods")],[InlineKeyboardButton("← Мой баланс",callback_data="balance")]])); return
    if data.startswith("fcp:"):
        from datetime import date
        state=context.user_data.get("finance_custom_wizard")
        if not state:
            await q.edit_message_text("Выбор периода завершён.",reply_markup=_finance_filters_keyboard()); return
        parts=data.split(":")
        if parts[1]=="back":
            if state["step"]=="день": state["step"]="месяц"; state.pop("month",None)
            elif state["step"]=="месяц": state["step"]="год"; state.pop("year",None)
            await q.edit_message_text(_fcp_prompt(state),reply_markup=_fcp_keyboard(state)); return
        if len(parts)!=3: return
        v=int(parts[2])
        if parts[1]=="y" and state["step"]=="год": state["year"]=v; state["step"]="месяц"
        elif parts[1]=="m" and state["step"]=="месяц" and 1<=v<=12: state["month"]=v; state["step"]="день"
        elif parts[1]=="d" and state["step"]=="день":
            try: selected=date(state["year"],state["month"],v)
            except ValueError: await q.answer("Такой даты нет.",show_alert=True); return
            if state["side"]=="from":
                state["from_date"]=selected; state.update({"side":"to","step":"год"}); state.pop("year",None); state.pop("month",None)
            else:
                if selected<state["from_date"]:
                    await q.answer("Дата окончания не может быть раньше даты начала.",show_alert=True); return
                date_from=state["from_date"].strftime("%d.%m.%Y"); date_to=selected.strftime("%d.%m.%Y"); context.user_data.pop("finance_custom_wizard",None)
                text,rows=_finance_custom_history_text(dealer,date_from,date_to)
                await q.edit_message_text(text,reply_markup=_finance_custom_keyboard(rows)); return
        await q.edit_message_text(_fcp_prompt(state),reply_markup=_fcp_keyboard(state)); return
    if data.startswith("finhist:"):
        period=data.split(":",1)[1]
        if period=="custom":
            state={"side":"from","step":"год"}; context.user_data["finance_custom_wizard"]=state
            await q.edit_message_text(_fcp_prompt(state),reply_markup=_fcp_keyboard(state)); return
        if period not in {"7","30","all"}:
            await q.edit_message_text("Неизвестный период.",reply_markup=_finance_filters_keyboard()); return
        await q.edit_message_text(_finance_history_text(dealer,period),reply_markup=_finance_history_buttons(dealer,period)); return
    if data.startswith("finevent:"):
        parts=data.split(":")
        if len(parts)!=3 or parts[2] not in {"7","30","all"}:
            await q.edit_message_text("Финансовая операция не найдена."); return
        event=FINANCE.event(parts[1],dealer["id"],"dealer","USD")
        if not event:
            await q.edit_message_text("Финансовая операция не найдена."); return
        await q.edit_message_text(_finance_event_text(event),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("← К истории",callback_data="finhist:"+parts[2])],[InlineKeyboardButton("← Мой баланс",callback_data="balance"),InlineKeyboardButton("📦 Мои заказы",callback_data="orders")]])); return
    if data.startswith("order:"):
        oid=data.split(":",1)[1]
        order=STORE.order_for_buyer(oid,dealer["id"],"dealer","telegram")
        if not order:
            await q.edit_message_text("\u0417\u0430\u043a\u0430\u0437 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d."); return
        buttons=[[InlineKeyboardButton("\U0001f4dc \u0418\u0441\u0442\u043e\u0440\u0438\u044f \u0437\u0430\u043a\u0430\u0437\u0430",callback_data="history:"+oid)]]
        if STORE.receivable_shipment_for_buyer(oid,dealer["id"]):
            buttons.append([InlineKeyboardButton("\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u044c \u043f\u043e\u043b\u0443\u0447\u0435\u043d\u0438\u0435",callback_data="receive:"+oid)])
        buttons.append([InlineKeyboardButton("\u2190 \u041c\u043e\u0438 \u0437\u0430\u043a\u0430\u0437\u044b",callback_data="orders")])
        await q.edit_message_text(_order_text(order),reply_markup=InlineKeyboardMarkup(buttons)); return
    if data.startswith("receive:"):
        oid=data.split(":",1)[1]
        shipment=STORE.receivable_shipment_for_buyer(oid,dealer["id"])
        if not shipment:
            await q.edit_message_text("\u041d\u0435\u0442 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u044f, \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u043e\u0433\u043e \u0434\u043b\u044f \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044f."); return
        await q.edit_message_text("\u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0434\u0438\u0442\u0435, \u0447\u0442\u043e \u0432\u044b \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0438 \u043f\u043e\u043b\u0443\u0447\u0438\u043b\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435.",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2705 \u041f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0430\u044e, \u0447\u0442\u043e \u043f\u043e\u043b\u0443\u0447\u0438\u043b \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435",callback_data="receive_yes:"+oid)],[InlineKeyboardButton("\u2190 \u041e\u0442\u043c\u0435\u043d\u0430",callback_data="order:"+oid)]])); return
    if data.startswith("receive_yes:"):
        oid=data.split(":",1)[1]
        try: STORE.confirm_receipt_for_buyer(oid,dealer["id"])
        except ValueError:
            await q.edit_message_text("\u041f\u043e\u043b\u0443\u0447\u0435\u043d\u0438\u0435 \u0443\u0436\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e \u0438\u043b\u0438 \u043e\u0442\u043f\u0440\u0430\u0432\u043b\u0435\u043d\u0438\u0435 \u0435\u0449\u0451 \u043d\u0435 \u0433\u043e\u0442\u043e\u0432\u043e \u043a \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u0438\u044e."); return
        order=STORE.order_for_buyer(oid,dealer["id"],"dealer","telegram")
        await q.edit_message_text("\u2705 \u041f\u043e\u043b\u0443\u0447\u0435\u043d\u0438\u0435 \u043f\u043e\u0434\u0442\u0432\u0435\u0440\u0436\u0434\u0435\u043d\u043e.\n\n"+_order_text(order),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\U0001f4dc \u0418\u0441\u0442\u043e\u0440\u0438\u044f \u0437\u0430\u043a\u0430\u0437\u0430",callback_data="history:"+oid)],[InlineKeyboardButton("\u2190 \u041c\u043e\u0438 \u0437\u0430\u043a\u0430\u0437\u044b",callback_data="orders")]])); return
    if data.startswith("history:"):
        oid=data.split(":",1)[1]
        history=STORE.order_history_for_buyer(oid,dealer["id"],"dealer","telegram")
        if not history:
            await q.edit_message_text("\u0417\u0430\u043a\u0430\u0437 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d."); return
        await q.edit_message_text(_history_text(history),reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("\u2190 \u041a \u0437\u0430\u043a\u0430\u0437\u0443",callback_data="order:"+oid)]])); return
    if data=="orders":
        orders=STORE.orders_for_buyer(dealer["id"],"dealer","telegram")
        await q.edit_message_text(_orders_text(orders),reply_markup=_orders_keyboard(orders) if orders else None); return
    if data=="cart":
        cart=STORE.cart_view(dealer["id"])
        await q.edit_message_text(cart_message(cart) if cart["items"] else "🛒 Корзина пуста.",reply_markup=_cart_keyboard(cart)); return
    audit=None
    if data.startswith("add:"):
        oem=data[4:]; offer=context.user_data.get("offer:"+oem)
        if not offer:
            await q.edit_message_text("Карточка устарела. Повторите поиск OEM."); return
        before=STORE.cart_view(dealer["id"])
        oldrow=next((x for x in before["items"] if x["oem"]==(offer.get("oem") or oem)),None)
        qty_before=oldrow["qty"] if oldrow else 0
        STORE.cart_add(dealer["id"],offer.get("oem") or oem,offer["dl_usd"],offer.get("manufacturer"),
                       offer.get("name"),offer.get("item_type"),1)
        audit=("add",offer.get("oem") or oem,qty_before,offer["dl_usd"])
    elif data.startswith(("q+:","q-:","del:")):
        prefix,oem=data.split(":",1); cart=STORE.cart_view(dealer["id"])
        row=next((x for x in cart["items"] if x["oem"]==oem),None)
        if row:
            qty_before=row["qty"]
            qty=0 if prefix=="del" else row["qty"]+(1 if prefix=="q+" else -1)
            STORE.cart_set_qty(dealer["id"],oem,qty)
            audit=(prefix,oem,qty_before,row["dl_usd"])
    elif data=="checkout":
        cart=STORE.cart_view(dealer["id"])
        if not cart["items"]:
            await q.edit_message_text("Корзина пуста."); return
        month_codes="ABCDEFGHIJKL"
        now=datetime.now()
        for _ in range(100):
            oid=f"E{now.day:02d}{month_codes[now.month-1]}-{secrets.randbelow(10000):04d}"
            with STORE.db() as c:
                exists=c.execute("SELECT 1 FROM dealer_orders WHERE id=? LIMIT 1",(oid,)).fetchone()
            if not exists: break
        else:
            raise RuntimeError("Could not generate unique order ID")
        try:
            total=STORE.checkout_cart(dealer["id"],oid)
        except ValueError as e:
            msg=str(e)
            if msg.startswith("INSUFFICIENT_AVAILABLE:"):
                from decimal import Decimal
                _,available_s,required_s=msg.split(":",2)
                available=Decimal(available_s); required=Decimal(required_s); shortage=required-available
                usd=lambda v:("−" if v<0 else "")+f"${abs(v):,.2f}"
                text="\n".join(["⛔ Недостаточно доступного лимита для заказа.","",f"Доступно для заказа: {usd(available)}",f"Сумма корзины: {usd(required)}",f"Не хватает: {usd(shortage)}","","Пополните баланс или уменьшите корзину."])
                await q.edit_message_text(text,reply_markup=_cart_keyboard(cart)); return
            raise
        await q.edit_message_text("✅ Заказ "+oid+" подтверждён.\nСумма заказа: $"+format(total,".2f")); return
    cart=STORE.cart_view(dealer["id"])
    if audit:
        action,oem,qty_before,unit_price=audit
        row_after=next((x for x in cart["items"] if x["oem"]==oem),None)
        qty_after=row_after["qty"] if row_after else 0
        total_after=cart.get("total_usd")
        logging.info("OEMIXI_CART_AUDIT timestamp=%s dealer=%s callback=%s OEM=%s qty_before=%s qty_after=%s unit_price=%s total_after=%s",
                     datetime.now().isoformat(timespec="seconds"),dealer["id"],action,oem,qty_before,qty_after,unit_price,total_after)
    await q.edit_message_text(cart_message(cart) if cart["items"] else "🛒 Корзина пуста.",reply_markup=_cart_keyboard(cart))


def _aging_no_due_days():
    try: value=int(os.getenv("OEMIXIBOT_NO_DUE_ALERT_DAYS","7"))
    except ValueError: value=7
    return value if 1<=value<=3650 else 7

def _aging_alert_text(row):
    labels={
        "overdue_1_7":("🟡","Просрочка 1–7 дней"),
        "overdue_8_30":("🟠","Просрочка 8–30 дней"),
        "overdue_30_plus":("🔴","Просрочка 30+ дней"),
        "no_due_old":("⚪","Обязательство без срока"),
    }
    icon,label=labels[row["alert_key"]]
    dealer=next((d for d in _admin_dealers() if d["id"]==row["dealer_id"]),None)
    due=row["due_date"] or "не установлен"
    lines=[f"{icon} AGING ALERT", "", f"Дилер: {dealer['name'] if dealer else row['dealer_id']}",
           f"Обязательство: {_admin_aging_source_label(row)}", f"Остаток: ${row['outstanding']:,.2f}",
           f"Срок оплаты: {due}", f"Статус: {label}"]
    if row["alert_key"]=="no_due_old":
        lines.append(f"Возраст без срока: {row['age_days']} дн. · порог {row['no_due_days']} дн.")
    elif row["days_overdue"]>0:
        lines.append(f"Дней просрочки: {row['days_overdue']}")
    return "\n".join(lines)

async def _aging_alert_job(context):
    threshold=_aging_no_due_days()
    for admin_id in sorted(telegram_admin_ids()):
        try:
            candidates=FINANCE.aging_alert_candidates(admin_id,no_due_days=threshold)
        except Exception:
            logging.exception("OEMIXI_AGING_ALERT_SCAN_FAILED admin=%s",admin_id)
            continue
        logging.info("OEMIXI_AGING_ALERT_SCAN admin=%s candidates=%s threshold=%s",admin_id,len(candidates),threshold)
        for row in candidates:
            kb=InlineKeyboardMarkup([
                [InlineKeyboardButton("⏳ Открыть обязательство",callback_data=f"admagingsum:item:{row['source_event_id']}:{row['dealer_id']}:all:0")],
                [InlineKeyboardButton("⏳ Aging по дилерам",callback_data="admagingsum:all:0")],
            ])
            try:
                await context.bot.send_message(chat_id=admin_id,text=_aging_alert_text(row),reply_markup=kb)
            except Exception:
                logging.exception("OEMIXI_AGING_ALERT_SEND_FAILED admin=%s source_event=%s key=%s",admin_id,row["source_event_id"],row["alert_key"])
                continue
            try:
                FINANCE.acknowledge_aging_alert(source_event_id=row["source_event_id"],admin_id=admin_id,alert_key=row["alert_key"])
                logging.info("OEMIXI_AGING_ALERT_SENT admin=%s source_event=%s key=%s",admin_id,row["source_event_id"],row["alert_key"])
            except Exception:
                logging.exception("OEMIXI_AGING_ALERT_ACK_FAILED admin=%s source_event=%s key=%s",admin_id,row["source_event_id"],row["alert_key"])

def build_application(token=None):
    STORE.init(); STORE.ensure_cart_schema()
    token=token or os.getenv("OEMIXIBOT_TOKEN","").strip()
    if not token: raise RuntimeError("OEMIXIBOT_TOKEN is not configured")
    app=Application.builder().token(token).build()
    if app.job_queue is not None:
        app.job_queue.run_repeating(_aging_alert_job,interval=3600,first=20,name="aging_alerts")
    else:
        logging.warning("OEMIXI_AGING_ALERT_JOB_DISABLED job_queue unavailable")
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CommandHandler("cart",cart_command))
    app.add_handler(CommandHandler("orders",orders_command))
    app.add_handler(CommandHandler("balance",balance_command))
    app.add_handler(CommandHandler("admin",admin_command))
    app.add_handler(CallbackQueryHandler(callback,pattern=r"^(?:add:|q\+:|q-:|del:|checkout$|cart$|orders$|order:|history:|receive:|receive_yes:|balance$|finhist:|finevent:|paymethods$|paymethod:|fcp:|admsearch$|admhome$|admdealer:|admdealers$|admdebt$|admdebt:|admagingsum:|admaging:|admfin:|admfinevent:|admdebit:|admtopup:|noop$)"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,oem_message))
    return app

def main():
    import logging
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.info("OEMixiBOT polling runtime starting")
    build_application().run_polling(drop_pending_updates=False)

if __name__=="__main__":
    main()
