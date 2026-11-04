// Карта регистров (TASK-711, 712): таблица адресов по слейву, live через WS.
// Геометрия карты — window.REGISTER_MAP (серверный рендер из SLAVE_SPECS).
// Пагинация — по 10 адресов: окно начинается с произвольного «Начального
// адреса», кнопки «Назад»/«Вперёд» сдвигают окно на страницу, поле «Страница» —
// быстрый переход по номеру. WS-подписка только на адреса видимого окна
// (плюс следующий адрес — для 32-битной пары N, N+1 последней строки).
// Формат значения выбирается в каждой строке: uint16/int16/HEX16 или
// 32-битные uint32/int32/float32 × ABCD/CDAB/BADC/DCBA и HEX32; 32-битный
// формат объединяет регистры N и N+1 (слот сетки слейвов 2..9 — то же самое).
// Кадры несут логические значения по формату слейва, reinterpretation идёт на
// клиенте через WIRE_PERM — зеркало перестановок app/modbus/codec.py.
// Выбранный формат хранится в localStorage и переживает перезагрузку.
// Колонка «Тег» — имя сигнала: поведение регистра (slave 1..9, конфиг из
// REST /api/modbus/behaviors) либо метрика времени (slave 10, HRIR_SPEC).
// Колонка «Поведение» (TASK-716) — выбор kind строкой и «⚙» с параметрами:
// смена kind сразу ставит поведение (PUT /api/modbus/behaviors/...), «⚙»
// открывает строку настроек (params + locked, «Сбросить к дефолту» — batch
// с remove:true). Поведения общие для всех пользователей; slave 10 — без них.
// Колонка «Тренд, 60 с» — спарклайн строки в её формате: кольцевой буфер
// сырых значений за 60 с, перерисовка раз в 500 мс.

import {
  collectParams as collectFormParams,
  defaultParams,
  fillParams,
  kindLabel,
  kindsForArea,
  renderParams as renderFormParams,
} from "./behavior-form.js";

const PAGE_SIZE = 10;
const RECONNECT_MAX_MS = 15000;
const FORMATS_KEY = "modbus-reg-formats";
const HISTORY_S = 60;
const TREND_H = 28;

// WIRE_PERM[order][i] — индекс байта значения (A=0, B=1, C=2, D=3),
// попадающего в i-й байт на проводе (байты внутри регистра big-endian).
const WIRE_PERM = {
  ABCD: [0, 1, 2, 3],
  CDAB: [2, 3, 0, 1],
  BADC: [1, 0, 3, 2],
  DCBA: [3, 2, 1, 0],
};

const FORMAT_GROUPS = [
  [window.t("16 бит"), [["uint16", "uint16"], ["int16", "int16"], ["hex16", "HEX16"]]],
  ["uint32", [
    ["uint32_abcd", "uint32 ABCD"], ["uint32_cdab", "uint32 CDAB"],
    ["uint32_badc", "uint32 BADC"], ["uint32_dcba", "uint32 DCBA"],
  ]],
  ["int32", [
    ["int32_abcd", "int32 ABCD"], ["int32_cdab", "int32 CDAB"],
    ["int32_badc", "int32 BADC"], ["int32_dcba", "int32 DCBA"],
  ]],
  ["float32", [
    ["float32_abcd", "float32 ABCD"], ["float32_cdab", "float32 CDAB"],
    ["float32_badc", "float32 BADC"], ["float32_dcba", "float32 DCBA"],
  ]],
  ["HEX", [["hex32", "HEX32"]]],
];

const FORMAT_LABELS = {};
const FORMAT_SET = new Set();
for (const [, items] of FORMAT_GROUPS) {
  for (const [code, label] of items) {
    FORMAT_LABELS[code] = label;
    FORMAT_SET.add(code);
  }
}

const map = window.REGISTER_MAP;

