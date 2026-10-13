'use strict';

const BUSINESS_STATUS_KEYS = new Set([
  'new',
  'assembled-unpaid',
  'assembled-paid',
  'shipped',
]);

const EFFECTIVE_ROLE_VIEW = {
  admin: 'admin',
  rf_warehouse_user: 'sklad',
};

const EFFECTIVE_ROLE_PANEL = {
  admin: 'warehousePanel',
  rf_warehouse_user: 'warehousePanel',
  client: 'clientPanel',
  dealer: 'dealerPanel',
};

const state = {
  fixture: null,
  viewRole: 'sklad',
  demoMode: false,
  query: '',
  dateFrom: '',
  dateTo: '',
  pageSize: 15,
  page: 1,
  dealerScenario: 'debt',
};

const esc = value => String(value ?? '').replace(/[&<>"']/g, ch => ({
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}[ch]));

const rub = value => {
  if (value === null || value === undefined) return '—';
  return new Intl.NumberFormat('ru-RU').format(Number(value)) + ' ₽';
};

const signedRub = value => {
  if (value === null || value === undefined) return '';
  const n = Number(value);
  if (n === 0) return '0 ₽';
  return (n > 0 ? '+' : '−') + new Intl.NumberFormat('ru-RU').format(Math.abs(n)) + ' ₽';
};

const usd = value => (Number(value) < 0 ? '−' : '') + '$' + Math.abs(Number(value)).toFixed(2);

