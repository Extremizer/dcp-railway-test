const brandLogo = document.querySelector(".brand-logo");
if (brandLogo) {
  const showBrandLogo = () => { brandLogo.hidden = false; };
  const hideBrandLogo = () => { brandLogo.hidden = true; };
  brandLogo.addEventListener("load", showBrandLogo);
  brandLogo.addEventListener("error", hideBrandLogo);
  if (brandLogo.complete && brandLogo.naturalWidth > 0) showBrandLogo();
}

const $ = (id) => document.getElementById(id);
const state = $("state");
const product = $("product");
const form = $("searchForm");
const input = $("oemInput");
const searchButton = $("searchButton");
const cartPanel = $("cartPanel");
const scrim = $("scrim");
let currentProduct = null;
let currentQty = 1;
let stockPollSerial = 0;
let cart = [];
try {
  const stored = JSON.parse(localStorage.getItem("extremizer_web2_cart") || "[]");
  if (Array.isArray(stored)) cart = stored.filter((x) =>
    x && typeof x.key === "string" && Number.isInteger(x.qty) &&
    x.qty > 0 && x.qty <= 99 && Number.isFinite(x.unit_rub) && x.unit_rub >= 0);
} catch (_) { cart = []; }

function node(tag, cls, value) {
  const el = document.createElement(tag);
  if (cls) el.className = cls;
  if (value != null) el.textContent = String(value);
  return el;
}


const validPrice = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const validQty = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const label = (value, fallback = "") => value == null ? fallback : String(value);
const usableOffer = (offer) => offer && typeof offer.key === "string" && offer.key.length > 0 &&
  (offer.source === "usa" || offer.source === "warehouse") && validPrice(offer.price_rub) &&
  offer.can_add === true && (offer.source === "usa" || (validQty(offer.available_quantity) && offer.available_quantity >= 1 && offer.warehouse_id != null));
const rub = (value) => !validPrice(value)
  ? "Цена уточняется"
  : new Intl.NumberFormat("ru-RU").format(value) + " ₽";
const usaRub = (value) => !validPrice(value)
  ? "Цена уточняется"
  : new Intl.NumberFormat("ru-RU").format(value) + "* ₽";
const qtyText = (value) => new Intl.NumberFormat(
  "ru-RU", { maximumFractionDigits: 3 }
).format(value);

function showState(text, kind = "info") {
  state.textContent = text;
  state.classList.remove("hidden", "error", "loading");
  if (kind === "error") state.classList.add("error");
  if (kind === "loading") state.classList.add("loading");
}

function hideState() {
  state.classList.add("hidden");
  state.classList.remove("error", "loading");
}

function setSearchBusy(busy) {
  form.setAttribute("aria-busy", busy ? "true" : "false");
  searchButton.disabled = busy;
  searchButton.textContent = busy ? "Ищем…" : "Найти";
}

function renderStock(stockData) {
  const root = $("stock");
  root.replaceChildren();
  const warehouses = Array.isArray(stockData?.warehouses) ? stockData.warehouses : [];
  for (const row of warehouses) {
    if (!row || typeof row !== "object" || !validQty(row.quantity)) continue;
    const el = node("div", "stock-row");
    const price = !validPrice(row.price_rub) ? "" : " • " + rub(row.price_rub);
    el.append(node("span", "", label(row.warehouse, "Склад уточняется")), node("b", "", qtyText(row.quantity) + " шт." + price));
    root.appendChild(el);
  }
  if (warehouses.length && stockData.refreshing) {
    root.appendChild(node("div", "stock-note", "Проверяем остальные склады…"));
  } else if (!warehouses.length) {
    root.textContent = stockData?.refreshing ? "Проверяем склады…" :
      (stockData?.known ? "Подтверждённого остатка сейчас нет." : "Остаток уточняется.");
  }
}

