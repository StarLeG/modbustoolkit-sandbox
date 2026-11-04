// Карта регистров (TASK-711, 712): таблица адресов по слейву, live через WS.
// Геометрия карты — window.REGISTER_MAP (серверный рендер из SLAVE_SPECS).
// Пагинация — по 10 адресов: окно начинается с произвольного «Начального
// адреса», кнопки «Назад»/«Вперёд» сдвигают окно на страницу, поле «Страница» —
// быстрый переход по номеру. WS-подписка только на адреса видимого окна.
// Форматы значений (TASK-712): DEC / DEC (знаковый) / HEX / FLOAT — по формату
// регистра слейва; для cl/di — только 0/1. Режимы FLOAT для uint32 и DEC/HEX
// для float — reinterpret битов значения.

const PAGE_SIZE = 10;
const RECONNECT_MAX_MS = 15000;

const MODE_LABELS = {
  dec: "DEC",
  signed: window.t("DEC (знаковый)"),
  hex: "HEX",
  float: "FLOAT",
  bit: "0/1",
};

const map = window.REGISTER_MAP;

const state = {
  slave: 1,
  area: null,
  mode: "dec",
  addresses: [],
  values: new Map(),
  start: 0, // индекс первого отображаемого адреса
  ws: null,
  reconnectAttempt: 0,
  reconnectTimer: null,
};

function el(id) {
  return document.getElementById(id);
}

function spec() {
  return map[String(state.slave)];
}

function areaList() {
  return Object.keys(spec().areas);
}

function fmtKind() {
  const fmt = spec().fmt;
  if (fmt.startsWith("uint16")) return "uint16";
  if (fmt.startsWith("uint32")) return "uint32";
  return "float";
}

function availableModes() {
  if (state.area === "cl" || state.area === "di") return ["bit"];
  const kind = fmtKind();
  if (kind === "uint16") return ["dec", "signed", "hex"];
  if (kind === "uint32") return ["dec", "signed", "hex", "float"];
  return ["float", "dec", "signed", "hex"];
}

function floatToBits(value) {
  const buffer = new ArrayBuffer(4);
  new DataView(buffer).setFloat32(0, value);
  return new DataView(buffer).getUint32(0);
}

function bitsToFloat(bits) {
  const buffer = new ArrayBuffer(4);
  new DataView(buffer).setUint32(0, bits >>> 0);
  return new DataView(buffer).getFloat32(0);
}

function toSigned(bits, width) {
  const limit = width === 16 ? 0x8000 : 0x80000000;
  const span = width === 16 ? 0x10000 : 0x100000000;
  return bits >= limit ? bits - span : bits;
}

function formatValue(value) {
  if (value === true) return "1";
  if (value === false) return "0";
  const kind = fmtKind();
  const bits = kind === "float" ? floatToBits(value) : value;
  switch (state.mode) {
    case "signed":
      return String(toSigned(bits, kind === "uint16" ? 16 : 32));
    case "hex":
      return (
        "0x" +
        (bits >>> 0).toString(16).toUpperCase().padStart(kind === "uint16" ? 4 : 8, "0")
      );
    case "float":
      return kind === "float" ? String(value) : String(bitsToFloat(value));
    default: // "dec" / "bit"
      return String(value);
  }
}

function editable() {
  return state.area === "cl" || state.area === "hr";
}

function showError(text) {
  el("reg-status").textContent = text;
}

async function saveValue(address, raw) {
  // Ввод: 0x… — hex, иначе число (для cl — 0/1/true/false); сервер клампует
  let value;
  const trimmed = raw.trim();
  if (state.area === "cl") {
    value = trimmed === "1" || trimmed.toLowerCase() === "true";
  } else if (/^0x[0-9a-f]+$/i.test(trimmed)) {
    value = parseInt(trimmed, 16);
  } else {
    value = Number(trimmed);
    if (Number.isNaN(value)) {
      showError(window.t("Не число: {text}", { text: trimmed }));
      renderTable();
      return;
    }
  }
  try {
    const response = await fetch("/api/modbus/write", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        slave: state.slave,
        area: state.area,
        address: address,
        value: value,
      }),
    });
    const body = await response.json();
    if (!response.ok) {
      showError(
        window.t("Запись отклонена ({status}): {detail}", {
          status: response.status,
          detail: body.detail || response.statusText,
        })
      );
      renderTable();
      return;
    }
    showError("");
    state.values.set(address, body.value);
    renderTable();
  } catch (error) {
    showError(window.t("Ошибка записи: {error}", { error: error }));
    renderTable();
  }
}