async function loadReadModel() {
  const response = await fetch('finance_role_preview_fixture.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('fixture read model unavailable');
  return response.json();
}

function effectiveRole() {
  return String(state.fixture?.actingAs?.effectiveRole || '');
}

function deriveEffectiveView() {
  const configured = state.fixture?.actingAs?.effectiveView;
  if (configured === 'admin' || configured === 'sklad') return configured;
  return EFFECTIVE_ROLE_VIEW[effectiveRole()] || 'sklad';
}

function allowedPanel() {
  return EFFECTIVE_ROLE_PANEL[effectiveRole()] || 'warehousePanel';
}

function statusClass(key) {
  return BUSINESS_STATUS_KEYS.has(key) ? `status-${key}` : 'status-unknown';
}

function setTabVisibility() {
  document.querySelectorAll('.tab').forEach(tab => {
    const allowed = state.demoMode || tab.dataset.panel === allowedPanel();
    tab.hidden = !allowed;
    tab.disabled = !allowed;
  });
}

function activatePanel(id) {
  if (!state.demoMode && id !== allowedPanel()) return false;
  document.querySelectorAll('.panel').forEach(el => el.classList.toggle('is-active', el.id === id));
  document.querySelectorAll('.tab').forEach(el => el.classList.toggle('is-active', el.dataset.panel === id));
  return true;
}

function renderContext() {
  const a = state.fixture.actingAs;
  const acting = document.querySelector('#actingBanner');
  const demo = document.querySelector('#demoBanner');
  const demoToggle = document.querySelector('#demoToggle');

  if (state.demoMode) {
    acting.hidden = true;
    demo.hidden = false;
    demoToggle.textContent = 'DEMO MODE: ON';
    demoToggle.setAttribute('aria-pressed', 'true');
  } else {
    acting.hidden = !a.active;
    demo.hidden = true;
    demoToggle.textContent = 'DEMO MODE: OFF';
    demoToggle.setAttribute('aria-pressed', 'false');

    document.querySelector('#actualAdmin').textContent = a.actualAdmin;
    document.querySelector('#effectiveSubject').textContent = a.effectiveSubject;
    document.querySelector('#effectiveRole').textContent = a.effectiveRole;
    document.querySelector('#effectiveView').textContent = state.viewRole.toUpperCase();
  }

  document.querySelector('#adminRole').classList.toggle('is-active', state.viewRole === 'admin');
  document.querySelector('#skladRole').classList.toggle('is-active', state.viewRole === 'sklad');
  setTabVisibility();
}

function filteredRows() {
  const q = state.query.trim().toLowerCase();
  return state.fixture.warehouse.rows.filter(row => {
    const text = [
      row.extremizerOrderNo,
      row.warehouseOrderNo,
      row.client,
      ...(Array.isArray(row.oems) ? row.oems : []),
      row.date,
    ].join(' ').toLowerCase();
    if (q && !text.includes(q)) return false;
    if (state.dateFrom && (!row.date || row.date < state.dateFrom)) return false;
    if (state.dateTo && (!row.date || row.date > state.dateTo)) return false;
    return true;
  });
}

function orderPair(row) {
  return state.viewRole === 'admin'
    ? [row.extremizerOrderNo, row.warehouseOrderNo]
    : [row.warehouseOrderNo, row.extremizerOrderNo];
}

function orderButtons(row) {
  const [first, second] = orderPair(row);
  const canonical = esc(row.extremizerOrderNo);
  return `<div class="order-links">
    <button class="order-link" data-open-order="${canonical}">${esc(first || '—')}</button>
    <span>/</span>
    <button class="order-link" data-open-order="${canonical}">${esc(second || '—')}</button>
  </div>`;
}

function renderSystemErrors() {
  const host = document.querySelector('#systemMessages');
  const errors = Array.isArray(state.fixture.warehouse.systemErrors)
    ? state.fixture.warehouse.systemErrors
    : [];
  host.innerHTML = errors.map(error => {
    const context = error.context || {};
    return `<aside class="system-error-message" data-system-error="${esc(error.code)}">
      <strong>System Error</strong>
      <span class="system-error-code">${esc(error.code)}</span>
      <p>${esc(error.message)}</p>
      <small>Контекст: ${esc(context.extremizerOrderNo || '—')} / ${esc(context.warehouseOrderNo || '—')}</small>
    </aside>`;
  }).join('');
}

function renderWarehouse() {
  const summary = state.fixture.warehouse.summary;
  document.querySelector('#postedBalance').textContent = rub(summary.postedRub);
  document.querySelector('#reservedBalance').textContent = rub(summary.reservedRub);
  document.querySelector('#availableBalance').textContent = rub(summary.availableRub);
  document.querySelector('#historyTotal').textContent = rub(summary.fullHistoryBalanceRub);
  document.querySelector('#activeViewLabel').textContent = state.viewRole.toUpperCase();
  document.querySelector('#ordersHeader').textContent = state.viewRole === 'admin'
    ? '№ нашего заказа / № заказа на складе'
    : '№ нашего заказа / № заказа Extremizer';

  const rows = filteredRows();
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.page = Math.min(state.page, pages);
  const start = (state.page - 1) * state.pageSize;
  const pageRows = rows.slice(start, start + state.pageSize);

  const body = document.querySelector('#warehouseBody');
  body.innerHTML = pageRows.map(row => {
    const oems = Array.isArray(row.oems) ? row.oems.map(esc).join(', ') : '';
    const partnerAmount = row.partnerAmountRub === null || row.partnerAmountRub === undefined
      ? '—'
      : rub(row.partnerAmountRub);
    const balanceDisplay = row.balanceDeltaRub === null || row.balanceDeltaRub === undefined
      ? ''
      : signedRub(row.balanceDeltaRub);
    return `
      <tr class="warehouse-row ${statusClass(row.statusKey)}"
          data-order="${esc(row.extremizerOrderNo)}"
          data-status="${esc(row.statusKey)}">
        <td>${esc(row.date || "—")}<span class="status-inline">${esc(row.status || "—")}</span><span class="submeta">${esc(row.client || "—")} · ${oems || "—"}</span></td>
        <td>${orderButtons(row)}</td>
        <td><span class="customer-amount ${row.customerAmountStruck ? 'is-spent' : ''}">${rub(row.customerAmountRub)}</span></td>
        <td class="partner-amount" data-authoritative="${row.partnerAmountAuthoritative === true ? 'true' : 'false'}">${partnerAmount}</td>
        <td class="balance-cell">${balanceDisplay}</td>
      </tr>`;
  }).join('');

  const cards = document.querySelector('#warehouseCards');
  cards.innerHTML = pageRows.map(row => {
    const oems = Array.isArray(row.oems) ? row.oems.map(esc).join(', ') : '';
    const partnerAmount = row.partnerAmountRub === null || row.partnerAmountRub === undefined
      ? '—'
      : rub(row.partnerAmountRub);
    const balanceDisplay = row.balanceDeltaRub === null || row.balanceDeltaRub === undefined
      ? '—'
      : signedRub(row.balanceDeltaRub);
    return `
      <article class="mobile-card ${statusClass(row.statusKey)}" data-order="${esc(row.extremizerOrderNo)}">
        <span class="status-inline">${esc(row.status || "—")}</span>
        <div class="field-grid">
          <div class="field"><span>Дата</span><strong>${esc(row.date || "—")}</strong></div>
          <div class="field"><span>Баланс</span><strong>${balanceDisplay}</strong></div>
          <div class="field full"><span>${state.viewRole === 'admin' ? '№ нашего заказа / № заказа на складе' : '№ нашего заказа / № заказа Extremizer'}</span><strong>${orderButtons(row)}</strong></div>
          <div class="field"><span>Сумма для клиента</span><strong class="customer-amount ${row.customerAmountStruck ? 'is-spent' : ''}">${rub(row.customerAmountRub)}</strong></div>
          <div class="field"><span>Сумма по условиям склада</span><strong class="partner-amount">${partnerAmount}</strong></div>
        </div>
        <span class="submeta">${esc(row.client || "—")} · ${oems || "—"}</span>
      </article>`;
  }).join('');

  document.querySelector('#resultCount').textContent = `Найдено: ${rows.length} · показано ${pageRows.length}`;
  document.querySelector('#pageIndicator').textContent = `${state.page} / ${pages}`;
  document.querySelector('#prevPage').disabled = state.page <= 1;
  document.querySelector('#nextPage').disabled = state.page >= pages;
  bindOrderLinks();
}

function findOrder(orderNo) {
  return state.fixture.warehouse.rows.find(row => row.extremizerOrderNo === orderNo);
}

function openOrder(orderNo) {
  const row = findOrder(orderNo);
  if (!row) return;
  document.querySelector('#dialogTitle').textContent = `${row.extremizerOrderNo} / ${row.warehouseOrderNo || '—'}`;
  const oems = Array.isArray(row.oems) ? row.oems.map(esc).join(', ') : '';
  const partnerAmount = row.partnerAmountRub === null || row.partnerAmountRub === undefined
    ? '—'
    : rub(row.partnerAmountRub);
  const balanceDisplay = row.balanceDeltaRub === null || row.balanceDeltaRub === undefined
    ? '—'
    : signedRub(row.balanceDeltaRub);
  document.querySelector('#dialogContent').innerHTML = `<div class="dialog-body"><dl>
    <dt>Клиент</dt><dd>${esc(row.client || "—")}</dd>
    <dt>OEM</dt><dd>${oems || "—"}</dd>
    <dt>Статус</dt><dd>${esc(row.status || "—")}</dd>
    <dt>Payment source</dt><dd>${esc(row.paymentSource || '—')}</dd>
    <dt>Сумма для клиента</dt><dd>${rub(row.customerAmountRub)}</dd>
    <dt>Сумма по условиям склада</dt><dd>${partnerAmount}</dd>
    <dt>Баланс строки</dt><dd>${balanceDisplay}</dd>
  </dl></div>`;
  document.querySelector('#orderDialog').showModal();
}

function bindOrderLinks() {
  document.querySelectorAll('[data-open-order]').forEach(btn => {
    btn.addEventListener('click', () => openOrder(btn.dataset.openOrder));
  });
}

function renderClient() {
  const model = state.fixture.clientWallet;
  document.querySelector('#clientWalletBalance').textContent = rub(model.balanceRub);
  document.querySelector('#clientEvents').innerHTML = model.events.map(event => `
    <article class="event-card" data-event-type="${esc(event.eventType)}">
      <div class="event-top">
        <div><span class="event-type">${esc(event.eventType)}</span><h3>${esc(event.description)}</h3></div>
        <strong>${esc(event.orderId)}</strong>
      </div>
      <div class="event-values">
        <div><span>Реальная внешняя сумма</span><strong>${rub(event.externalAmountRub)}</strong></div>
        <div><span>Изменение кошелька EXTREMIZER</span><strong class="zero-delta">${rub(event.walletDeltaRub)}</strong></div>
        <div><span>Получатель / источник</span><strong>${esc(event.warehouse)}</strong></div>
      </div>
    </article>`).join('');
}

function renderDealer() {
  const dealer = state.fixture.dealer;
  const scenario = dealer.scenarios[state.dealerScenario];
  document.querySelector('#dealerBalance').textContent = usd(scenario.balanceUsd);
  document.querySelector('#dealerOrderStatus').textContent = scenario.orderStatus;
  const gate = document.querySelector('#dealerGate');
  gate.textContent = scenario.gateReady ? 'READY' : 'BLOCKED';
  gate.className = scenario.gateReady ? 'gate-ready' : 'gate-blocked';
  document.querySelector('#dealerCharge').textContent = usd(scenario.orderChargeUsd);
  document.querySelector('#dealerChargeCount').textContent = `ORDER_CHARGE ×${Number(scenario.orderChargeCount)}`;
  document.querySelector('#dealerOrder').innerHTML = `
    <strong>${esc(scenario.orderId)}</strong> · сумма ${usd(scenario.orderTotalUsd)} · заказ оформлен независимо от USD balance.
    <br><small>Supplier/DCP gate wiring: ${esc(dealer.supplierGateWiring)}. Это не заявляет автоматическую блокировку реальной DCP-отправки.</small>`;
}

function showToast(text) {
  const toast = document.querySelector('#toast');
  toast.textContent = text;
  toast.classList.add('is-visible');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('is-visible'), 3200);
}

