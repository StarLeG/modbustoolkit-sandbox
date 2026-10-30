// Watch list регистров (TASK-714): localStorage + WS-подписка.
// Список {slave, area, address} хранится в браузере (localStorage) и живёт
// между визитами. Добавление — кнопкой ★ на карте регистров (событие
// "watch-add" из registers.js). Live-значения — WS /ws/state: одно соединение
// на каждый slave списка, подписка только на его адреса.

const STORAGE_KEY = "modbus-watchlist";
const RECONNECT_DELAY_MS = 3000;

const state = {
  items: [],
  values: new Map(),
  sockets: new Map(), // slave -> WebSocket
  retries: new Map(), // slave -> timeout id
};

function el(id) {
  return document.getElementById(id);
}

function itemKey(item) {
  return item.slave + "/" + item.area + "/" + item.address;
}

function loadList() {
  try {
    const raw = JSON.parse(localStorage.getItem(STORAGE_KEY));
    return Array.isArray(raw) ? raw : [];
  } catch (error) {
    return []; // повреждённый JSON — начинаем с пустого списка
  }
}

function persist() {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state.items));
  } catch (error) {
    // storage недоступен (private mode) — список живёт до перезагрузки
  }
}

function status(text) {
  el("watch-status").textContent = text;
}

function render() {
  const body = el("watch-body");
  body.innerHTML = "";
  if (state.items.length === 0) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "note";
    cell.textContent = window.t("Список пуст - добавьте регистры кнопкой ★ в таблице выше.");
    row.appendChild(cell);
    body.appendChild(row);
    return;
  }
  for (const item of state.items) {
    const key = itemKey(item);
    const row = document.createElement("tr");
    for (const text of [String(item.slave), item.area.toUpperCase(), String(item.address)]) {
      const cell = document.createElement("td");
      cell.textContent = text;
      row.appendChild(cell);
    }
    const cellValue = document.createElement("td");
    cellValue.id = "watch-cell-" + key;
    cellValue.textContent = state.values.has(key) ? String(state.values.get(key)) : "—";
    row.appendChild(cellValue);
    const cellRemove = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "✕";
    button.title = window.t("Убрать из избранного");
    button.addEventListener("click", function () {
      removeItem(key);
    });
    cellRemove.appendChild(button);
    row.appendChild(cellRemove);
    body.appendChild(row);
  }
}

function addItem(item) {
  const key = itemKey(item);
  if (state.items.some((existing) => itemKey(existing) === key)) {
    status(window.t("Уже в избранном: {key}", { key: key }));
    return;
  }
  state.items.push(item);
  persist();
  render();
  reconnect();
  status(window.t("Добавлено: {key}", { key: key }));
}

function removeItem(key) {
  state.items = state.items.filter((item) => itemKey(item) !== key);
  persist();
  render();
  reconnect();
  status(window.t("Убрано: {key}", { key: key }));
}

function clearList() {
  state.items = [];
  persist();
  render();
  reconnect();
  status(window.t("Список очищен"));
}

function applyFrame(frame) {
  for (const [area, values] of Object.entries(frame.areas || {})) {
    for (const [address, value] of Object.entries(values)) {
      const key = frame.slave + "/" + area + "/" + Number(address);
      if (state.values.has(key) && state.values.get(key) === value) continue;
      state.values.set(key, value);
      const cell = el("watch-cell-" + key);
      if (cell) cell.textContent = String(value);
      // событие ловит chart.js (TASK-715) для истории графика
      document.dispatchEvent(
        new CustomEvent("watch-value", {
          detail: { slave: frame.slave, area: area, address: Number(address), value: value },
        })
      );
    }
  }
}

function connectSlave(slave) {
  const items = state.items.filter((item) => item.slave === slave);
  if (items.length === 0) return;
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(scheme + location.host + "/ws/state");
  state.sockets.set(slave, ws);
  ws.onopen = function () {
    ws.send(
      JSON.stringify({
        slave: slave,
        areas: [...new Set(items.map((item) => item.area))],
        addresses: [...new Set(items.map((item) => item.address))],
      })
    );
  };
  ws.onmessage = function (event) {
    const frame = JSON.parse(event.data);
    if (frame.type === "state" && frame.slave === slave) applyFrame(frame);
  };
  ws.onclose = function () {
    state.sockets.delete(slave);
    // slave мог исчезнуть из списка, пока соединение жило
    if (state.items.some((item) => item.slave === slave) && !state.retries.has(slave)) {
      state.retries.set(
        slave,
        setTimeout(function () {
          state.retries.delete(slave);
          connectSlave(slave);
        }, RECONNECT_DELAY_MS)
      );
    }
  };
}

function reconnect() {
  for (const ws of state.sockets.values()) {
    ws.onclose = null;
    ws.close();
  }
  state.sockets.clear();
  for (const timer of state.retries.values()) clearTimeout(timer);
  state.retries.clear();
  for (const slave of new Set(state.items.map((item) => item.slave))) {
    connectSlave(slave);
  }
}

function init() {
  state.items = loadList();
  render();
  reconnect();
  document.addEventListener("watch-add", function (event) {
    addItem(event.detail);
  });
  el("watch-clear").addEventListener("click", clearList);
}

init();