function startEdit(address) {
  const cell = el("reg-cell-" + address);
  cell.classList.add("editing");  // WS-кадры не трогают ячейку до конца правки
  cell.textContent = "";
  let field;
  if (state.area === "cl") {
    // coil принимает только 0/1 - выбираем из списка
    field = document.createElement("select");
    for (const item of ["0", "1"]) {
      const option = document.createElement("option");
      option.value = item;
      option.textContent = item;
      field.appendChild(option);
    }
    field.value = state.values.get(address) ? "1" : "0";
    field.addEventListener("change", function () {
      saveValue(address, field.value); // выбор значения сразу пишет
    });
  } else {
    field = document.createElement("input");
    field.value = state.values.has(address) ? String(state.values.get(address)) : "";
  }
  field.addEventListener("click", function (event) {
    event.stopPropagation(); // клик по полю не всплывает до ячейки
  });
  const button = document.createElement("button");
  button.type = "button";
  button.textContent = "✓";
  button.addEventListener("mousedown", function (event) {
    event.preventDefault(); // blur не прерывает правку перед нажатием
  });
  button.addEventListener("click", function (event) {
    event.stopPropagation();
    saveValue(address, field.value);
  });
  field.addEventListener("keydown", function (event) {
    if (event.key === "Enter") saveValue(address, field.value);
    if (event.key === "Escape") renderTable();
  });
  field.addEventListener("blur", function () {
    renderTable(); // отмена
  });
  cell.appendChild(field);
  cell.appendChild(button);
  field.focus();
}

function addressesOf(area) {
  const out = [];
  for (const [start, stop, step] of spec().areas[area]) {
    for (let address = start; address <= stop; address += step) {
      out.push(address);
    }
  }
  return out;
}

function pageAddresses() {
  return state.addresses.slice(state.start, state.start + PAGE_SIZE);
}

function pagesCount() {
  return Math.max(1, Math.ceil(state.addresses.length / PAGE_SIZE));
}

// Ближайший адрес области, не меньше заданного: у slave 10 в карте есть «дырки»
function indexForAddress(address) {
  for (let index = 0; index < state.addresses.length; index += 1) {
    if (state.addresses[index] >= address) return index;
  }
  return Math.max(0, state.addresses.length - 1);
}

function gotoStart(index) {
  const total = state.addresses.length;
  if (total === 0) return;
  showError("");
  state.start = Math.max(0, Math.min(index, total - 1));
  renderTable();
  subscribe();
  loadRest();
}

function stepPage(delta) {
  // ход кнопками — целыми страницами; последняя страница может быть короче
  const maxStart = (pagesCount() - 1) * PAGE_SIZE;
  gotoStart(Math.max(0, Math.min(state.start + delta, maxStart)));
}

function applyStartField() {
  const raw = el("reg-start").value.trim();
  if (raw === "") return;
  const address = Number(raw);
  if (!Number.isFinite(address) || address < 1) {
    showError(window.t("Адрес - целое число от 1 и больше (из docs/HRIR_SPEC.md)."));
    syncJumpFields(true);
    return;
  }
  gotoStart(indexForAddress(Math.round(address)));
}

function applyPageField() {
  const raw = el("reg-page-input").value.trim();
  if (raw === "") return;
  const page = Number(raw);
  if (!Number.isFinite(page) || page < 1) {
    showError(window.t("Страница - целое число от 1 и больше."));
    syncJumpFields(true);
    return;
  }
  gotoStart((Math.min(Math.round(page), pagesCount()) - 1) * PAGE_SIZE);
}

function renderAreas() {
  const select = el("reg-area");
  const available = areaList();
  select.innerHTML = "";
  for (const area of available) {
    const option = document.createElement("option");
    option.value = area;
    option.textContent = area.toUpperCase();
    select.appendChild(option);
  }
  state.area = available.includes(state.area) ? state.area : available[0];
  select.value = state.area;
}

function renderFormats() {
  const select = el("reg-format");
  const modes = availableModes();
  select.innerHTML = "";
  for (const mode of modes) {
    const option = document.createElement("option");
    option.value = mode;
    option.textContent = MODE_LABELS[mode];
    select.appendChild(option);
  }
  state.mode = modes.includes(state.mode) ? state.mode : modes[0];
  select.value = state.mode;
}

