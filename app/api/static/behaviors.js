// Форма настройки поведений (TASK-721…723): slave/area/address → kind → params.
// Kind фильтруется по области (числовые — hr/ir, дискретные — cl/di), поля
// параметров рисуются по выбранному kind (спецификации — PARAM_SPECS, дефолты
// совпадают с generators.py). Установка одной привязки — PUT
// /api/modbus/behaviors/{slave}/{area}/{address}; диапазон адресов
// («Применить к диапазону» / «Сбросить») — POST /api/modbus/behaviors/batch;
// список переопределений — GET /api/modbus/behaviors?only_overrides=true.

const NUMERIC_KINDS = [
  "constant",
  "counter",
  "sawtooth",
  "triangle",
  "meander",
  "sine",
  "random_uniform",
  "random_walk",
  "ramp_oneshot",
  "countdown",
  "step",
  "follow",
];

const DISCRETE_KINDS = ["constant", "toggle", "pwm", "random_bit", "counter_bits", "follow_bit"];

const KIND_LABELS = {
  constant: window.t("константа"),
  counter: window.t("счётчик"),
  sawtooth: window.t("пила"),
  triangle: window.t("треугольник"),
  meander: window.t("меандр"),
  sine: window.t("синус"),
  random_uniform: window.t("равномерный шум"),
  random_walk: window.t("блуждание"),
  ramp_oneshot: window.t("переход (ramp)"),
  countdown: window.t("обратный отсчёт"),
  step: window.t("ступеньки"),
  follow: window.t("копия регистра"),
  toggle: window.t("переключение"),
  pwm: window.t("ШИМ (скважность)"),
  random_bit: window.t("случайный бит"),
  counter_bits: window.t("биты счётчика секунд"),
  follow_bit: window.t("копия бита"),
};

// Поля параметров kind'ов: name — имя параметра API, type — number/bool/text,
// def — значение по умолчанию (как в generators.py), optional — пустое поле
// не отправляется (сервер берёт дефолт; нужно для counter_bits: bit = address−1).
const PARAM_SPECS = {
  constant: [{ name: "value", label: window.t("Значение"), type: "number", def: 0 }],
  counter: [
    { name: "start", label: window.t("Старт"), type: "number", def: 0 },
    { name: "step", label: window.t("Шаг"), type: "number", def: 1 },
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 4294967295 },
    { name: "wrap", label: window.t("Цикл (wrap)"), type: "bool", def: true },
    { name: "interval_s", label: window.t("Период шага, с"), type: "number", def: 1 },
  ],
  sawtooth: [
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 100 },
    { name: "period_s", label: window.t("Период, с"), type: "number", def: 10 },
    { name: "phase", label: window.t("Фаза (доля периода)"), type: "number", def: 0 },
  ],
  triangle: [
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 100 },
    { name: "period_s", label: window.t("Период, с"), type: "number", def: 10 },
    { name: "phase", label: window.t("Фаза (доля периода)"), type: "number", def: 0 },
  ],
  meander: [
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 100 },
    { name: "period_s", label: window.t("Период, с"), type: "number", def: 10 },
    { name: "duty", label: window.t("Скважность (от 0 до 1)"), type: "number", def: 0.5 },
    { name: "phase", label: window.t("Фаза (доля периода)"), type: "number", def: 0 },
  ],
  sine: [
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 100 },
    { name: "period_s", label: window.t("Период, с"), type: "number", def: 10 },
    { name: "phase", label: window.t("Фаза (доля периода)"), type: "number", def: 0 },
  ],
  random_uniform: [
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 100 },
  ],
  random_walk: [
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "max", label: window.t("Максимум"), type: "number", def: 100 },
    { name: "step", label: window.t("Шаг"), type: "number", def: 1 },
    { name: "mean_reversion", label: window.t("Возврат к среднему (от 0 до 1)"), type: "number", def: 0 },
  ],
  ramp_oneshot: [
    { name: "from", label: window.t("От"), type: "number", def: 0 },
    { name: "to", label: window.t("До"), type: "number", def: 100 },
    { name: "duration_s", label: window.t("Длительность, с"), type: "number", def: 10 },
    {
      name: "then",
      label: window.t("Дальше (hold, loop или число)"),
      type: "text",
      def: "hold",
    },
  ],
  countdown: [
    { name: "start", label: window.t("Старт"), type: "number", def: 100 },
    { name: "step", label: window.t("Шаг"), type: "number", def: 1 },
    { name: "min", label: window.t("Минимум"), type: "number", def: 0 },
    { name: "interval_s", label: window.t("Период шага, с"), type: "number", def: 1 },
  ],
  step: [
    { name: "values", label: window.t("Значения через запятую"), type: "text", def: "0, 1" },
    { name: "interval_s", label: window.t("Период шага, с"), type: "number", def: 1 },
  ],
  follow: [{ name: "source", label: window.t("Источник (вида «hr:15»)"), type: "text", def: "hr:15" }],
  toggle: [{ name: "period_s", label: window.t("Период, с"), type: "number", def: 1 }],
  pwm: [
    { name: "period_s", label: window.t("Период, с"), type: "number", def: 1 },
    { name: "duty", label: window.t("Скважность (от 0 до 1)"), type: "number", def: 0.5 },
  ],
  random_bit: [{ name: "p", label: window.t("Вероятность 1 (от 0 до 1)"), type: "number", def: 0.5 }],
  counter_bits: [
    {
      name: "bit",
      label: window.t("Номер бита (пусто - на единицу меньше адреса)"),
      type: "number",
      def: null,
      optional: true,
    },
  ],
  follow_bit: [
    { name: "source", label: window.t("Источник (вида «hr:15»)"), type: "text", def: "hr:15" },
    { name: "bit", label: window.t("Номер бита"), type: "number", def: 0 },
  ],
};

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
  for (const [key, value] of Object.entries(preset.params)) {
    const input = el("bh-param-" + key);
    if (!input) continue;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else input.value = String(value);
  }
  el("bh-status").textContent = window.t("Пресет «{name}» загружен в форму - нажмите «Установить».", { name: name });
}

