// Карта регистров (TASK-711, 712): таблица адресов по слейву, live через WS.
// Геометрия карты — window.REGISTER_MAP (серверный рендер из SLAVE_SPECS).
// Пагинация — по 64 адреса; WS-подписка только на адреса видимой страницы.
// Форматы значений (TASK-712): DEC / DEC (знаковый) / HEX / FLOAT — по формату
// регистра слейва; для cl/di — только 0/1. Режимы FLOAT для uint32 и DEC/HEX
// для float — reinterpret битов значения.

const PAGE_SIZE = 64;
const RECONNECT_MAX_MS = 15000;

const MODE_LABELS = {
  dec: "DEC",
  signed: "DEC (знаковый)",
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
  page: 0,
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
  const start = state.page * PAGE_SIZE;
  return state.addresses.slice(start, start + PAGE_SIZE);
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
  el("reg-fmt").textContent = "Формат: " + spec().fmt;
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
      : "—";
    row.appendChild(cellAddress);
    row.appendChild(cellValue);
    body.appendChild(row);
  }
  const pages = Math.max(1, Math.ceil(state.addresses.length / PAGE_SIZE));
  el("reg-page").textContent = "Страница " + (state.page + 1) + " из " + pages;
  el("reg-prev").disabled = state.page === 0;
  el("reg-next").disabled = state.page + 1 >= pages;
}

function applyFrame(frame) {
  const values = (frame.areas && frame.areas[state.area]) || {};
  for (const [address, value] of Object.entries(values)) {
    state.values.set(Number(address), value);
    const cell = el("reg-cell-" + address);
    if (cell) cell.textContent = formatValue(value);
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
  state.page = 0;
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
    if (state.page > 0) {
      state.page -= 1;
      renderTable();
      subscribe();
    }
  });
  el("reg-next").addEventListener("click", function () {
    const pages = Math.ceil(state.addresses.length / PAGE_SIZE);
    if (state.page + 1 < pages) {
      state.page += 1;
      renderTable();
      subscribe();
    }
  });
  renderAreas();
  refresh();
  connect();
}

init();
