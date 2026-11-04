// Форма настройки поведений (TASK-721…723): slave/area/address → kind → params.
// Kind фильтруется по области (числовые — hr/ir, дискретные — cl/di), поля
// параметров рисуются по выбранному kind (спецификации — PARAM_SPECS, дефолты
// совпадают с generators.py). Установка одной привязки — PUT
// /api/modbus/behaviors/{slave}/{area}/{address}; диапазон адресов
// («Применить к диапазону» / «Сбросить») — POST /api/modbus/behaviors/batch;
// список переопределений — GET /api/modbus/behaviors?only_overrides=true.
// Схема kind/params — общая с картой регистров (behavior-form.js, TASK-716).

import {
  collectParams as collectFormParams,
  DTYPE_WIDTH,
  dtypesForFmt,
  fillParams,
  kindLabel,
  kindsForArea,
  renderParams as renderFormParams,
} from "./behavior-form.js";

function el(id) {
  return document.getElementById(id);
}

// Пресеты (TASK-722): быстрая настройка формы — kind + params одним нажатием.
// Работают для hr/ir (числовые поведения); «reset» — константа 0.
const PRESETS = {
  counter: { kind: "counter", params: { start: 0, step: 1, min: 0, max: 65535, wrap: true, interval_s: 1 } },
  sawtooth: { kind: "sawtooth", params: { min: 0, max: 100, period_s: 10, phase: 0 } },
  triangle: { kind: "triangle", params: { min: 0, max: 100, period_s: 10, phase: 0 } },
  meander: { kind: "meander", params: { min: 0, max: 100, period_s: 10, duty: 0.5, phase: 0 } },
  noise: { kind: "random_uniform", params: { min: 0, max: 100 } },
  sine: { kind: "sine", params: { min: 0, max: 100, period_s: 10, phase: 0 } },
  step: { kind: "step", params: { values: "0, 1", interval_s: 1 } },
  reset: { kind: "constant", params: { value: 0 } },
};

// Заполнить форму пресетом: kind → renderParams() → выставить значения полей.
function applyPreset(name) {
  const preset = PRESETS[name];
  if (!preset) return;
  const area = el("bh-area").value;
  if (area !== "hr" && area !== "ir") {
    showError(window.t("пресеты работают для областей hr/ir (для cl/di выберите поведение вручную)"));
    return;
  }
  el("bh-kind").value = preset.kind;
  renderParams();
  fillParams(el("bh-params"), preset.params);
  el("bh-status").textContent = window.t("Пресет «{name}» загружен в форму - нажмите «Установить».", { name: name });
}

function renderKinds() {
  const select = el("bh-kind");
  const previous = select.value;
  select.textContent = "";
  for (const kind of kindsForArea(el("bh-area").value)) {
    const option = document.createElement("option");
    option.value = kind;
    option.textContent = kind + " - " + kindLabel(kind);
    select.appendChild(option);
  }
  if (kindsForArea(el("bh-area").value).includes(previous)) select.value = previous;
  renderParams();
  renderDtypes();
}

// Селект типа значения (TASK-717): типы по сетке слейва (16-битная — все,
// 32-битная — только 32-битные); у cl/di типа нет — селект пуст и заблокирован
function renderDtypes() {
  const select = el("bh-dtype");
  const previous = select.value;
  const area = el("bh-area").value;
  select.textContent = "";
  if (area !== "hr" && area !== "ir") {
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "—";
    select.appendChild(none);
    select.value = "";
    select.disabled = true;
    return;
  }
  select.disabled = false;
  const spec = window.REGISTER_MAP[String(el("bh-slave").value)];
  for (const dtype of dtypesForFmt(spec ? spec.fmt : "uint16")) {
    const option = document.createElement("option");
    option.value = dtype;
    option.textContent = dtype;
    select.appendChild(option);
  }
  if ([].some.call(select.options, function (option) { return option.value === previous; })) {
    select.value = previous;
  }
}

