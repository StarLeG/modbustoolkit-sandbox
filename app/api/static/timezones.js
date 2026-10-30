// Часовые пояса slave 10 (TASK-731…732): 27 зон + Unix timestamp.
// Значения — из IR slave 10 (WS /ws/state с REST-фолбэком): блок зоны Z —
// адреса 1+10*Z .. 8+10*Z (год, месяц, день, час, минута, секунда, день
// недели, смещение UTC); IR 300 — Unix timestamp (uint32 ABCD). Расхождение —
// разница серверного timestamp с системным временем браузера.

const ZONE_COUNT = 27;
const ZONE_SLOTS = 10;
const WEEKDAYS = [
  window.t("Пн"),
  window.t("Вт"),
  window.t("Ср"),
  window.t("Чт"),
  window.t("Пт"),
  window.t("Сб"),
  window.t("Вс"),
];
const RECONNECT_MAX_MS = 15000;

const state = { ws: null, reconnectAttempt: 0, reconnectTimer: null, values: new Map() };

function el(id) {
  return document.getElementById(id);
}

function pad2(value) {
  return String(value).padStart(2, "0");
}

// Смещение на проводе — int16 two's complement (slave10._K_OFFSET).
function toSigned16(raw) {
  return raw >= 0x8000 ? raw - 0x10000 : raw;
}

function offsetLabel(offset) {
  const sign = offset < 0 ? "−" : "+";
  return "UTC" + sign + pad2(Math.abs(offset)) + ":00";
}

function zoneValue(zone, k) {
  return state.values.get(String(1 + zone * ZONE_SLOTS + k));
}

function renderZones() {
  for (let zone = 0; zone < ZONE_COUNT; zone++) {
    const year = zoneValue(zone, 0);
    const month = zoneValue(zone, 1);
    const day = zoneValue(zone, 2);
    const hour = zoneValue(zone, 3);
    const minute = zoneValue(zone, 4);
    const second = zoneValue(zone, 5);
    const weekday = zoneValue(zone, 6);
    const offset = zoneValue(zone, 7);
    if (year === undefined) continue;
    el("tz-" + zone + "-date").textContent =
      year + "-" + pad2(month) + "-" + pad2(day);
    el("tz-" + zone + "-time").textContent =
      pad2(hour) + ":" + pad2(minute) + ":" + pad2(second);
    el("tz-" + zone + "-weekday").textContent =
      weekday >= 1 && weekday <= 7 ? WEEKDAYS[weekday - 1] : String(weekday);
    el("tz-" + zone + "-offset").textContent = offsetLabel(toSigned16(offset));
  }
}

function renderTimestamp() {
  const unix = state.values.get("300");
  if (unix === undefined) return;
  el("tz-unix").textContent = String(unix);
}

// Системное время и расхождение — живые, между кадрами WS тоже тикают.
function renderClock() {
  const now_s = Date.now() / 1000;
  el("tz-system").textContent = String(Math.round(now_s));
  const unix = state.values.get("300");
  if (unix === undefined) return;
  const delta = unix - now_s;
  const sign = delta < 0 ? "−" : "+";
  el("tz-delta").textContent = window.t("{v} с", { v: sign + Math.abs(delta).toFixed(1) });
}

function applyFrame(frame) {
  const values = (frame.areas && frame.areas.ir) || {};
  for (const [address, value] of Object.entries(values)) {
    state.values.set(address, value);
  }
  renderZones();
  renderTimestamp();
  renderClock();
}

async function loadRest() {
  try {
    const response = await fetch("/api/modbus/state?slave=10&area=ir");
    if (!response.ok) return;
    applyFrame(await response.json());
  } catch (error) {
    // REST недоступен — значения остаются прежними, WS-кадры догонят
  }
}

function subscribe() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  const addresses = [300];
  for (let address = 1; address <= 268; address++) addresses.push(address);
  state.ws.send(JSON.stringify({ slave: 10, areas: ["ir"], addresses: addresses }));
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

function init() {
  connect();
  loadRest();
  setInterval(renderClock, 500);
}

init();
