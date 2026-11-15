const $ = (id) => document.getElementById(id);
const state = $("state");
const product = $("product");
const form = $("searchForm");
const input = $("oemInput");
const cartPanel = $("cartPanel");
const scrim = $("scrim");
let currentProduct = null;
let currentQty = 1;
let stockPollSerial = 0;
let cart = JSON.parse(localStorage.getItem("extremizer_web1_cart") || "[]");

const rub = (value) => value == null
  ? "Цена уточняется"
  : new Intl.NumberFormat("ru-RU").format(value) + " ₽";
const qtyText = (value) => new Intl.NumberFormat(
  "ru-RU", { maximumFractionDigits: 3 }
).format(value);

function showState(text, error = false) {
  state.textContent = text;
  state.classList.remove("hidden", "error");
  if (error) state.classList.add("error");
}

function hideState() {
  state.classList.add("hidden");
}

function renderStock(stockData) {
  const stock = $("stock");
  stock.innerHTML = "";
  if (stockData.warehouses.length) {
    stockData.warehouses.forEach((row) => {
      const el = document.createElement("div");
      el.className = "stock-row";
      const price = row.price_rub == null ? "" : ` • ${rub(row.price_rub)}`;
      el.innerHTML = `<span>${escapeHtml(row.warehouse)}</span><b>${qtyText(row.quantity)} шт.${price}</b>`;
      stock.appendChild(el);
    });
    if (stockData.refreshing) {
      stock.insertAdjacentHTML("beforeend", '<div class="stock-note">Проверяем остальные склады…</div>');
    }
  } else {
    stock.textContent = stockData.refreshing
      ? "Проверяем склады…"
      : (stockData.known ? "Подтверждённого остатка сейчас нет." : "Остаток уточняется.");
  }
}

function renderOffers(offers = []) {
  const root = $("offers");
  root.innerHTML = "";
  if (!offers.length) {
    root.innerHTML = '<div class="offer-empty">Доступные варианты покупки уточняются.</div>';
    return;
  }
  offers.forEach((offer) => {
    const el = document.createElement("article");
    el.className = "offer-card";
    const sourceLabel = offer.source === "usa" ? "🇺🇸 склад США" : `🇷🇺 ${escapeHtml(offer.label)}`;
    const availability = offer.source === "usa"
      ? "????? ?? ???"
      : (offer.site_in_stock_without_quantity === true
          ? "???? ? ??????? ?? ?????, ?????????? ?? ???????"
          : "? ??????? ?? ?????: " + qtyText(offer.available_quantity) + " ??.");
    const price = offer.price_rub == null
      ? "Цена уточняется"
      : (offer.source === "usa" ? rub(offer.price_rub).replace(" ₽", "* ₽") : rub(offer.price_rub));
    const disabled = offer.can_add ? "" : " disabled";
    const cartKey = `${currentProduct?.manufacturer || ""}|${currentProduct?.oem || ""}|${offer.key}`;
    const existingQty = cart.find((x) => x.key === cartKey)?.qty || 0;
    const inCart = existingQty
      ? `<div class="offer-in-cart">🛒 В корзине: ${existingQty} шт.</div>`
      : "";
    const buttonText = offer.can_add
      ? (existingQty ? "Добавить ещё" : "В корзину")
      : "Цена уточняется";
    el.innerHTML = `
      <div class="offer-meta">
        <div class="offer-source">${sourceLabel}</div>
        <div class="offer-availability">${availability}</div>
        ${inCart}
      </div>
      <div class="offer-buy">
        <strong>${price}</strong>
        <button type="button" data-offer-key="${escapeHtml(offer.key)}"${disabled}>${buttonText}</button>
      </div>`;
    root.appendChild(el);
  });
}

async function pollStock(card, serial, attempt = 0) {
  if (serial !== stockPollSerial) return;
  if (attempt >= 40) {
    if (currentProduct && currentProduct.oem === card.oem) {
      currentProduct.stock.refreshing = false;
      renderStock(currentProduct.stock);
    }
    return;
  }
  await new Promise((resolve) => setTimeout(resolve, 1500));
  if (serial !== stockPollSerial || !currentProduct || currentProduct.oem !== card.oem) return;
  try {
    const params = new URLSearchParams({ manufacturer: card.manufacturer });
    const response = await fetch(`/api/v1/oem/${encodeURIComponent(card.oem)}/stock?${params}`);
    if (!response.ok) throw new Error("stock_refresh_failed");
    const stockData = await response.json();
    if (serial !== stockPollSerial || !currentProduct || currentProduct.oem !== card.oem) return;
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
    } else if (currentProduct && currentProduct.oem === card.oem) {
      currentProduct.stock.refreshing = false;
      renderStock(currentProduct.stock);
    }
  }
}

