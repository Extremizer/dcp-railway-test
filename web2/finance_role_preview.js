'use strict';

const state = {
  fixture: null,
  role: 'admin',
  query: '',
  dateFrom: '',
  dateTo: '',
  pageSize: 15,
  page: 1,
  dealerScenario: 'debt',
};

const rub = value => new Intl.NumberFormat('ru-RU').format(value) + ' ₽';
const usd = value => (value < 0 ? '−' : '') + '$' + Math.abs(value).toFixed(2);

async function loadReadModel() {
  const response = await fetch('finance_role_preview_fixture.json', {cache: 'no-store'});
  if (!response.ok) throw new Error('fixture read model unavailable');
  return response.json();
}

function statusClass(key) {
  return `status-${key}`;
}

function renderActingAs() {
  const a = state.fixture.actingAs;
  const banner = document.querySelector('#actingBanner');
  banner.hidden = !a.active;
  document.querySelector('#actualAdmin').textContent = a.actualAdmin;
  document.querySelector('#effectiveSubject').textContent = a.effectiveSubject;
  document.querySelector('#effectiveRole').textContent = a.effectiveRole;
}

function filteredRows() {
  const q = state.query.trim().toLowerCase();
  return state.fixture.warehouse.rows.filter(row => {
    const text = [
      row.extremizerOrderNo,
      row.warehouseOrderNo,
      row.client,
      ...row.oems,
      row.date,
    ].join(' ').toLowerCase();
    if (q && !text.includes(q)) return false;
    if (state.dateFrom && row.date < state.dateFrom) return false;
    if (state.dateTo && row.date > state.dateTo) return false;
    return true;
  });
}

function orderPair(row) {
  return state.role === 'admin'
    ? [row.extremizerOrderNo, row.warehouseOrderNo]
    : [row.warehouseOrderNo, row.extremizerOrderNo];
}

function orderButtons(row) {
  const [first, second] = orderPair(row);
  return `<div class="order-links">
    <button class="order-link" data-open-order="${row.extremizerOrderNo}">${first}</button>
    <span>/</span>
    <button class="order-link" data-open-order="${row.extremizerOrderNo}">${second}</button>
  </div>`;
}

