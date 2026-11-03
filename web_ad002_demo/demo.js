"use strict";
/* No API, fetch, storage, navigation, tracking, finance or server writes. */
(function () {
  const orders = window.AD002_DEMO_ORDERS;
  const body = document.getElementById("ordersBody");
  const form = document.getElementById("demoFilters");
  const clientFilter = document.getElementById("clientFilter");
  const numberFilter = document.getElementById("numberFilter");
  const deliveryFilter = document.getElementById("deliveryFilter");
  const startDate = document.getElementById("startDate");
  const endDate = document.getElementById("endDate");
  const found = document.getElementById("foundCount");

  function element(tag, className, value) {
    const item = document.createElement(tag);
    if (className) item.className = className;
    if (value != null) item.textContent = String(value);
    return item;
  }

  function addOptions(select, values) {
    values.forEach(value => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = value;
      select.appendChild(option);
    });
  }
  addOptions(clientFilter, [...new Set(orders.map(o => o.customer))].sort((a,b)=>a.localeCompare(b,"ru")));
  addOptions(numberFilter, orders.map(o => o.id));

  function appendCell(row, className) {
    const td = element("td", className);
    row.appendChild(td);
    return td;
  }
  function drawOne(order) {
    const tr = element("tr");
    const orderCell = appendCell(tr, "order-cell");
    orderCell.appendChild(element("span", "order-id", order.id));
    if (order.hasDemoMessage) {
      const marker = element("span", "demo-message", "▣");
      marker.setAttribute("aria-label", "Демонстрационный значок: реальные сообщения не подключены");
      marker.title = "DEMO / реальные сообщения не подключены";
      orderCell.appendChild(marker);
    }
    orderCell.appendChild(element("div", "order-created", order.created));
    if (Number.isInteger(order.count)) {
      const counter = element("div", "item-count");
      counter.appendChild(document.createTextNode(String(order.count)+" = "));
      counter.appendChild(element("span", "count-ok", order.count));
      counter.appendChild(document.createTextNode(" / "));
      counter.appendChild(element("span", "count-miss", "0"));
      orderCell.appendChild(counter);
    }
    appendCell(tr, "tracking-cell").appendChild(element("span", "tracking-empty", "—"));
    appendCell(tr, "client-cell").appendChild(element("span", "customer-name", order.customer));
    appendCell(tr, "money", order.price);
    const status = appendCell(tr, "status-cell");
    status.appendChild(element("span", "status-pill", order.status));
    status.appendChild(element("div", "status-date", order.created));
    appendCell(tr, "placement-cell").appendChild(element("span", "place-pill", order.placement));
    return tr;
  }

  function render() {
    const rows = orders.filter(o =>
      (!clientFilter.value || o.customer === clientFilter.value) &&
      (!deliveryFilter.value || o.region === deliveryFilter.value) &&
      (!numberFilter.value || o.id === numberFilter.value) &&
      (!startDate.value || o.iso >= startDate.value) &&
      (!endDate.value || o.iso <= endDate.value)
    );
    body.replaceChildren();
    rows.forEach(o => body.appendChild(drawOne(o)));
    if (!rows.length) {
      const tr = element("tr");
      const td = element("td", "empty-row", "Нет совпадений среди девяти демонстрационных заказов");
      td.colSpan = 6;
      tr.appendChild(td);
      body.appendChild(tr);
    }
    found.textContent = String(rows.length);
  }
  form.addEventListener("submit", e => { e.preventDefault(); render(); });
  render();
})();