// Выбранный тип формы: null для cl/di (сервер принимает dtype только у hr/ir)
function selectedDtype() {
  return el("bh-dtype").value || null;
}

// Ширина выбранного типа в регистрах: 32-битный — пара N, N+1
function selectedWidth() {
  return DTYPE_WIDTH[selectedDtype()] || 1;
}

// Поля параметров выбранного kind'а в контейнер #bh-params (общая схема —
// behavior-form.js)
function renderParams() {
  renderFormParams(el("bh-params"), el("bh-kind").value);
}

// Собрать params из формы (типизация и optional-поля — в behavior-form.js)
function collectParams() {
  return collectFormParams(el("bh-params"), el("bh-kind").value, showError);
}

function showError(text) {
  el("bh-status").textContent = window.t("Ошибка: {text}", { text: text });
}

async function apply() {
  const status = el("bh-status");
  const params = collectParams();
  if (params === null) return;
  const slave = Number(el("bh-slave").value);
  const area = el("bh-area").value;
  const address = Number(el("bh-address").value);
  const kind = el("bh-kind").value;
  const locked = el("bh-locked").checked;
  status.textContent = window.t("Установка…");
  try {
    const response = await fetch(
      "/api/modbus/behaviors/" + slave + "/" + area + "/" + address,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ kind: kind, params: params, locked: locked, dtype: selectedDtype() }),
      }
    );
    const body = await response.json();
    if (!response.ok) {
      status.textContent = window.t("Ошибка {status}: {detail}", { status: response.status, detail: body.detail || "" });
      return;
    }
    status.textContent = window.t(
      "Установлено: slave {slave} {area} {address}, поведение {kind}.",
      { slave: body.slave, area: body.area, address: body.address, kind: body.kind }
    );
    refreshList();
  } catch (error) {
    status.textContent = window.t("Ошибка: {text}", { text: error });
  }
}

// Адреса сетки значений области в диапазоне from..to (TASK-723, 717): у
// 32-битных форматов значения идут с шагом 2 (1, 3, …, 1023) — берём только
// существующие. Ширина выбранного типа задаёт геометрию: 32-битный тип на
// slave 1 занимает пару N, N+1, поэтому в диапазон идут только головы пар
// (младшие регистры покрываются предыдущей головой).
function rangeAddresses(area, from, to) {
  const spec = window.REGISTER_MAP[String(el("bh-slave").value)];
  const ranges = (spec && spec.areas[area]) || [];
  const width = selectedWidth();
  const addresses = [];
  let coveredTo = 0; // последний регистр, занятый предыдущей головой пары
  for (const [start, stop, step] of ranges) {
    for (let address = start; address <= stop; address += step) {
      if (address < from || address > to) continue;
      if (address <= coveredTo) continue; // младший регистр пары уже занят
      if (address + width - 1 > stop + (step - 1)) continue; // пара не влезает в карту
      addresses.push(address);
      coveredTo = address + width - 1;
    }
  }
  return addresses;
}

function readRange() {
  const from = Number(el("bh-address").value);
  const to = Number(el("bh-address-to").value);
  if (!Number.isInteger(from) || !Number.isInteger(to) || from < 1 || to < 1) {
    showError(window.t("адреса диапазона - целые числа от 1 и больше"));
    return null;
  }
  if (to < from) {
    showError(window.t("«Адрес по» меньше «Адрес от»"));
    return null;
  }
  const area = el("bh-area").value;
  const addresses = rangeAddresses(area, from, to);
  if (addresses.length === 0) {
    showError(window.t("в диапазоне от {from} до {to} нет адресов области {area}", { from: from, to: to, area: area }));
    return null;
  }
  return { area: area, addresses: addresses };
}