function applyEffectiveContext() {
  state.viewRole = deriveEffectiveView();
  state.page = 1;
  setTabVisibility();
  activatePanel(allowedPanel());
  renderContext();
  renderWarehouse();
}

function setDemoMode(enabled) {
  state.demoMode = Boolean(enabled);
  if (state.demoMode) {
    state.viewRole = deriveEffectiveView();
    setTabVisibility();
    activatePanel('warehousePanel');
  } else {
    applyEffectiveContext();
  }
  renderContext();
  renderWarehouse();
}

function bindControls() {
  document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => {
    if (!activatePanel(tab.dataset.panel)) {
      showToast('Доступ к этому представлению не соответствует effective role.');
    }
  }));

  document.querySelector('#demoToggle').addEventListener('click', () => setDemoMode(!state.demoMode));

  document.querySelector('#adminRole').addEventListener('click', () => {
    if (!state.demoMode) {
      showToast('ADMIN/SKLAD сравнение доступно только в DEMO MODE.');
      return;
    }
    state.viewRole = 'admin';
    state.page = 1;
    renderContext();
    renderWarehouse();
  });

  document.querySelector('#skladRole').addEventListener('click', () => {
    if (!state.demoMode) {
      showToast('ADMIN/SKLAD сравнение доступно только в DEMO MODE.');
      return;
    }
    state.viewRole = 'sklad';
    state.page = 1;
    renderContext();
    renderWarehouse();
  });

  document.querySelector('#warehouseSearch').addEventListener('input', event => {
    state.query = event.target.value; state.page = 1; renderWarehouse();
  });
  document.querySelector('#dateFrom').addEventListener('change', event => {
    state.dateFrom = event.target.value; state.page = 1; renderWarehouse();
  });
  document.querySelector('#dateTo').addEventListener('change', event => {
    state.dateTo = event.target.value; state.page = 1; renderWarehouse();
  });
  document.querySelector('#pageSize').addEventListener('change', event => {
    state.pageSize = Number(event.target.value); state.page = 1; renderWarehouse();
  });
  document.querySelector('#prevPage').addEventListener('click', () => {
    state.page = Math.max(1, state.page - 1); renderWarehouse();
  });
  document.querySelector('#nextPage').addEventListener('click', () => {
    state.page += 1; renderWarehouse();
  });
  document.querySelector('#dialogClose').addEventListener('click', () => document.querySelector('#orderDialog').close());

  document.querySelector('#dealerDebt').addEventListener('click', () => {
    state.dealerScenario = 'debt';
    document.querySelector('#dealerDebt').classList.add('is-active');
    document.querySelector('#dealerRepaid').classList.remove('is-active');
    renderDealer();
  });
  document.querySelector('#dealerRepaid').addEventListener('click', () => {
    state.dealerScenario = 'repaid';
    document.querySelector('#dealerRepaid').classList.add('is-active');
    document.querySelector('#dealerDebt').classList.remove('is-active');
    renderDealer();
  });

  document.querySelector('#actingExit').addEventListener('click', () => {
    showToast('FIXTURE ONLY: серверная Acting-As сессия не подключена, не изменена и не завершена.');
  });
}

async function init() {
  try {
    state.fixture = await loadReadModel();
    state.demoMode = Boolean(state.fixture.meta?.demoModeDefault);
    state.viewRole = deriveEffectiveView();
    renderSystemErrors();
    renderWarehouse();
    renderClient();
    renderDealer();
    bindControls();
    if (state.demoMode) {
      setDemoMode(true);
    } else {
      applyEffectiveContext();
    }
  } catch (error) {
    document.body.replaceChildren();
    const main = document.createElement('main');
    const h1 = document.createElement('h1');
    const p = document.createElement('p');
    h1.textContent = 'Preview unavailable';
    p.textContent = String(error?.message || error);
    main.append(h1, p);
    document.body.append(main);
  }
}

document.addEventListener('DOMContentLoaded', init);