function renderOffers(offers = []) {
  const root = $("offers");
  root.replaceChildren();
  if (!Array.isArray(offers) || !offers.length) {
    root.appendChild(node("div", "offer-empty", "Доступные варианты покупки уточняются."));
    return;
  }
  for (const offer of offers) {
    if (!offer || typeof offer !== "object" || !["usa", "warehouse"].includes(offer.source)) continue;
    const card = node("article", "offer-card");
    const meta = node("div", "offer-meta");
    meta.appendChild(node("div", "offer-source", offer.source === "usa"
      ? "🇺🇸 склад США" : "🇷🇺 " + label(offer.label, "Склад уточняется")));
    meta.appendChild(node("div", "offer-availability", offer.source === "usa"
      ? "Заказ из США" : "В наличии: " + (validQty(offer.available_quantity) ? qtyText(offer.available_quantity) + " шт." : "уточняется")));
    const key = String(currentProduct?.manufacturer ?? "") + "|" + String(currentProduct?.oem ?? "") + "|" + String(offer.key);
    const existing = cart.find((x) => x.key === key)?.qty || 0;
    if (existing) meta.appendChild(node("div", "offer-in-cart", "🛒 В корзине: " + existing + " шт."));
    const buy = node("div", "offer-buy");
    buy.appendChild(node("strong", "", offer.source === "usa" ? usaRub(offer.price_rub) : rub(offer.price_rub)));
    const button = node("button", "", !offer.can_add ? "Цена уточняется" : existing ? "Добавить ещё" : "В корзину");
    button.type = "button";
    button.dataset.offerKey = String(offer.key ?? "");
    button.disabled = !usableOffer(offer);
    buy.appendChild(button);
    card.append(meta, buy);
    root.appendChild(card);
  }
}

async function pollStock(card, serial, attempt = 0) {
  if (serial !== stockPollSerial) return;
  if (attempt >= 40) {
    if (currentProduct === card) {
      currentProduct.stock.refreshing = false;
      renderStock(currentProduct.stock);
    }
    return;
  }
  await new Promise((resolve) => setTimeout(resolve, 1500));
  if (serial !== stockPollSerial || currentProduct !== card) return;
  try {
    const params = new URLSearchParams({ manufacturer: card.manufacturer });
    const response = await fetch(`/api/v1/oem/${encodeURIComponent(card.oem)}/stock?${params}`);
    if (!response.ok) throw new Error("stock_refresh_failed");
    const stockData = await response.json();
    if (serial !== stockPollSerial || currentProduct !== card) return;
    if (!stockData || !Array.isArray(stockData.warehouses)) throw new Error("invalid_stock_response");
    currentProduct.stock = stockData;
    renderStock(stockData);
    if (Array.isArray(stockData.offers)) {
      currentProduct.offers = stockData.offers;
      renderOffers(currentProduct.offers);
    }
    if (stockData.refreshing) pollStock(card, serial, attempt + 1);
  } catch (_) {
    if (attempt < 5) {
      pollStock(card, serial, attempt + 1);
    } else if (currentProduct === card) {
      currentProduct.stock.refreshing = false;
      renderStock(currentProduct.stock);
    }
  }
}