function renderProduct(card) {
  currentProduct = card;
  currentQty = 1;
  $("qtyValue").textContent = "1";
  $("productBrand").textContent = [card.manufacturer, card.catalog].filter(Boolean).join(" • ");
  $("productName").textContent = card.name || "OEM позиция";
  $("productOem").textContent = card.oem;
  const pricePanel = document.querySelector(".price-panel");
  pricePanel?.classList.toggle("hidden", Boolean(card.warehouse_only));
  $("customerPrice").textContent = card.price.customer_rub == null
    ? rub(null)
    : rub(card.price.customer_rub).replace(" ₽", "* ₽");

  const replacement = $("replacement");
  if (card.requested_oem && card.requested_oem !== card.oem) {
    replacement.textContent = `Запрошен ${card.requested_oem} → актуальный ${card.oem}`;
    replacement.classList.remove("hidden");
  } else {
    replacement.classList.add("hidden");
  }

  const msrp = $("msrpBlock");
  if (card.price.msrp_rub) {
    $("msrpPrice").textContent = rub(card.price.msrp_rub);
    $("benefit").textContent = `выгода ${card.price.benefit_pct}%`;
    msrp.classList.remove("hidden");
  } else {
    msrp.classList.add("hidden");
  }

  stockPollSerial += 1;
  const stockSerial = stockPollSerial;
  renderStock(card.stock);
  renderOffers(card.offers || []);
  if (card.stock.refreshing) pollStock(card, stockSerial);

  const weight = $("weight");
  weight.innerHTML = "";
  if (card.weight) {
    if (card.weight.actual_kg) {
      weight.insertAdjacentHTML("beforeend",
        `<div class="weight-line">Фактический: <b>${card.weight.actual_kg} кг / шт.</b></div>`);
    }
    if (card.weight.volume_kg) {
      weight.insertAdjacentHTML("beforeend",
        `<div class="weight-line">Объёмный: <b>${card.weight.volume_kg} кг / шт.</b></div>`);
    }
    weight.insertAdjacentHTML("beforeend",
      `<div class="weight-note">${escapeHtml(card.weight.notice)}</div>`);
  } else {
    weight.textContent = "Вес неизвестен.";
  }

  $("deliveryNotice").textContent = card.delivery_notice?.startsWith("* -")
    ? card.delivery_notice
    : "🚚 " + card.delivery_notice;
  product.classList.remove("hidden");
  product.scrollIntoView({ behavior: "smooth", block: "start" });
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = String(value ?? "");
  return div.innerHTML;
}

