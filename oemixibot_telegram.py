"""OEMixiBOT Telegram interface foundation. No production startup wiring yet."""
import os
from oemixibot_store import OemixiStore

ACCESS_DENIED = "⛔ Доступ к OEMixiBOT закрыт. Обратитесь к администратору EXTREMIZER."
WELCOME = "OEMixiBOT\n\nВведите OEM одним сообщением."

class DealerAccess:
    def __init__(self, store: OemixiStore): self.store=store
    def dealer_for_telegram(self, telegram_id:int):
        with self.store.db() as c:
            row=c.execute("SELECT * FROM dealers WHERE telegram_id=? AND active=1",(telegram_id,)).fetchone()
            return dict(row) if row else None

def delay_message(brand,oem,qty,expected_date=None):
    when=(f"Ориентировочная дата появления у производителя: {expected_date}" if expected_date
          else "Ориентировочная дата появления у производителя: открытая дата")
    return (f"⚠️ Задержка поставки\n\n{brand}\nOEM: {oem}\nКоличество: {qty} шт.\n\n"
            f"Поставщик в штатах пока не может отгрузить позицию.\n{when}")

DELAY_BUTTONS=(("⏳ Ждать","delay_wait"),("❌ Запросить отказ","delay_cancel"))

def normalize_oem(value):
    return "".join(ch for ch in str(value).strip().upper() if ch.isalnum() or ch in "-_")

def price_card(brand,oem,name,dl_usd,coefficient,item_type=None,weight=None,volume=None):
    dlp=round(float(dl_usd)*float(coefficient)+1e-9,2)
    title = (brand or "Производитель определяется") + (" — " + name if name else "")
    if weight is not None and volume is not None:
        weight_text = format(weight, ".2f").rstrip("0").rstrip(".") + " кг / " + format(volume, ".2f").rstrip("0").rstrip(".") + " кг"
    elif weight is not None:
        weight_text = format(weight, ".2f").rstrip("0").rstrip(".") + " кг"
    elif volume is not None:
        weight_text = "нет фактического веса / " + format(volume, ".2f").rstrip("0").rstrip(".") + " кг"
    else:
        weight_text = "данных по весу нет"
    return title + "\n🇺🇸 склад США— $" + format(dlp, ".2f") + " (" + weight_text + ")"

def cart_message(cart):
    lines=["🛒 КОРЗИНА"]
    for n,r in enumerate(cart["items"],1):
        lines.append(str(n)+". "+r["oem"]+" — "+str(r["qty"])+" шт. × $"+format(r["dlp_usd"],".2f"))
    lines.append("ИТОГО: $"+format(cart["total_usd"],".2f"))
    return "\n".join(lines)

CARD_BUTTONS=(("➕ В корзину","cart_add"),)
CART_BUTTONS=(("➖","qty_minus"),("➕","qty_plus"),("🗑 Удалить","qty_delete"),("✅ Подтвердить заказ","checkout_confirm"))
