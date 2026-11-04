// Карта регистров (TASK-711, 712, 717): таблица адресов по слейву, live через WS.
// Геометрия карты — window.REGISTER_MAP (серверный рендер из SLAVE_SPECS).
// Пагинация — по 10 адресов: окно начинается с произвольного «Начального
// адреса», кнопки «Назад»/«Вперёд» сдвигают окно на страницу, поле «Страница» —
// быстрый переход по номеру. WS-подписка только на адреса видимого окна.
// Тип значения строки (колонка «Тип») задаётся в настройках поведения («⚙»):
// uint16/int16 — один регистр N, 32-битные uint32/int32/float32 — пара N, N+1
// (строка N+1 скрыта - младший регистр пары). Тип определяет и математику:
// float32 даёт дробные значения, целые — в диапазоне типа. Кадры несут
// логические значения по типу привязки, клиент их только форматирует
// (float32 — 7 значащих цифр).
// Колонка «Тег» — имя сигнала: поведение регистра (slave 1..9, конфиг из
// REST /api/modbus/behaviors) либо метрика времени (slave 10, HRIR_SPEC).
// Колонка «Поведение» (TASK-716) — выбор kind строкой и «⚙» с параметрами:
// смена kind сразу ставит поведение (PUT /api/modbus/behaviors/...), «⚙»
// открывает строку настроек (тип, params, locked, «Сбросить к дефолту» — batch
// с remove:true). Поведения общие для всех пользователей; slave 10 — без них.
// Колонка «Тренд, 60 с» — спарклайн строки: кольцевой буфер значений за 60 с,
// перерисовка раз в 500 мс.

import {
  collectParams as collectFormParams,
  defaultParams,
  DTYPE_WIDTH,
  dtypesForFmt,
  fillParams,
  kindLabel,
  kindsForArea,
  renderParams as renderFormParams,
} from "./behavior-form.js";

const PAGE_SIZE = 10;
const RECONNECT_MAX_MS = 15000;
const HISTORY_S = 60;
const TREND_H = 28;

const map = window.REGISTER_MAP;

const state = {
  slave: 1,
  area: null,
  addresses: [],
  values: new Map(),
  history: new Map(), // адрес → [{t, v}] значения за HISTORY_S
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

// ---------- тип значения строки (TASK-717) ----------

function behaviorAt(address) {
  return state.behaviors.get(address) || null;
}

// Эффективный тип строки: тип задаётся в настройках поведения («⚙») и ведёт
// математику с геометрией. cl/di — биты (типа нет), slave 10 — uint16,
// метрики; IR 300 — uint32 timestamp (HRIR_SPEC.md).
function rowType(address) {
  if (state.area === "cl" || state.area === "di") return null;
  if (state.slave === 10) return address === 300 ? "uint32" : "uint16";
  const item = behaviorAt(address);
  return item && item.dtype ? item.dtype : null;
}

// Колонка «Тип»: биты — 0/1, hr/ir — имя типа («…» пока конфиг не загружен)
function typeLabel(address) {
  if (state.area === "cl" || state.area === "di") return "0/1";
  return rowType(address) || "…";
}

// Ширина типа в регистрах: 32-битные типы занимают пару N, N+1
function typeWidth(address) {
  const dtype = rowType(address);
  return dtype ? DTYPE_WIDTH[dtype] : 1;
}

// Пара N, N+1 доступна, если адрес N+1 есть в карте (slave 1 и slave 10)
function pairAvailable(address) {
  return state.addresses.includes(address + 1);
}

// Строка с 32-битным типом объединяет пару регистров N, N+1 (null — не пара):
// строка N+1 скрыта, её значением распоряжается поведение адреса N
function pairOf(address) {
  if (typeWidth(address) !== 2 || !pairAvailable(address)) return null;
  return { a: address, b: address + 1 };
}

// Подсказка о паре для тултипа значения
function pairHint(address) {
  const pair = pairOf(address);
  if (!pair) return "";
  return window.t("Пара регистров {a} и {b}: одно значение типа {type}, строка {b} скрыта", {
    a: pair.a,
    b: pair.b,
    type: rowType(address),
  });
}

// Подсказки ячейки «Значение»: пара регистров и условие правки
function valueTitle(address) {
  const hints = [];
  const pair = pairHint(address);
  if (pair) hints.push(pair);
  if (editable()) {
    hints.push(window.t("Нажмите, чтобы изменить (CL/HR общие, last-write-wins)"));
  }
  return hints.join(" · ");
}

// ---------- отображение значения по типу строки ----------

function formatDisplay(address) {
  if (state.area === "cl" || state.area === "di") {
    return state.values.has(address) ? (state.values.get(address) ? "1" : "0") : "—";
  }
  if (!state.values.has(address)) return "—";
  const value = Number(state.values.get(address));
  if (!Number.isFinite(value)) return "—";
  if (rowType(address) === "float32") {
    return String(Number(value.toPrecision(7))); // 841.3699951171875 → 841.37
  }
  return String(Math.round(value));
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

// PUT привязки поведения: kind + params + locked + dtype; ответ обновляет кэш
// строки. Поведения общие (last-write-wins) — настройка действует для всех.
async function putBehavior(address, kind, params, locked, dtype) {
  showError(window.t("Установка…"));
  try {
    const response = await fetch(
      "/api/modbus/behaviors/" + state.slave + "/" + state.area + "/" + address,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: kind, params: params, locked: locked, dtype: dtype || null }),
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
// ставит поведение с параметрами по умолчанию (тип строки сохраняется)
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
    putBehavior(address, select.value, defaultParams(select.value), item.locked, item.dtype);
  });
  return select;
}