const state = {
  slave: 1,
  area: null,
  addresses: [],
  values: new Map(),
  history: new Map(), // адрес → [{t, v}] сырые значения за HISTORY_S
  behaviors: new Map(), // адрес → поведение из REST (имя для тега строки)
  bhEdit: null, // адрес строки с открытой формой настроек поведения
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

// ---------- кодек на клиенте: значения ↔ слова регистров ----------

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

// Порядок слов из имени формата: "uint32_cdab" → "CDAB"
function orderOf(fmt) {
  return fmt.split("_").pop().toUpperCase();
}

function codeWidth(code) {
  return code === "hex16" || code === "uint16" || code === "int16" ? 16 : 32;
}

// Байты значения → слова регистров (зеркало codec.encode)
function bitsToWords(bits, order) {
  const raw = [(bits >>> 24) & 0xff, (bits >>> 16) & 0xff, (bits >>> 8) & 0xff, bits & 0xff];
  const perm = WIRE_PERM[order];
  const wire = [0, 0, 0, 0];
  for (let i = 0; i < 4; i += 1) wire[i] = raw[perm[i]];
  return [(wire[0] << 8) | wire[1], (wire[2] << 8) | wire[3]];
}

// Слова регистров → биты значения (зеркало codec.decode)
function wordsToBits(words, order) {
  const wire = [
    (words[0] >>> 8) & 0xff, words[0] & 0xff,
    (words[1] >>> 8) & 0xff, words[1] & 0xff,
  ];
  const perm = WIRE_PERM[order];
  const raw = [0, 0, 0, 0];
  for (let i = 0; i < 4; i += 1) raw[perm[i]] = wire[i];
  return (((raw[0] << 24) | (raw[1] << 16) | (raw[2] << 8) | raw[3]) >>> 0);
}

// Логическое значение (по формату слейва) → слова регистров слота
function valueToWords(value, fmt) {
  if (fmt === "uint16") return [value & 0xffff];
  const bits = fmt.startsWith("float") ? floatToBits(value) : value >>> 0;
  return bitsToWords(bits, orderOf(fmt));
}

// Слова регистров слота → логическое значение (по формату слейва)
function wordsToValue(words, fmt) {
  if (fmt === "uint16") return words[0] & 0xffff;
  const bits = wordsToBits(words, orderOf(fmt));
  return fmt.startsWith("float") ? bitsToFloat(bits) : bits;
}

// ---------- нативный слот строки и слова её регистров ----------

// Как логическое значение строки ложится на регистры: 32-битные слейвы (2..9)
// несут слот N..N+1 в одном значении, slave 1 и метрики slave 10 — слово
// регистра N; IR 300 slave 10 — Unix timestamp (UINT32_ABCD, HRIR_SPEC.md).
function slotOf(address) {
  if (fmtKind() !== "uint16") return { width: 32, fmt: spec().fmt };
  if (state.slave === 10 && state.area === "ir" && address === 300) {
    return { width: 32, fmt: "uint32_abcd" };
  }
  return { width: 16, fmt: "uint16" };
}

// Слово регистра N (то, что стоит в регистре N), или null
function wireAt(address) {
  if (state.values.has(address)) {
    return valueToWords(state.values.get(address), slotOf(address).fmt)[0];
  }
  // строка-продолжение слота: IR 301 slave 10 — младшее слово timestamp
  const head = address - 1;
  if (state.values.has(head) && slotOf(head).width === 32) {
    return valueToWords(state.values.get(head), slotOf(head).fmt)[1];
  }
  return null;
}

// Слова пары регистров (N, N+1) для 32-битного формата, или null
function pairWords(address) {
  const slot = slotOf(address);
  if (slot.width === 32 && state.values.has(address)) {
    return valueToWords(state.values.get(address), slot.fmt);
  }
  const first = wireAt(address);
  const second = wireAt(address + 1);
  if (first === null || second === null) return null;
  return [first, second];
}

// Пара N, N+1 доступна по геометрии карты (не зависит от загрузки значений)
function pairAvailable(address) {
  if (slotOf(address).width === 32) return true;
  return state.addresses.includes(address + 1);
}

// ---------- формат строки: выбор пользователя ----------

function loadFormats() {
  try {
    return JSON.parse(localStorage.getItem(FORMATS_KEY) || "{}");
  } catch (error) {
    return {};
  }
}

const rowFormats = loadFormats();

function rowKey(address) {
  return state.slave + ":" + state.area + ":" + address;
}

function defaultCode(address) {
  const fmt = slotOf(address).fmt;
  if (fmt === "uint16") return "uint16";
  if (fmt.startsWith("uint32")) return fmt;
  return "float32_" + fmt.split("_").pop();
}

function rowCode(address) {
  const stored = rowFormats[rowKey(address)];
  return stored && FORMAT_SET.has(stored) ? stored : defaultCode(address);
}

function setRowCode(address, code) {
  rowFormats[rowKey(address)] = code;
  try {
    localStorage.setItem(FORMATS_KEY, JSON.stringify(rowFormats));
  } catch (error) {
    // приватный режим браузера — выбор живёт до перезагрузки
  }
}

// ---------- отображение значения в формате строки ----------

// Пара слов → число в 32-битном формате строки
function numberFromWords(words, code) {
  if (code === "hex32") return ((words[0] << 16) | words[1]) >>> 0;
  const bits = wordsToBits(words, orderOf(code));
  if (code.startsWith("int32")) return toSigned(bits, 32);
  if (code.startsWith("float32")) return bitsToFloat(bits);
  return bits;
}

// Число строки в выбранном формате (для ячейки и тренда), или null
function rowNumber(address, code) {
  if (state.area === "cl" || state.area === "di") {
    return state.values.has(address) ? (state.values.get(address) ? 1 : 0) : null;
  }
  if (codeWidth(code) === 16) {
    const word = wireAt(address);
    if (word === null) return null;
    return code === "int16" ? toSigned(word, 16) : word;
  }
  const words = pairWords(address);
  if (words === null) return null;
  return numberFromWords(words, code);
}

function formatNumber(number, code) {
  if (number === null) return "—";
  if (code === "hex16") {
    return "0x" + (number & 0xffff).toString(16).toUpperCase().padStart(4, "0");
  }
  if (code === "hex32") {
    const bits = number >>> 0;
    return (
      "0x" + ((bits >>> 16) & 0xffff).toString(16).toUpperCase().padStart(4, "0") +
      " 0x" + (bits & 0xffff).toString(16).toUpperCase().padStart(4, "0")
    );
  }
  return String(number);
}

function formatDisplay(address) {
  const code = rowCode(address);
  return formatNumber(rowNumber(address, code), code);
}

// ---------- теги: имена сигналов строк ----------

// Метрики блока зоны slave 10 (docs/HRIR_SPEC.md): смещения 1..10 внутри зоны
const S10_METRICS = [
  "Год", "Месяц", "День", "Час", "Минута", "Секунда",
  "День недели", "Смещение UTC", "резерв", "резерв",
];

// Тег slave 10: зона Z=0..26 и метрика её блока; 300..301 — Unix timestamp
function slave10Tag(address) {
  if (address >= 300) return "Unix timestamp";
  const zone = Math.floor((address - 1) / 10);
  const metric = S10_METRICS[(address - 1) % 10];
  const hours = zone <= 14 ? zone : zone - 14;
  const sign = zone <= 14 ? "+" : "-";
  return "UTC" + sign + (hours < 10 ? "0" : "") + hours + " · " + window.t(metric);
}

// Тег строки: поведение регистра (slave 1..9) либо метрика времени (slave 10)
function tagText(address) {
  if (state.slave === 10) return slave10Tag(address);
  const item = state.behaviors.get(address);
  if (!item) return "";
  if (item.kind === "follow" && item.params && item.params.source) {
    return window.t("копия {source}", { source: item.params.source });
  }
  return kindLabel(item.kind);
}

// ---------- поведение строки: выбор kind и настройки (TASK-716) ----------

function behaviorAt(address) {
  return state.behaviors.get(address) || null;
}

// PUT привязки поведения: kind + params + locked; ответ обновляет кэш строки.
// Поведения общие (last-write-wins) — настройка действует для всех пользователей.
async function putBehavior(address, kind, params, locked) {
  showError(window.t("Установка…"));
  try {
    const response = await fetch(
      "/api/modbus/behaviors/" + state.slave + "/" + state.area + "/" + address,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: kind, params: params, locked: locked }),
      }
    );
    const body = await response.json();
    if (!response.ok) {
      showError(
        window.t("Запись отклонена ({status}): {detail}", {
          status: response.status,
          detail: body.detail || response.statusText,
        })
      );
      renderTable(); // селект возвращается к установленному поведению
      return;
    }
    state.behaviors.set(address, body);
    showError(
      window.t("Установлено: slave {slave} {area} {address}, поведение {kind}.", {
        slave: body.slave,
        area: body.area,
        address: body.address,
        kind: kindLabel(body.kind),
      })
    );
    renderTable();
  } catch (error) {
    showError(window.t("Ошибка: {text}", { text: error }));
    renderTable();
  }
}