// «Применить к диапазону» (TASK-723): одна форма → batch на все адреса
// диапазона (POST /api/modbus/behaviors/batch, атомарно).
async function applyRange() {
  const status = el("bh-status");
  const params = collectParams();
  if (params === null) return;
  const range = readRange();
  if (range === null) return;
  const slave = Number(el("bh-slave").value);
  const kind = el("bh-kind").value;
  const locked = el("bh-locked").checked;
  const items = range.addresses.map(function (address) {
    return {
      slave: slave,
      area: range.area,
      address: address,
      kind: kind,
      params: params,
      locked: locked,
      dtype: selectedDtype(),
    };
  });
  status.textContent = window.t("Применение к {count} адресам…", { count: items.length });
  try {
    const response = await fetch("/api/modbus/behaviors/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: items }),
    });
    const body = await response.json();
    if (!response.ok) {
      status.textContent = window.t("Ошибка {status}: {detail}", { status: response.status, detail: body.detail || "" });
      return;
    }
    status.textContent = window.t(
      "Применено к {count} адресам ({kind}, slave {slave} {area}).",
      { count: body.applied, kind: kind, slave: slave, area: range.area }
    );
    refreshList();
  } catch (error) {
    status.textContent = window.t("Ошибка: {text}", { text: error });
  }
}

// «Сбросить» (TASK-723): диапазон возвращается к поведениям по умолчанию
// (batch с remove=true — атомарно).
async function resetRange() {
  const status = el("bh-status");
  const range = readRange();
  if (range === null) return;
  const slave = Number(el("bh-slave").value);
  const items = range.addresses.map(function (address) {
    return { slave: slave, area: range.area, address: address, remove: true };
  });
  status.textContent = window.t("Сброс {count} адресов…", { count: items.length });
  try {
    const response = await fetch("/api/modbus/behaviors/batch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items: items }),
    });
    const body = await response.json();
    if (!response.ok) {
      status.textContent = window.t("Ошибка {status}: {detail}", { status: response.status, detail: body.detail || "" });
      return;
    }
    status.textContent = window.t(
      "Сброшено к дефолтам: {count} (slave {slave} {area}).",
      { count: body.removed, slave: slave, area: range.area }
    );
    refreshList();
  } catch (error) {
    status.textContent = window.t("Ошибка: {text}", { text: error });
  }
}

// Таблица переопределений: только привязки, отличающиеся от дефолтов.
async function refreshList() {
  const list = el("bh-list");
  try {
    const response = await fetch("/api/modbus/behaviors?only_overrides=true");
    if (!response.ok) return;
    const body = await response.json();
    list.textContent = "";
    if (body.items.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 7;
      cell.textContent = window.t("Переопределений нет - все привязки по умолчанию.");
      row.appendChild(cell);
      list.appendChild(row);
      return;
    }
    for (const item of body.items) {
      const row = document.createElement("tr");
      const values = [
        String(item.slave),
        item.area,
        String(item.address),
        item.kind,
        item.dtype || "—", // эффективный тип: у cl/di типа нет
        JSON.stringify(item.params),
        item.locked ? window.t("да") : window.t("нет"),
      ];
      for (const value of values) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      list.appendChild(row);
    }
  } catch (error) {
    /* сбой загрузки списка — следующий тик/«Обновить» повторит запрос */
  }
}

function init() {
  el("bh-area").addEventListener("change", renderKinds);
  el("bh-slave").addEventListener("change", renderDtypes); // сетка слейва меняет список типов
  el("bh-kind").addEventListener("change", renderParams);
  el("bh-apply").addEventListener("click", apply);
  el("bh-apply-range").addEventListener("click", applyRange);
  el("bh-reset-range").addEventListener("click", resetRange);
  el("bh-refresh").addEventListener("click", refreshList);
  for (const button of document.querySelectorAll("#bh-presets button[data-preset]")) {
    button.addEventListener("click", function () {
      applyPreset(this.dataset.preset);
    });
  }
  renderKinds();
  refreshList();
}

init();