async function searchOem(oem) {
  product.classList.add("hidden");
  showState("\u0418\u0449\u0435\u043c OEM \u043d\u0430 \u0441\u0430\u0439\u0442\u0430\u0445 \u043f\u043e\u0441\u0442\u0430\u0432\u0449\u0438\u043a\u043e\u0432\u2026");
  try {
    const path = "/api/v1/oem/" + encodeURIComponent(oem);
    const [usaResult, siteResult] = await Promise.allSettled([
      fetch(path + "?cache_only=true"), fetch(path + "?website_first=true")
    ]);
    const baseResponse = usaResult.status === "fulfilled" ? usaResult.value : null;
    const siteResponse = siteResult.status === "fulfilled" ? siteResult.value : null;
    const usa = baseResponse && baseResponse.ok ? await baseResponse.json() : null;
    const rf = siteResponse && siteResponse.ok ? await siteResponse.json() : null;
    if (!usa && !rf) throw new Error("OEM \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d \u043d\u0430 \u0441\u0430\u0439\u0442\u0430\u0445.");
    let card = usa || rf;
    if (rf && usa) {
      card = {...usa, warehouse_only:false};
      card.offers = [
        ...(usa.offers || []).filter(x => x.source === "usa"),
        ...(rf.offers || [])
      ];
      card.stock = {...usa.stock, warehouses:rf.stock.warehouses,
                    refreshing:false, known:true};
    }
    hideState();
    renderProduct(card);
  } catch (error) {
    showState(error.message || "\u041e\u0448\u0438\u0431\u043a\u0430 \u043f\u043e\u0438\u0441\u043a\u0430.", true);
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
  const offerKey = target.dataset.offerKey;
  if (!offerKey || !currentProduct) return;

  const offer = (currentProduct.offers || []).find((x) => x.key === offerKey);
  if (!offer || !offer.can_add || offer.price_rub == null) return;
  const rf39InternalBySlot = {
    1: "Основной склад",
    2: "склад ЯРС",
    3: "склад КРС",
  };
  const rf39Slot = offer.rf39_preview_slot;
  const rf39Internal = offer.supplier_internal_warehouse;
  if (offer.source === "warehouse" &&
      (!Number.isInteger(rf39Slot) ||
       rf39InternalBySlot[rf39Slot] !== rf39Internal)) return;

  const key = `${currentProduct.manufacturer}|${currentProduct.oem}|${offer.key}`;
  const maxQty = offer.available_quantity == null
    ? (offer.source === "warehouse" ? 1 : 99)
    : Math.max(0, Math.floor(Number(offer.available_quantity)));
  if (maxQty < 1) return;

  const existing = cart.find((x) => x.key === key);
  if (existing) {
    if (offer.source === "warehouse" &&
        (existing.warehouse_id !== rf39Slot ||
         existing.supplier_internal_warehouse !== rf39Internal)) return;
    existing.qty = Math.min(maxQty, existing.qty + currentQty);
  } else {
    cart.push({
      key,
      manufacturer: currentProduct.manufacturer,
      oem: currentProduct.oem,
      requested_oem: currentProduct.requested_oem,
      name: currentProduct.name,
      offer_source: offer.source,
      warehouse_id: offer.source === "warehouse" ? rf39Slot : null,
      supplier_internal_warehouse: offer.source === "warehouse"
        ? rf39Internal : null,
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
  localStorage.setItem("extremizer_web1_cart", JSON.stringify(cart));
  renderCart();
  if (currentProduct) renderOffers(currentProduct.offers || []);
}

function renderCart() {
  const itemCount = cart.reduce((sum, x) => sum + x.qty, 0);
  $("cartCount").textContent = itemCount;
  $("cartButton").classList.toggle("has-items", itemCount > 0);

  const root = $("cartItems");
  root.innerHTML = "";
  $("cartEmpty").classList.toggle("hidden", cart.length > 0);
  $("cartFooter").classList.toggle("hidden", cart.length === 0);

  cart.forEach((item, index) => {
    const el = document.createElement("div");
    el.className = "cart-item";
    el.innerHTML = `
      <div class="cart-item-top">
        <div>
          <div class="cart-item-name">${escapeHtml(item.name || item.manufacturer)}</div>
          <div class="cart-item-oem">${escapeHtml(item.oem)}</div>
          <div class="cart-item-source">${item.offer_source === "warehouse" ? "🇷🇺" : "🇺🇸"} ${escapeHtml(item.source_label || "склад США")}</div>
        </div>
        <button class="remove" data-remove="${index}" type="button">Удалить</button>
      </div>
      <div class="cart-item-bottom">
        <div class="cart-mini-qty">
          <button data-minus="${index}" type="button">−</button>
          <b>${item.qty}</b>
          <button data-plus="${index}" type="button">+</button>
        </div>
        <strong>${item.unit_rub ? rub(item.unit_rub * item.qty) : "Цена уточняется"}</strong>
      </div>`;
    root.appendChild(el);
  });
  const scopes = new Set(cart.map(x =>
    x.offer_source === "warehouse" ? "rf:" + String(x.warehouse_id) : "usa"));
  $("cartTotal").textContent = scopes.size + " ????????? ???????";
  $("cartButtonTotal").textContent = scopes.size + " ???????";
}

function openCart() {
  cartPanel.classList.add("open");
  cartPanel.setAttribute("aria-hidden", "false");
  scrim.classList.remove("hidden");
}
function closeCart() {
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
        ? (item.offer_source === "warehouse" ? 1 : 99)
        : Math.max(1, Math.floor(Number(item.available_quantity)));
      item.qty = Math.min(maxQty, item.qty + 1);
    }
  }
  saveCart();
});

$("telegramButton").addEventListener("click", async () => {
  if (!cart.length) return;
  const rf39InternalBySlot = {
    1: "Основной склад", 2: "склад ЯРС", 3: "склад КРС",
  };
  if (cart.some((item) => item.offer_source === "warehouse" &&
        (!Number.isInteger(item.warehouse_id) ||
         rf39InternalBySlot[item.warehouse_id] !== item.supplier_internal_warehouse))) {
    alert("Склад РФ нужно выбрать повторно в существующей карточке товара.");
    return;
  }
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
          warehouse_id: x.offer_source === "warehouse" ? x.warehouse_id : null,
          supplier_internal_warehouse: x.offer_source === "warehouse"
            ? x.supplier_internal_warehouse : null,
          expected_price_rub: x.offer_source === "warehouse" ? x.unit_rub : null,
          qty: x.qty,
        })),
      }),
    });
    if (!response.ok) throw new Error("Не удалось передать корзину.");
    const data = await response.json();
    window.location.href = data.telegram_url;
  } catch (error) {
    alert(error.message || "Не удалось открыть Telegram.");
    button.disabled = false;
    button.textContent = old;
  }
});

renderCart();