// Сброс привязки к дефолту (batch с remove:true), конфиг перечитывается из API
async function resetBehavior(address) {
  showError(window.t("Сброс к дефолту…"));
  try {
    const response = await fetch("/api/modbus/behaviors/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        items: [{ slave: state.slave, area: state.area, address: address, remove: true }],
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
      return;
    }
    showError(
      window.t("Сброшено к дефолту: slave {slave} {area} {address}.", {
        slave: state.slave,
        area: state.area,
        address: address,
      })
    );
    await loadBehaviors(); // дефолты приходят с сервера
  } catch (error) {
    showError(window.t("Ошибка: {text}", { text: error }));
  }
}

// Селект поведения строки: kind из NUMERIC/DISCRETE по области; смена сразу
// ставит поведение с параметрами по умолчанию (настройки открываются по «⚙»)
function buildBehaviorSelect(address) {
  const select = document.createElement("select");
  select.className = "bh-kind";
  select.id = "reg-bh-select-" + address;
  const item = behaviorAt(address);
  for (const kind of kindsForArea(state.area)) {
    const option = document.createElement("option");
    option.value = kind;
    option.textContent = kindLabel(kind);
    select.appendChild(option);
  }
  if (!item) {
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = "…";
    select.insertBefore(placeholder, select.firstChild);
    select.value = "";
    select.disabled = true; // конфиг ещё не загружен
    return select;
  }
  select.value = item.kind;
  select.addEventListener("change", function () {
    if (!select.value) return;
    putBehavior(address, select.value, defaultParams(select.value), item.locked);
  });
  return select;
}