// Строка настроек поведения: тип значения (hr/ir), параметры kind'а, locked,
// «Установить»/«Сбросить к дефолту»/«Закрыть» (слотом 0 — form-actions)
function buildBehaviorEditor(address) {
  const row = document.createElement("tr");
  row.className = "bh-edit";
  const cell = document.createElement("td");
  cell.colSpan = 7;
  const item = behaviorAt(address);
  const kind = item ? item.kind : "";

  // Тип значения (TASK-717): 16-битная сетка — все типы, 32-битная — только
  // 32-битные (слот слейва уже пара регистров)
  if (state.area === "hr" || state.area === "ir") {
    const typeBox = document.createElement("label");
    typeBox.className = "bh-param";
    typeBox.textContent = window.t("Тип") + " ";
    const typeSelect = document.createElement("select");
    typeSelect.id = "reg-bh-dtype-" + address;
    for (const dtype of dtypesForFmt(spec().fmt)) {
      const option = document.createElement("option");
      option.value = dtype;
      option.textContent = dtype;
      typeSelect.appendChild(option);
    }
    if (item && item.dtype) typeSelect.value = item.dtype;
    typeBox.appendChild(typeSelect);
    cell.appendChild(typeBox);
  }

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
    const typeSelect = el("reg-bh-dtype-" + address);
    putBehavior(address, kindNow, params, lock.checked, typeSelect ? typeSelect.value : null);
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
  // На slave 1 32-битный тип — пара регистров N, N+1: поведение настраивает
  // только N, а N+1 скрыт (его строка появляется при 16-битном типе)
  const pair = pairOf(address);
  if (pair) {
    const hint = document.createElement("p");
    hint.className = "note";
    hint.textContent = window.t(
      "Тип {type} занимает регистры {a} и {b}; поведение настраивает регистр {a}, регистр {b} скрыт - младший регистр пары.",
      { type: rowType(address), a: pair.a, b: pair.b }
    );
    cell.appendChild(hint);
  }
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

// Ряд [{t, v}] строки: логические значения истории, как в ячейке «Значение»
function seriesFor(address) {
  const out = [];
  const bitArea = state.area === "cl" || state.area === "di";
  for (const sample of samplesOf(address)) {
    const value = bitArea ? (sample.v ? 1 : 0) : Number(sample.v);
    out.push({ t: sample.t, v: value });
  }
  return out;
}

// Спарклайн строки: линия по истории, масштаб по min/max (как в minichart.js)
function drawSpark(canvas, address) {
  const now = Date.now();
  const from = now - HISTORY_S * 1000;
  const points = seriesFor(address).filter(function (point) {
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

// ---------- ввод значения по типу строки ----------

// Ввод строки → логическое значение типа: 0x… — шестнадцатеричное (только
// целые типы), float32 принимает дробные; диапазон сверяется по типу
function parseValue(raw, dtype) {
  const text = raw.trim();
  const value = Number(text);
  if (text === "" || !Number.isFinite(value)) {
    return { error: window.t("Не число: {text}", { text: text }) };
  }
  if (dtype === "float32") return { value: value };
  if (!Number.isInteger(value)) {
    return { error: window.t("Нужно целое число: {text}", { text: text }) };
  }
  const ranges = {
    uint16: [0, 0xffff],
    int16: [-0x8000, 0x7fff],
    uint32: [0, 0xffffffff],
    int32: [-0x80000000, 0x7fffffff],
  };
  const range = ranges[dtype];
  if (range && (value < range[0] || value > range[1])) {
    return { error: window.t("{type} вне диапазона: {text}", { type: dtype, text: text }) };
  }
  return { value: value };
}

async function saveValue(address, raw) {
  // Ввод трактуется по типу строки; на сервер уходит логическое значение
  let value;
  if (state.area === "cl") {
    const trimmed = raw.trim();
    value = trimmed === "1" || trimmed.toLowerCase() === "true";
  } else {
    const parsed = parseValue(raw, rowType(address));
    if (parsed.error) {
      showError(parsed.error);
      renderTable();
      return;
    }
    value = parsed.value;
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
    state.values.set(address, body.value);
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
    covered = typeWidth(address) === 2 && pairAvailable(address);
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

    // Тип значения строки (TASK-717): только отображение, выбирается в «⚙»
    const cellType = document.createElement("td");
    const typeSpan = document.createElement("span");
    typeSpan.className = "reg-bits";
    typeSpan.textContent = typeLabel(address);
    cellType.appendChild(typeSpan);

    const cellValue = document.createElement("td");
    cellValue.id = "reg-cell-" + address;
    cellValue.textContent = formatDisplay(address);
    cellValue.title = valueTitle(address); // пара N, N+1 + условие правки
    if (editable()) {
      cellValue.classList.add("editable");
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
    row.appendChild(cellType);
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
  // Пара N, N+1 и строки slave 10 зависят от соседних регистров: перерисовываем
  // всё окно, редактируемые ячейки не трогаем
  for (const address of visibleRows()) {
    const cell = el("reg-cell-" + address);
    if (cell && !cell.classList.contains("editing")) {
      cell.textContent = formatDisplay(address);
      cell.title = valueTitle(address); // подсказки пары меняются с кадрами
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

// Конфиг поведений тегов и типов: по слейву и области, целиком (дефолты
// включены). У slave 10 поведений нет — его теги считает slave10Tag.
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
    // API недоступен — теги и типы остаются пустыми
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
  loadBehaviors(); // теги и типы строк догоняют после ответа API
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
