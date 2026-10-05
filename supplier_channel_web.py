from html import escape
LABELS={"telegram":"Telegram","whatsapp":"WhatsApp","email":"e-mail","custom":"Свой канал"}
def render(service,channels):
 rows=channels.list_warehouses(service.db_file); out=['<!doctype html><html><head><meta charset="utf-8"><link rel="stylesheet" href="/static/supplier-admin.css"></head><body><main><a class="back" href="/admin/supplier-orders">← Заказы поставщикам</a><h1>⚙️ Каналы складов</h1>']
 for x in rows:
  current=LABELS.get(x.get("channel"),"Не настроен")
  out.append(f'''<section class="warehouse"><h2>{escape(x["public_name"])}</h2><p>Сейчас: <b>{escape(current)}</b> {escape(x.get("recipient") or "")}</p>
  <select id="ch-{x["id"]}"><option value="telegram">Telegram</option><option value="whatsapp">WhatsApp</option><option value="email">e-mail</option><option value="custom">Свой канал</option></select>
  <input id="rec-{x["id"]}" placeholder="@username / телефон / e-mail / адрес">
  <input id="custom-{x["id"]}" placeholder="Название своего канала">
  <button class="primary channel-save" data-id="{x["id"]}">💾 Сохранить</button></section>''')
 out.append('<div id="msg"></div></main><script src="/static/supplier-admin.js"></script></body></html>'); return "".join(out)
