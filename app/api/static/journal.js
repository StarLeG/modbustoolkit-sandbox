// Журнал событий (TASK-745): последние 1000 записей + live через /ws/events.
// Стартовый снимок — GET /api/modbus/journal?limit=1000 (от старых к новым,
// в таблице — новые сверху); новые события вставляются в начало. Дубликаты
// (снимок и WS могут пересечься) отбрасываются по ключу записи.

const LIMIT = 1000;

function el(id) {
  return document.getElementById(id);
}

function eventKey(event) {
  return [
    event.timestamp,
    event.action,
    event.slave,
    event.area,
    event.address,
    event.source,
    event.ip,
    JSON.stringify(event.value),
  ].join("|");
}

function fmtTime(timestamp) {
  return new Date(timestamp * 1000).toLocaleString();
}

function fmtValue(value) {
  if (value === null || value === undefined) return "—";
  return typeof value === "object" ? JSON.stringify(value) : String(value);
}

function rowFor(event) {
  const tr = document.createElement("tr");
  for (const cell of [
    fmtTime(event.timestamp),
    event.source,
    event.ip || "—",
    event.action,
    String(event.slave),
    event.area || "—",
    event.address === null || event.address === undefined ? "—" : String(event.address),
    fmtValue(event.value),
  ]) {
    const td = document.createElement("td");
    td.textContent = cell;
    tr.appendChild(td);
  }
  return tr;
}

// Ключи показанных записей, новые сверху; вместе с DOM ограничиваются LIMIT.
let shownKeys = [];

function prepend(event) {
  const key = eventKey(event);
  if (shownKeys.includes(key)) return;
  const body = el("journal-rows");
  body.insertBefore(rowFor(event), body.firstChild);
  shownKeys.unshift(key);
  while (shownKeys.length > LIMIT) {
    shownKeys.pop();
    body.removeChild(body.lastChild);
  }
}

function renderAll(items) {
  const body = el("journal-rows");
  body.textContent = "";
  shownKeys = [];
  for (let i = items.length - 1; i >= 0; i--) {
    prepend(items[i]);
  }
}

async function load() {
  const status = el("journal-status");
  const response = await fetch("/api/modbus/journal?limit=" + LIMIT);
  if (!response.ok) {
    status.textContent = "Ошибка загрузки (" + response.status + ").";
    return;
  }
  const body = await response.json();
  renderAll(body.items);
  status.textContent = "Записей в буфере: " + body.total + ".";
}

let attempt = 0;

function connect() {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(scheme + location.host + "/ws/events");
  ws.onopen = () => {
    attempt = 0;
    el("journal-status").textContent = "Live подключён (/ws/events).";
  };
  ws.onmessage = (message) => {
    const frame = JSON.parse(message.data);
    if (frame.type === "event") prepend(frame);
  };
  ws.onclose = () => {
    // Backoff по ADR-005: min(1000 * 2^attempt, 15000)
    const delay = Math.min(1000 * 2 ** attempt, 15000);
    attempt += 1;
    el("journal-status").textContent = "Переподключение /ws/events…";
    setTimeout(connect, delay);
  };
}

function init() {
  el("journal-refresh").addEventListener("click", load);
  load();
  connect();
}

init();