// Строка настроек поведения: параметры kind'а, locked, «Установить»/
// «Сбросить к дефолту»/«Закрыть» (слотом 0 — form-actions)
function buildBehaviorEditor(address) {
  const row = document.createElement("tr");
  row.className = "bh-edit";
  const cell = document.createElement("td");
  cell.colSpan = 7;
  const item = behaviorAt(address);
  const kind = item ? item.kind : "";
  const paramsBox = document.createElement("div");
  paramsBox.className = "bh-params";
  renderFormParams(paramsBox, kind);
  if (item) fillParams(paramsBox, item.params);

  const lockLabel = document.createElement("label");
  lockLabel.className = "check";
  const lock = document.createElement("input");
  lock.type = "checkbox";
  lock.checked = Boolean(item && item.locked);
  lockLabel.appendChild(lock);
  lockLabel.appendChild(
    document.createTextNode(" " + window.t("Заблокировать (locked: внешние записи игнорируются)"))
  );

  const apply = document.createElement("button");
  apply.type = "button";
  apply.textContent = window.t("Установить");
  apply.addEventListener("click", function () {
    const current = behaviorAt(address);
    const selected = el("reg-bh-select-" + address);
    const kindNow = selected ? selected.value : (current ? current.kind : "");
    const params = collectFormParams(paramsBox, kindNow, function (message) {
      showError(window.t("Ошибка: {text}", { text: message }));
    });
    if (params === null) return;
    putBehavior(address, kindNow, params, lock.checked);
  });

  const reset = document.createElement("button");
  reset.type = "button";
  reset.textContent = window.t("Сбросить к дефолту");
  reset.addEventListener("click", function () {
    resetBehavior(address);
  });

  const close = document.createElement("button");
  close.type = "button";
  close.textContent = window.t("Закрыть");
  close.addEventListener("click", function () {
    state.bhEdit = null;
    renderTable();
  });

  const actions = document.createElement("p");
  actions.className = "form-actions";
  actions.appendChild(lockLabel);
  actions.appendChild(apply);
  actions.appendChild(reset);
  actions.appendChild(close);
  cell.appendChild(paramsBox);
  cell.appendChild(actions);
  row.appendChild(cell);
  return row;
}