function kindsForArea(area) {
  return area === "hr" || area === "ir" ? NUMERIC_KINDS : DISCRETE_KINDS;
}

function renderKinds() {
  const select = el("bh-kind");
  const previous = select.value;
  select.textContent = "";
  for (const kind of kindsForArea(el("bh-area").value)) {
    const option = document.createElement("option");
    option.value = kind;
    option.textContent = kind + " - " + KIND_LABELS[kind];
    select.appendChild(option);
  }
  if (kindsForArea(el("bh-area").value).includes(previous)) select.value = previous;
  renderParams();
}

function renderParams() {
  const container = el("bh-params");
  container.textContent = "";
  const specs = PARAM_SPECS[el("bh-kind").value] || [];
  for (const spec of specs) {
    const label = document.createElement("label");
    label.className = "bh-param";
    label.textContent = spec.label + " ";
    const input = document.createElement("input");
    input.id = "bh-param-" + spec.name;
    if (spec.type === "bool") {
      input.type = "checkbox";
      input.checked = Boolean(spec.def);
    } else if (spec.type === "number") {
      input.type = "number";
      input.step = "any";
      if (spec.def !== null && spec.def !== undefined) input.value = String(spec.def);
    } else {
      input.type = "text";
      if (spec.def !== null && spec.def !== undefined) input.value = String(spec.def);
    }
    label.appendChild(input);
    container.appendChild(label);
  }
}

// Собрать params из формы: bool → checkbox, number → Number, text → строка
// (step.values — список чисел из строки «0, 1»); пустое optional-поле не
// отправляется — сервер возьмёт дефолт.
function collectParams() {
  const params = {};
  const specs = PARAM_SPECS[el("bh-kind").value] || [];
  for (const spec of specs) {
    const input = el("bh-param-" + spec.name);
    if (!input) continue;
    if (spec.type === "bool") {
      params[spec.name] = input.checked;
      continue;
    }
    const raw = input.value.trim();
    if (raw === "") {
      if (spec.optional) continue;
      showError(window.t("заполните параметр «{label}»", { label: spec.label }));
      return null;
    }
    if (spec.type === "number") {
      const value = Number(raw);
      if (Number.isNaN(value)) {
        showError(window.t("параметр «{label}» должен быть числом", { label: spec.label }));
        return null;
      }
      params[spec.name] = value;
      continue;
    }
    if (spec.name === "values") {
      const parts = raw.split(",").map((part) => part.trim()).filter(Boolean);
      const values = [];
      for (const part of parts) {
        const value = Number(part);
        if (Number.isNaN(value)) {
          showError(window.t("параметр «{label}»: «{part}» - не число", { label: spec.label, part: part }));
          return null;
        }
        values.push(value);
      }
      if (values.length === 0) {
        showError(window.t("параметр «{label}»: нужен хотя бы один элемент", { label: spec.label }));
        return null;
      }
      params[spec.name] = values;
      continue;
    }
    if (spec.name === "then") {
      // "hold" / "loop" — строки, иначе число (RampOneshotGenerator).
      if (raw === "hold" || raw === "loop") {
        params[spec.name] = raw;
        continue;
      }
      const value = Number(raw);
      if (Number.isNaN(value)) {
        showError(window.t("параметр «{label}»: hold, loop или число", { label: spec.label }));
        return null;
      }
      params[spec.name] = value;
      continue;
    }
    params[spec.name] = raw;
  }
  return params;
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
        body: JSON.stringify({ kind: kind, params: params, locked: locked }),
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

// Адреса сетки значений области в диапазоне from..to (TASK-723): у 32-битных
// форматов значения идут с шагом 2 (1, 3, …, 1023) — берём только существующие.
function rangeAddresses(area, from, to) {
  const spec = window.REGISTER_MAP[String(el("bh-slave").value)];
  const ranges = (spec && spec.areas[area]) || [];
  const addresses = [];
  for (const [start, stop, step] of ranges) {
    for (let address = start; address <= stop; address += step) {
      if (address >= from && address <= to) addresses.push(address);
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
      cell.colSpan = 6;
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