function renderWarehouse() {
  const summary = state.fixture.warehouse.summary;
  document.querySelector('#postedBalance').textContent = rub(summary.postedRub);
  document.querySelector('#reservedBalance').textContent = rub(summary.reservedRub);
  document.querySelector('#availableBalance').textContent = rub(summary.availableRub);
  document.querySelector('#historyTotal').textContent = rub(summary.fullHistoryBalanceRub);
  document.querySelector('#ordersHeader').textContent = state.role === 'admin'
    ? '№ нашего заказа / № заказа на складе'
    : '№ нашего заказа / № заказа Extremizer';

  const rows = filteredRows();
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize));
  state.page = Math.min(state.page, pages);
  const start = (state.page - 1) * state.pageSize;
  const pageRows = rows.slice(start, start + state.pageSize);

  const body = document.querySelector('#warehouseBody');
  body.innerHTML = pageRows.map(row => `
    <tr class="warehouse-row ${statusClass(row.statusKey)}" data-order="${row.extremizerOrderNo}" data-status="${row.statusKey}">
      <td>${row.date}<span class="status-inline">${row.status}</span><span class="submeta">${row.client} · ${row.oems.join(', ')}</span></td>
      <td>${orderButtons(row)}</td>
      <td><span class="customer-amount ${row.customerAmountStruck ? 'is-spent' : ''}">${rub(row.customerAmountRub)}</span></td>
      <td>${rub(row.partnerAmountRub)}</td>
      <td class="balance-cell">${row.balanceDisplay}</td>
    </tr>`).join('');

  const cards = document.querySelector('#warehouseCards');
  cards.innerHTML = pageRows.map(row => `
    <article class="mobile-card ${statusClass(row.statusKey)}" data-order="${row.extremizerOrderNo}">
      <span class="status-inline">${row.status}</span>
      <div class="field-grid">
        <div class="field"><span>Дата</span><strong>${row.date}</strong></div>
        <div class="field"><span>Баланс</span><strong>${row.balanceDisplay || '—'}</strong></div>
        <div class="field full"><span>${state.role === 'admin' ? '№ нашего заказа / № заказа на складе' : '№ нашего заказа / № заказа Extremizer'}</span><strong>${orderButtons(row)}</strong></div>
        <div class="field"><span>Сумма для клиента</span><strong class="customer-amount ${row.customerAmountStruck ? 'is-spent' : ''}">${rub(row.customerAmountRub)}</strong></div>
        <div class="field"><span>Сумма по условиям склада</span><strong>${rub(row.partnerAmountRub)}</strong></div>
      </div>
      <span class="submeta">${row.client} · ${row.oems.join(', ')}</span>
    </article>`).join('');

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
  document.querySelector('#dialogTitle').textContent = `${row.extremizerOrderNo} / ${row.warehouseOrderNo}`;
  document.querySelector('#dialogContent').innerHTML = `<div class="dialog-body"><dl>
    <dt>Клиент</dt><dd>${row.client}</dd>
    <dt>OEM</dt><dd>${row.oems.join(', ')}</dd>
    <dt>Статус</dt><dd>${row.status}</dd>
    <dt>Сумма для клиента</dt><dd>${rub(row.customerAmountRub)}</dd>
    <dt>Сумма по условиям склада</dt><dd>${rub(row.partnerAmountRub)}</dd>
    <dt>Баланс строки</dt><dd>${row.balanceDisplay || '—'}</dd>
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
    <article class="event-card" data-event-type="${event.eventType}">
      <div class="event-top">
        <div><span class="event-type">${event.eventType}</span><h3>${event.description}</h3></div>
        <strong>${event.orderId}</strong>
      </div>
      <div class="event-values">
        <div><span>Реальная внешняя сумма</span><strong>${rub(event.externalAmountRub)}</strong></div>
        <div><span>Изменение кошелька EXTREMIZER</span><strong class="zero-delta">${rub(event.walletDeltaRub)}</strong></div>
        <div><span>Получатель / источник</span><strong>${event.warehouse}</strong></div>
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
  document.querySelector('#dealerChargeCount').textContent = `ORDER_CHARGE ×${scenario.orderChargeCount}`;
  document.querySelector('#dealerOrder').innerHTML = `
    <strong>${scenario.orderId}</strong> · сумма ${usd(scenario.orderTotalUsd)} · заказ оформлен независимо от USD balance.
    <br><small>Supplier/DCP gate wiring: ${dealer.supplierGateWiring}. Это не заявляет автоматическую блокировку реальной DCP-отправки.</small>`;
}

function activatePanel(id) {
  document.querySelectorAll('.panel').forEach(el => el.classList.toggle('is-active', el.id === id));
  document.querySelectorAll('.tab').forEach(el => el.classList.toggle('is-active', el.dataset.panel === id));
}

function showToast(text) {
  const toast = document.querySelector('#toast');
  toast.textContent = text;
  toast.classList.add('is-visible');
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => toast.classList.remove('is-visible'), 2200);
}

function bindControls() {
  document.querySelectorAll('.tab').forEach(tab => tab.addEventListener('click', () => activatePanel(tab.dataset.panel)));
  document.querySelector('#adminRole').addEventListener('click', () => {
    state.role = 'admin'; state.page = 1;
    document.querySelector('#adminRole').classList.add('is-active');
    document.querySelector('#skladRole').classList.remove('is-active');
    renderWarehouse();
  });
  document.querySelector('#skladRole').addEventListener('click', () => {
    state.role = 'sklad'; state.page = 1;
    document.querySelector('#skladRole').classList.add('is-active');
    document.querySelector('#adminRole').classList.remove('is-active');
    renderWarehouse();
  });
  document.querySelector('#warehouseSearch').addEventListener('input', event => { state.query = event.target.value; state.page = 1; renderWarehouse(); });
  document.querySelector('#dateFrom').addEventListener('change', event => { state.dateFrom = event.target.value; state.page = 1; renderWarehouse(); });
  document.querySelector('#dateTo').addEventListener('change', event => { state.dateTo = event.target.value; state.page = 1; renderWarehouse(); });
  document.querySelector('#pageSize').addEventListener('change', event => { state.pageSize = Number(event.target.value); state.page = 1; renderWarehouse(); });
  document.querySelector('#prevPage').addEventListener('click', () => { state.page = Math.max(1, state.page - 1); renderWarehouse(); });
  document.querySelector('#nextPage').addEventListener('click', () => { state.page += 1; renderWarehouse(); });
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
    state.fixture.actingAs.active = false;
    renderActingAs();
    showToast('Fixture only: UI замещения закрыт. Реальная сессия не изменялась.');
  });
}

async function init() {
  try {
    state.fixture = await loadReadModel();
    renderActingAs();
    renderWarehouse();
    renderClient();
    renderDealer();
    bindControls();
  } catch (error) {
    document.body.innerHTML = `<main><h1>Preview unavailable</h1><p>${error.message}</p></main>`;
  }
}

document.addEventListener('DOMContentLoaded', init);