// ---------- тренд: история строки за 60 с ----------

function samplesOf(address) {
  return state.history.get(address) || [];
}

function pushHistory(address, value, now) {
  let samples = state.history.get(address);
  if (!samples) {
    samples = [];
    state.history.set(address, samples);
  }
  samples.push({ t: now, v: value });
  while (samples.length > 0 && samples[0].t < now - HISTORY_S * 1000) {
    samples.shift();
  }
}

// Откуда берётся слово регистра N: строка-продолжение слота (slave 10, IR 301)
// живёт в истории адреса-заголовка (300) младшим словом
function sampleSource(address) {
  if (
    slotOf(address).width === 16 &&
    slotOf(address - 1).width === 32 &&
    state.addresses.includes(address - 1)
  ) {
    return { address: address - 1, word: 1 };
  }
  return { address: address, word: 0 };
}

// Ряд [{t, v}] строки в текущем формате: значения преобразуются так же,
// как ячейка «Значение»; пара N, N+1 собирается по меткам времени кадра
function seriesFor(address, code) {
  const out = [];
  const push = function (t, words) {
    out.push({
      t: t,
      v: codeWidth(code) === 16
        ? (code === "int16" ? toSigned(words[0], 16) : words[0])
        : numberFromWords(words, code),
    });
  };
  if (state.area === "cl" || state.area === "di") {
    for (const sample of samplesOf(address)) out.push({ t: sample.t, v: sample.v ? 1 : 0 });
    return out;
  }
  const slot = slotOf(address);
  if (slot.width === 32) {
    // слот N, N+1 целиком лежит в одном логическом значении
    for (const sample of samplesOf(address)) push(sample.t, valueToWords(sample.v, slot.fmt));
    return out;
  }
  const source = sampleSource(address);
  const first = samplesOf(source.address).map(function (sample) {
    return {
      t: sample.t,
      v: valueToWords(sample.v, slotOf(source.address).fmt)[source.word],
    };
  });
  if (codeWidth(code) === 16) {
    for (const sample of first) push(sample.t, [sample.v]);
    return out;
  }
  const second = new Map();
  for (const sample of samplesOf(address + 1)) {
    second.set(sample.t, valueToWords(sample.v, slotOf(address + 1).fmt)[0]);
  }
  for (const sample of first) {
    if (second.has(sample.t)) push(sample.t, [sample.v, second.get(sample.t)]);
  }
  return out;
}

