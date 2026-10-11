RULES_BY_CHAT_ID = {
 -1001660190650: 'https://t.me/c/1660190650/8471',
 -1001501348314: 'https://t.me/c/1501348314/5548',
 -1001825262529: 'https://t.me/c/1825262529/1349',
 -1001153012765: 'https://t.me/c/1153012765/1860',
 -1001625373275: 'https://t.me/c/1625373275/1577',
}

from html import escape

def welcome_text(chat_id):
    url = RULES_BY_CHAT_ID.get(int(chat_id))
    if not url:
        return None
    return (
        "дарова 🖐🏻\n\n"
        f'внимательно читаем <b><a href="{escape(url, quote=True)}">раздел правил</a></b> и добро пожаловать 🤝\n\n'
        "синяя кнопка <b>ПРОЦЕНИТЬ</b> справа вверху покажет:\n"
        "• цену заказа из США\n"
        "• наличие и стоимость в РФ\n\n"
        "просто нажми ее и отправь один каталожный номер в личный чат с ботом 👌"
    )

async def welcome_new_members(update, context):
    chat = update.effective_chat
    message = update.effective_message
    if not chat or not message or str(chat.type) not in ("group", "supergroup"):
        return
    text = welcome_text(chat.id)
    if not text:
        return
    seen = set()
    for member in (getattr(message, "new_chat_members", None) or []):
        uid = getattr(member, "id", None)
        if uid is None or getattr(member, "is_bot", False) or uid in seen:
            continue
        seen.add(uid)
        await context.bot.send_message(
            chat_id=int(chat.id), text=text, parse_mode="HTML",
            disable_web_page_preview=True,
        )