function renderProduct(card) {
  currentProduct = card;
  currentQty = 1;
  $("qtyValue").textContent = "1";
  $("productBrand").textContent = [card.manufacturer, card.catalog].filter((x) => typeof x === "string" && x.trim()).join(" • ");
  $("productName").textContent = typeof card.name === "string" && card.name.trim() ? card.name : "Название уточняется";
  $("productOem").textContent = label(card.oem);
  const pricePanel = document.querySelector(".price-panel");
  pricePanel?.classList.toggle("hidden", Boolean(card.warehouse_only));
  $("customerPrice").textContent = !validPrice(card.price.customer_rub)
    ? rub(null)
    : usaRub(card.price.customer_rub);

  const replacement = $("replacement");
  if (card.requested_oem && card.requested_oem !== card.oem) {
    replacement.textContent = `Запрошен ${card.requested_oem} → актуальный ${card.oem}`;
    replacement.classList.remove("hidden");
  } else {
    replacement.classList.add("hidden");
  }

  const msrp = $("msrpBlock");
  const benefitLine = $("benefitLine");
  const usaDeliveryNotice = $("usaDeliveryNotice");
  const usaDeliveryText = $("usaDeliveryText");
  const showUsaTerms = !card.warehouse_only && validPrice(card.price.customer_rub);
  if (showUsaTerms && validPrice(card.price.msrp_rub) && card.price.msrp_rub > card.price.customer_rub) {
    $("msrpPrice").textContent = rub(card.price.msrp_rub);
    msrp.classList.remove("hidden");
    if (typeof card.price.benefit_pct === "number" && Number.isFinite(card.price.benefit_pct)) {
      $("benefit").textContent = `${card.price.benefit_pct}%`;
      benefitLine.classList.remove("hidden");
    } else {
      $("benefit").textContent = "";
      benefitLine.classList.add("hidden");
    }
  } else {
    $("msrpPrice").textContent = "";
    $("benefit").textContent = "";
    benefitLine.classList.add("hidden");
    msrp.classList.add("hidden");
  }

  if (showUsaTerms && card.delivery_notice) {
    usaDeliveryText.textContent = String(card.delivery_notice).replace(/^\*\s*-\s*/, "");
    usaDeliveryNotice.classList.remove("hidden");
  } else {
    usaDeliveryText.textContent = "";
    usaDeliveryNotice.classList.add("hidden");
  }

  stockPollSerial += 1;
  const stockSerial = stockPollSerial;
  renderStock(card.stock);
  renderOffers(card.offers || []);
  if (card.stock?.refreshing) pollStock(card, stockSerial);

  const weight = $("weight");
  weight.replaceChildren();
  if (card.weight) {
    for (const [value, label] of [[card.weight.actual_kg, "Фактический: "],
      [card.weight.volume_kg, "Объёмный: "]]) {
      if (value != null && Number(value) > 0) {
        const line = node("div", "weight-line", label);
        line.appendChild(node("b", "", String(value) + " кг / шт."));
        weight.appendChild(line);
      }
    }
    weight.appendChild(node("div", "weight-note", card.weight.notice));
  } else {
    weight.textContent = "Вес неизвестен.";
  }

  const deliveryNotice = $("deliveryNotice");
  deliveryNotice.textContent = "";
  deliveryNotice.classList.add("hidden");
  product.classList.remove("hidden");
  product.scrollIntoView({ behavior: "smooth", block: "start" });
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

const SEARCH_TIMEOUT_MS = 15000;
let lastSearchStatus = "idle";

async function searchOem(oem) {
  if (form.getAttribute("aria-busy") === "true") return;
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), SEARCH_TIMEOUT_MS);
  product.classList.add("hidden");
  stockPollSerial += 1;
  currentProduct = null;
  setSearchBusy(true);
  lastSearchStatus = "loading";
  showState("Ищем…", "loading");
  try {
    const response = await fetch(`/api/v1/oem/${encodeURIComponent(oem)}`, {
      signal: controller.signal,
    });
    if (response.status === 404) {
      lastSearchStatus = "not_found";
      showState("OEM " + oem + " не найден в проверенной базе. Проверь номер и попробуй ещё раз.", "error");
      return;
    }
    if (!response.ok) throw new Error("search_api_unavailable");
    const card = await response.json();
    if (!card || typeof card.oem !== "string" || !card.oem.trim() || !card.price || typeof card.price !== "object" || !card.stock || typeof card.stock !== "object" || !Array.isArray(card.offers)) {
      throw new Error("invalid_search_response");
    }
    lastSearchStatus = "found";
    hideState();
    renderProduct(card);
  } catch (error) {
    lastSearchStatus = "technical_error";
    const timedOut = error?.name === "AbortError";
    showState(timedOut
      ? "Поиск занял слишком много времени. Проверь подключение и повтори запрос."
      : "Техническая ошибка поиска. Повтори запрос чуть позже.", "error");
  } finally {
    window.clearTimeout(timeout);
    setSearchBusy(false);
  }
}

form.addEventListener("submit", (event) => {
  event.preventDefault();
  const oem = input.value.trim();
  if (!oem) return;
  searchOem(oem);
});

$("qtyMinus").addEventListener("click", () => {
  currentQty = Math.max(1, currentQty - 1);
  $("qtyValue").textContent = currentQty;
});
$("qtyPlus").addEventListener("click", () => {
  currentQty = Math.min(99, currentQty + 1);
  $("qtyValue").textContent = currentQty;
});

$("offers").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const offerKey = target.closest("button[data-offer-key]")?.dataset.offerKey;
  if (!offerKey || !currentProduct) return;

  const offer = (currentProduct.offers || []).find((x) => x.key === offerKey);
  if (!usableOffer(offer) || typeof currentProduct.oem !== "string" || !currentProduct.oem.trim()) return;

  const key = `${currentProduct.manufacturer}|${currentProduct.oem}|${offer.key}`;
  const maxQty = offer.available_quantity == null
    ? 99
    : Math.max(0, Math.floor(Number(offer.available_quantity)));
  if (maxQty < 1) return;

  const existing = cart.find((x) => x.key === key);
  if (existing) {
    existing.qty = Math.min(maxQty, existing.qty + currentQty);
  } else {
    cart.push({
      key,
      manufacturer: currentProduct.manufacturer,
      oem: currentProduct.oem,
      requested_oem: currentProduct.requested_oem,
      name: currentProduct.name,
      offer_source: offer.source,
      warehouse_id: offer.warehouse_id,
      source_label: offer.source === "usa" ? "склад США" : offer.label,
      unit_rub: offer.price_rub,
      available_quantity: offer.available_quantity,
      qty: Math.min(maxQty, currentQty),
    });
  }
  saveCart();
  openCart();
});