// Спарклайн строки: линия по истории, масштаб по min/max (как в minichart.js)
function drawSpark(canvas, address) {
  const now = Date.now();
  const from = now - HISTORY_S * 1000;
  const points = seriesFor(address, rowCode(address)).filter(function (point) {
    return point.t >= from && Number.isFinite(point.v);
  });
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth || 120;
  const wanted = Math.round(width * dpr);
  if (canvas.width !== wanted) {
    canvas.width = wanted;
    canvas.height = Math.round(TREND_H * dpr);
  }
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, TREND_H);
  if (points.length < 2) return; // точки только накапливаются
  let min = Infinity;
  let max = -Infinity;
  for (const point of points) {
    if (point.v < min) min = point.v;
    if (point.v > max) max = point.v;
  }
  if (min === max) {
    min -= 1;
    max += 1;
  }
  const pad = 2; // линия не прилипает к краям канвы
  ctx.strokeStyle = getComputedStyle(canvas).color;
  ctx.lineWidth = 1.25;
  ctx.beginPath();
  points.forEach(function (point, index) {
    const x = pad + ((point.t - from) / (HISTORY_S * 1000)) * (width - 2 * pad);
    const y = pad + (1 - (point.v - min) / (max - min)) * (TREND_H - 2 * pad);
    if (index === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
}

function drawTrends() {
  for (const address of visibleRows()) {
    const canvas = el("reg-trend-" + address);
    if (canvas) drawSpark(canvas, address);
  }
}

function editable() {
  return state.area === "cl" || state.area === "hr";
}

function showError(text) {
  el("reg-status").textContent = text;
}

// ---------- ввод значения в формате строки ----------

function rangeError(code, text) {
  return window.t("{type} вне диапазона: {text}", { type: FORMAT_LABELS[code], text: text });
}

// HEX-ввод: 0x… — шестнадцатеричное слово; без префикса — десятичное число
function parseHex(text, width) {
  const parts = text.split(/[\s,]+/).filter(function (part) { return part !== ""; });
  const words = [];
  for (const part of parts) {
    let value;
    if (/^0x[0-9a-f]+$/i.test(part)) value = parseInt(part, 16);
    else if (/^[0-9]+$/.test(part)) value = Number(part);
    else return { error: window.t("Не число: {text}", { text: part }) };
    if (!Number.isFinite(value) || value < 0 || value > 0xffffffff) {
      return { error: window.t("Не число: {text}", { text: part }) };
    }
    words.push(value);
  }
  if (width === 16) {
    if (words.length !== 1 || words[0] > 0xffff) {
      return { error: window.t("HEX16 — это одно слово 0x0000..0xFFFF") };
    }
    return { words: [words[0]] };
  }
  if (words.length === 2 && words[0] <= 0xffff && words[1] <= 0xffff) {
    return { words: [words[0], words[1]] };
  }
  if (words.length === 1 && words[0] <= 0xffffffff) {
    return { words: [(words[0] >>> 16) & 0xffff, words[0] & 0xffff] };
  }
  return { error: window.t("HEX32 — это два слова: 0x1234 0x5678 или 0x12345678") };
}

// Ввод значения в формате строки → слова регистров (16 бит — слово, 32 — пара)
function parseWords(raw, code) {
  const text = raw.trim();
  if (code === "hex16") return parseHex(text, 16);
  if (code === "hex32") return parseHex(text, 32);
  const value = Number(text);
  if (text === "" || !Number.isFinite(value)) {
    return { error: window.t("Не число: {text}", { text: text }) };
  }
  if (code.startsWith("float32")) {
    if (!Number.isFinite(bitsToFloat(floatToBits(value)))) {
      return { error: window.t("Значение вне диапазона float32: {text}", { text: text }) };
    }
    return { words: bitsToWords(floatToBits(value), orderOf(code)) };
  }
  if (!Number.isInteger(value)) {
    return { error: window.t("Нужно целое число: {text}", { text: text }) };
  }
  if (code === "uint16") {
    if (value < 0 || value > 0xffff) return { error: rangeError(code, text) };
    return { words: [value] };
  }
  if (code === "int16") {
    if (value < -0x8000 || value > 0x7fff) return { error: rangeError(code, text) };
    return { words: [value & 0xffff] };
  }
  if (code.startsWith("int32")) {
    if (value < -0x80000000 || value > 0x7fffffff) return { error: rangeError(code, text) };
    return { words: bitsToWords(value >>> 0, orderOf(code)) };
  }
  if (value < 0 || value > 0xffffffff) return { error: rangeError(code, text) };
  return { words: bitsToWords(value >>> 0, orderOf(code)) };
}

async function saveValue(address, raw) {
  // Ввод трактуется в формате строки; на сервер уходит логическое значение
  // формата слейва. Для пары N, N+1 — две записи (по слову на регистр).
  let updates;
  if (state.area === "cl") {
    const trimmed = raw.trim();
    updates = [[address, trimmed === "1" || trimmed.toLowerCase() === "true"]];
  } else {
    const code = rowCode(address);
    const parsed = parseWords(raw, code);
    if (parsed.error) {
      showError(parsed.error);
      renderTable();
      return;
    }
    const slot = slotOf(address);
    if (slot.width === 32) {
      const current = pairWords(address);
      if (current === null) {
        showError(window.t("Нет текущего значения слова — запись отменена"));
        renderTable();
        return;
      }
      // 16-битный формат меняет слово регистра N, 32-битный — оба слова
      const words = codeWidth(code) === 16 ? [parsed.words[0], current[1]] : parsed.words;
      const value = wordsToValue(words, slot.fmt);
      if (slot.fmt.startsWith("float") && !Number.isFinite(value)) {
        showError(window.t("Значение даёт NaN/Inf в формате слейва — запись отменена"));
        renderTable();
        return;
      }
      updates = [[address, value]];
    } else if (codeWidth(code) === 16) {
      updates = [[address, parsed.words[0]]];
    } else {
      updates = [[address, parsed.words[0]], [address + 1, parsed.words[1]]];
    }
  }
  try {
    for (const [target, value] of updates) {
      const response = await fetch("/api/modbus/write", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          slave: state.slave,
          area: state.area,
          address: target,
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
      state.values.set(target, body.value);
    }
    showError("");
  } catch (error) {
    showError(window.t("Ошибка записи: {error}", { error: error }));
  }
  renderTable();
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
    field.value = state.values.has(address) ? formatDisplay(address) : "";
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

// ---------- пагинация ----------

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

// Строки окна: адрес, поглощённый 32-битной строкой перед ним, не показывается
// — пара N, N+1 отображается одной строкой (slave 10: 300 — UINT32 timestamp,
// регистр 301 — его младшее слово). Первая строка окна показывается всегда.
function visibleRows() {
  const out = [];
  let covered = false;
  for (const address of pageAddresses()) {
    const coveredHere = covered && out.length > 0 && address === out[out.length - 1] + 1;
    if (coveredHere) {
      covered = false;
      continue;
    }
    out.push(address);
    covered = codeWidth(rowCode(address)) === 32 && pairAvailable(address);
  }
  return out;
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
  state.bhEdit = null; // другая страница — форма настроек закрывается
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

// ---------- рендер ----------

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

// Селект формата строки: 32-битные форматы недоступны без пары N, N+1
function buildFormatSelect(address) {
  const select = document.createElement("select");
  const spanOk = pairAvailable(address);
  for (const [label, items] of FORMAT_GROUPS) {
    const group = document.createElement("optgroup");
    group.label = label;
    for (const [code, text] of items) {
      const option = document.createElement("option");
      option.value = code;
      option.textContent = text;
      if (codeWidth(code) === 32 && !spanOk) option.disabled = true;
      group.appendChild(option);
    }
    select.appendChild(group);
  }
  select.value = rowCode(address);
  select.addEventListener("change", function () {
    setRowCode(address, select.value);
    renderTable();
  });
  return select;
}

function renderTable() {
  const body = el("reg-body");
  body.innerHTML = "";
  for (const address of visibleRows()) {
    const row = document.createElement("tr");

    const cellAddress = document.createElement("td");
    cellAddress.textContent = String(address);

    const cellTag = document.createElement("td");
    cellTag.className = "tag";
    cellTag.textContent = tagText(address);
    cellTag.title = cellTag.textContent;

    // Поведение строки: kind + «⚙» настроек; slave 10 — без поведений
    const cellBehavior = document.createElement("td");
    cellBehavior.className = "bh-cell";
    if (state.slave === 10) {
      const none = document.createElement("span");
      none.className = "reg-bits";
      none.textContent = "—";
      none.title = window.t("Slave 10: поведений нет - его IR вычисляются");
      cellBehavior.appendChild(none);
    } else {
      const wrap = document.createElement("div");
      wrap.className = "bh-cell-row";
      wrap.appendChild(buildBehaviorSelect(address));
      const gear = document.createElement("button");
      gear.type = "button";
      gear.textContent = "⚙";
      gear.title = window.t("Настроить поведение");
      gear.disabled = !behaviorAt(address); // конфиг ещё не загружен
      gear.addEventListener("click", function () {
        state.bhEdit = state.bhEdit === address ? null : address;
        renderTable();
      });
      wrap.appendChild(gear);
      cellBehavior.appendChild(wrap);
    }

    const cellFormat = document.createElement("td");
    if (state.area === "cl" || state.area === "di") {
      const bits = document.createElement("span");
      bits.className = "reg-bits";
      bits.textContent = "0/1";
      cellFormat.appendChild(bits);
    } else {
      cellFormat.appendChild(buildFormatSelect(address));
    }

    const cellValue = document.createElement("td");
    cellValue.id = "reg-cell-" + address;
    cellValue.textContent = formatDisplay(address);
    if (editable()) {
      cellValue.classList.add("editable");
      cellValue.title = window.t("Нажмите, чтобы изменить (CL/HR общие, last-write-wins)");
      cellValue.addEventListener("click", function () {
        if (cellValue.classList.contains("editing")) return; // клик по редактору не перезапускает правку
        startEdit(address);
      });
    }

    const cellTrend = document.createElement("td");
    cellTrend.className = "trend";
    const trend = document.createElement("canvas");
    trend.className = "reg-trend";
    trend.id = "reg-trend-" + address;
    trend.title = window.t("Тренд, 60 с");
    cellTrend.appendChild(trend);

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
    row.appendChild(cellTag);
    row.appendChild(cellBehavior);
    row.appendChild(cellFormat);
    row.appendChild(cellValue);
    row.appendChild(cellTrend);
    row.appendChild(cellStar);
    body.appendChild(row);
    if (state.bhEdit === address && state.slave !== 10) {
      body.appendChild(buildBehaviorEditor(address));
    }
  }
  drawTrends();
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

// ---------- WS / REST ----------

function applyFrame(frame) {
  const now = Date.now();
  const tracked = new Set(trackedAddresses());
  const values = (frame.areas && frame.areas[state.area]) || {};
  for (const [address, value] of Object.entries(values)) {
    const number = Number(address);
    state.values.set(number, value);
    if (tracked.has(number)) pushHistory(number, value, now);
  }
  // Строка без собственного ключа значения (301 — младшее слово слота 300)
  // и пара N, N+1 зависят от соседних регистров: перерисовываем всё окно,
  // редактируемые ячейки не трогаем
  for (const address of visibleRows()) {
    const cell = el("reg-cell-" + address);
    if (cell && !cell.classList.contains("editing")) {
      cell.textContent = formatDisplay(address);
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

// Конфиг поведений для тегов: по слейву и области, целиком (дефолты включены).
// У slave 10 поведений нет — его теги считает slave10Tag.
async function loadBehaviors() {
  const slave = state.slave;
  const area = state.area;
  if (slave === 10) return;
  try {
    const response = await fetch(
      "/api/modbus/behaviors?slave=" + slave + "&area=" + area
    );
    if (!response.ok) return;
    const body = await response.json();
    if (slave !== state.slave || area !== state.area) return; // опоздавший ответ
    state.behaviors.clear();
    for (const item of body.items || []) {
      state.behaviors.set(item.address, item);
    }
    renderTable();
  } catch (error) {
    // API недоступен — теги остаются пустыми
  }
}

// Адреса видимого окна + следующий адрес: для 32-битной пары N, N+1 последней
// строки нужны оба регистра. Адрес берётся из сетки — чужой сервер отклоняет.
function trackedAddresses() {
  const out = pageAddresses().slice();
  const next = state.addresses[state.start + PAGE_SIZE];
  if (next !== undefined) out.push(next);
  return out;
}

function subscribe() {
  if (!state.ws || state.ws.readyState !== WebSocket.OPEN) return;
  state.ws.send(
    JSON.stringify({
      slave: state.slave,
      areas: [state.area],
      addresses: trackedAddresses(),
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
  state.history.clear(); // другой slave/область — своя история
  state.behaviors.clear();
  state.bhEdit = null; // форма настроек закрывается при смене контекста
  renderTable();
  loadBehaviors(); // теги строк догоняют после ответа API
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
  setInterval(drawTrends, 500); // спарклайны обновляются раз в 500 мс (как график избранного)
}

init();