function renderTable() {
  const body = el("reg-body");
  body.innerHTML = "";
  for (const address of pageAddresses()) {
    const row = document.createElement("tr");
    const cellAddress = document.createElement("td");
    cellAddress.textContent = String(address);
    const cellValue = document.createElement("td");
    cellValue.id = "reg-cell-" + address;
    cellValue.textContent = state.values.has(address)
      ? formatValue(state.values.get(address))
      : "-";
    if (editable()) {
      cellValue.classList.add("editable");
      cellValue.title = window.t("Нажмите, чтобы изменить (CL/HR общие, last-write-wins)");
      cellValue.addEventListener("click", function () {
        if (cellValue.classList.contains("editing")) return; // клик по редактору не перезапускает правку
        startEdit(address);
      });
    }
    const cellStar = document.createElement("td");
    const star = document.createElement("button");
    star.type = "button";
    star.textContent = "★";
    star.title = window.t("В избранное");
    star.addEventListener("click", function () {
      // событие ловит watchlist.js (TASK-714)
      document.dispatchEvent(
        new CustomEvent("watch-add", {
          detail: { slave: state.slave, area: state.area, address: address },
        })
      );
    });
    cellStar.appendChild(star);
    row.appendChild(cellAddress);
    row.appendChild(cellValue);
    row.appendChild(cellStar);
    body.appendChild(row);
  }
  updateNav();
}

function updateNav() {
  el("reg-page").textContent = window.t("из {pages}", { pages: pagesCount() });
  el("reg-prev").disabled = state.start === 0;
  el("reg-next").disabled = state.start >= (pagesCount() - 1) * PAGE_SIZE;
  syncJumpFields();
}

// Поля перехода показывают текущую позицию; пока в них печатают, не перетираем
function syncJumpFields(force) {
  const total = state.addresses.length;
  const startField = el("reg-start");
  const pageField = el("reg-page-input");
  if (total > 0) {
    startField.min = String(state.addresses[0]);
    startField.max = String(state.addresses[total - 1]);
  }
  pageField.min = "1";
  pageField.max = String(pagesCount());
  if (total > 0 && (force || document.activeElement !== startField)) {
    startField.value = String(state.addresses[state.start]);
  }
  if (force || document.activeElement !== pageField) {
    pageField.value = String(Math.floor(state.start / PAGE_SIZE) + 1);
  }
}

function applyFrame(frame) {
  const values = (frame.areas && frame.areas[state.area]) || {};
  for (const [address, value] of Object.entries(values)) {
    state.values.set(Number(address), value);
    const cell = el("reg-cell-" + address);
    if (cell && !cell.classList.contains("editing")) {
      cell.textContent = formatValue(value);
    }
  }
}

async function loadRest() {
  try {
    const response = await fetch(
      "/api/modbus/state?slave=" + state.slave + "&area=" + state.area
    );
    if (!response.ok) return;
    applyFrame(await response.json());
  } catch (error) {
    // REST недоступен — ячейки остаются «—”, WS-кадры догонят
  }
}

function subscribe() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  state.ws.send(
    JSON.stringify({
      slave: state.slave,
      areas: [state.area],
      addresses: pageAddresses(),
    })
  );
}

function connect() {
  const scheme = location.protocol === "https:" ? "wss://" : "ws://";
  const ws = new WebSocket(scheme + location.host + "/ws/state");
  state.ws = ws;
  ws.onopen = function () {
    state.reconnectAttempt = 0;
    subscribe();
  };
  ws.onmessage = function (event) {
    const frame = JSON.parse(event.data);
    if (frame.type === "state") applyFrame(frame);
  };
  ws.onclose = function () {
    // реконнект с backoff (ADR-008 п. 4); без WS страница живёт на REST
    const delay = Math.min(1000 * 2 ** state.reconnectAttempt, RECONNECT_MAX_MS);
    state.reconnectAttempt += 1;
    state.reconnectTimer = setTimeout(connect, delay);
    loadRest();
  };
}

function refresh() {
  state.addresses = addressesOf(state.area);
  state.start = 0;
  state.values.clear();
  renderFormats();
  renderTable();
  subscribe();
  loadRest();
}

function init() {
  el("reg-slave").addEventListener("change", function () {
    state.slave = Number(this.value);
    renderAreas();
    refresh();
  });
  el("reg-area").addEventListener("change", function () {
    state.area = this.value;
    refresh();
  });
  el("reg-format").addEventListener("change", function () {
    state.mode = this.value;
    renderTable();
  });
  el("reg-prev").addEventListener("click", function () {
    stepPage(-PAGE_SIZE);
  });
  el("reg-next").addEventListener("click", function () {
    stepPage(PAGE_SIZE);
  });
  el("reg-start").addEventListener("change", applyStartField);
  el("reg-start").addEventListener("keydown", function (event) {
    if (event.key === "Enter") applyStartField();
  });
  el("reg-page-input").addEventListener("change", applyPageField);
  el("reg-page-input").addEventListener("keydown", function (event) {
    if (event.key === "Enter") applyPageField();
  });
  renderAreas();
  refresh();
  connect();
}

init();