function saveCart() {
  localStorage.setItem("extremizer_web2_cart", JSON.stringify(cart));
  renderCart();
  if (currentProduct) renderOffers(currentProduct.offers || []);
}

function renderCart() {
  const itemCount = cart.reduce((sum, x) => sum + x.qty, 0);
  $("cartCount").textContent = itemCount;
  $("cartButton").classList.toggle("has-items", itemCount > 0);
  const root = $("cartItems");
  root.replaceChildren();
  $("cartEmpty").classList.toggle("hidden", cart.length > 0);
  $("cartFooter").classList.toggle("hidden", cart.length === 0);
  let total = 0;
  cart.forEach((item, index) => {
    total += Number(item.unit_rub) * item.qty;
    const el = node("div", "cart-item");
    const top = node("div", "cart-item-top");
    const details = node("div");
    details.append(
      node("div", "cart-item-name", item.name || item.manufacturer),
      node("div", "cart-item-oem", item.oem),
      node("div", "cart-item-source", (item.offer_source === "warehouse" ? "🇷🇺 " : "🇺🇸 ") + String(item.source_label || "склад США"))
    );
    const remove = node("button", "remove", "Удалить");
    remove.type = "button"; remove.dataset.remove = String(index);
    top.append(details, remove);
    const bottom = node("div", "cart-item-bottom");
    const qty = node("div", "cart-mini-qty");
    const minus = node("button", "", "−");
    minus.type = "button"; minus.dataset.minus = String(index);
    const plus = node("button", "", "+");
    plus.type = "button"; plus.dataset.plus = String(index);
    qty.append(minus, node("b", "", item.qty), plus);
    bottom.append(qty, node("strong", "", rub(Number(item.unit_rub) * item.qty)));
    el.append(top, bottom);
    root.appendChild(el);
  });
  $("cartTotal").textContent = cart.length ? rub(total) : "уточняется";
  $("cartButtonTotal").textContent = cart.length ? rub(total) : "0 ₽";
}

function openCart() {
  document.body.classList.add("cart-open");
  cartPanel.classList.add("open");
  cartPanel.setAttribute("aria-hidden", "false");
  scrim.classList.remove("hidden");
}
function closeCart() {
  document.body.classList.remove("cart-open");
  cartPanel.classList.remove("open");
  cartPanel.setAttribute("aria-hidden", "true");
  scrim.classList.add("hidden");
}

$("cartButton").addEventListener("click", openCart);
$("cartClose").addEventListener("click", closeCart);
scrim.addEventListener("click", closeCart);
$("continueSearchButton").addEventListener("click", () => {
  closeCart();
  input.value = "";
  window.scrollTo({ top: 0, behavior: "smooth" });
  window.setTimeout(() => input.focus(), 250);
});

$("cartItems").addEventListener("click", (event) => {
  const target = event.target;
  if (!(target instanceof HTMLElement)) return;
  const remove = target.dataset.remove;
  const minus = target.dataset.minus;
  const plus = target.dataset.plus;
  if (remove !== undefined) cart.splice(Number(remove), 1);
  if (minus !== undefined) {
    const item = cart[Number(minus)];
    if (item) {
      item.qty -= 1;
      if (item.qty <= 0) cart.splice(Number(minus), 1);
    }
  }
  if (plus !== undefined) {
    const item = cart[Number(plus)];
    if (item) {
      const maxQty = item.available_quantity == null
        ? 99
        : Math.max(1, Math.floor(Number(item.available_quantity)));
      item.qty = Math.min(maxQty, item.qty + 1);
    }
  }
  saveCart();
});

$("telegramButton").addEventListener("click", async () => {
  if (!cart.length) return;
  const button = $("telegramButton");
  const old = button.textContent;
  button.disabled = true;
  button.textContent = "Готовим передачу…";
  try {
    const response = await fetch("/api/v1/handoff", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: cart.map((x) => ({
          manufacturer: x.manufacturer,
          oem: x.oem,
          requested_oem: x.requested_oem,
          offer_source: x.offer_source || "usa",
          warehouse_id: x.warehouse_id ?? null,
          qty: x.qty,
        })),
      }),
    });
    if (!response.ok) throw new Error("Не удалось передать корзину.");
    const data = await response.json();
    const url = new URL(String(data.telegram_url ?? ""));
    if (url.protocol !== "https:" || !["t.me", "telegram.me"].includes(url.hostname)) {
      throw new Error("Некорректная ссылка передачи в Telegram.");
    }
    window.location.href = url.href;
  } catch (error) {
    alert(error.message || "Не удалось открыть Telegram.");
    button.disabled = false;
    button.textContent = old;
  }
});

renderCart();
