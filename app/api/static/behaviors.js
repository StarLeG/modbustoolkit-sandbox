// Форма настройки поведений (TASK-721): slave/area/address → kind → params.
// Kind фильтруется по области (числовые — hr/ir, дискретные — cl/di), поля
// параметров рисуются по выбранному kind (спецификации — PARAM_SPECS, дефолты
// совпадают с generators.py). Установка — PUT /api/modbus/behaviors/{slave}/{area}/{address},
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
  constant: "константа",
  counter: "счётчик",
  sawtooth: "пила",
  triangle: "треугольник",
  meander: "меандр",
  sine: "синус",
  random_uniform: "равномерный шум",
  random_walk: "блуждание",
  ramp_oneshot: "переход (ramp)",
  countdown: "обратный отсчёт",
  step: "ступеньки",
  follow: "копия регистра",
  toggle: "переключение",
  pwm: "ШИМ (скважность)",
  random_bit: "случайный бит",
  counter_bits: "биты счётчика секунд",
  follow_bit: "копия бита",
};

// Поля параметров kind'ов: name — имя параметра API, type — number/bool/text,
// def — значение по умолчанию (как в generators.py), optional — пустое поле
// не отправляется (сервер берёт дефолт; нужно для counter_bits: bit = address−1).
const PARAM_SPECS = {
  constant: [{ name: "value", label: "Значение", type: "number", def: 0 }],
  counter: [
    { name: "start", label: "Старт", type: "number", def: 0 },
    { name: "step", label: "Шаг", type: "number", def: 1 },
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 4294967295 },
    { name: "wrap", label: "Цикл (wrap)", type: "bool", def: true },
    { name: "interval_s", label: "Период шага, с", type: "number", def: 1 },
  ],
  sawtooth: [
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 100 },
    { name: "period_s", label: "Период, с", type: "number", def: 10 },
    { name: "phase", label: "Фаза (доля периода)", type: "number", def: 0 },
  ],
  triangle: [
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 100 },
    { name: "period_s", label: "Период, с", type: "number", def: 10 },
    { name: "phase", label: "Фаза (доля периода)", type: "number", def: 0 },
  ],
  meander: [
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 100 },
    { name: "period_s", label: "Период, с", type: "number", def: 10 },
    { name: "duty", label: "Скважность (0..1)", type: "number", def: 0.5 },
    { name: "phase", label: "Фаза (доля периода)", type: "number", def: 0 },
  ],
  sine: [
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 100 },
    { name: "period_s", label: "Период, с", type: "number", def: 10 },
    { name: "phase", label: "Фаза (доля периода)", type: "number", def: 0 },
  ],
  random_uniform: [
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 100 },
  ],
  random_walk: [
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "max", label: "Максимум", type: "number", def: 100 },
    { name: "step", label: "Шаг", type: "number", def: 1 },
    { name: "mean_reversion", label: "Возврат к среднему (0..1)", type: "number", def: 0 },
  ],
  ramp_oneshot: [
    { name: "from", label: "От", type: "number", def: 0 },
    { name: "to", label: "До", type: "number", def: 100 },
    { name: "duration_s", label: "Длительность, с", type: "number", def: 10 },
    {
      name: "then",
      label: "Дальше (hold | loop | число)",
      type: "text",
      def: "hold",
    },
  ],
  countdown: [
    { name: "start", label: "Старт", type: "number", def: 100 },
    { name: "step", label: "Шаг", type: "number", def: 1 },
    { name: "min", label: "Минимум", type: "number", def: 0 },
    { name: "interval_s", label: "Период шага, с", type: "number", def: 1 },
  ],
  step: [
    { name: "values", label: "Значения через запятую", type: "text", def: "0, 1" },
    { name: "interval_s", label: "Период шага, с", type: "number", def: 1 },
  ],
  follow: [{ name: "source", label: 'Источник (вида "hr:15")', type: "text", def: "hr:15" }],
  toggle: [{ name: "period_s", label: "Период, с", type: "number", def: 1 }],
  pwm: [
    { name: "period_s", label: "Период, с", type: "number", def: 1 },
    { name: "duty", label: "Скважность (0..1)", type: "number", def: 0.5 },
  ],
  random_bit: [{ name: "p", label: "Вероятность 1 (0..1)", type: "number", def: 0.5 }],
  counter_bits: [
    {
      name: "bit",
      label: "Номер бита (пусто — адрес−1)",
      type: "number",
      def: null,
      optional: true,
    },
  ],
  follow_bit: [
    { name: "source", label: 'Источник (вида "hr:15")', type: "text", def: "hr:15" },
    { name: "bit", label: "Номер бита", type: "number", def: 0 },
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
    showError("пресеты работают для областей hr/ir (для cl/di выберите поведение вручную)");
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
  el("bh-status").textContent = "Пресет «" + name + "» загружен в форму — нажмите «Установить».";
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
    option.textContent = kind + " — " + KIND_LABELS[kind];
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
      showError("заполните параметр «" + spec.label + "»");
      return null;
    }
    if (spec.type === "number") {
      const value = Number(raw);
      if (Number.isNaN(value)) {
        showError("параметр «" + spec.label + "» должен быть числом");
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
          showError("параметр «" + spec.label + "»: «" + part + "» — не число");
          return null;
        }
        values.push(value);
      }
      if (values.length === 0) {
        showError("параметр «" + spec.label + "»: нужен хотя бы один элемент");
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
        showError("параметр «" + spec.label + "»: hold, loop или число");
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
  el("bh-status").textContent = "Ошибка: " + text;
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
  status.textContent = "Установка…";
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
      status.textContent = "Ошибка " + response.status + ": " + (body.detail || "");
      return;
    }
    status.textContent =
      "Установлено: slave " + body.slave + " " + body.area + " " + body.address
      + " → " + body.kind;
    refreshList();
  } catch (error) {
    status.textContent = "Ошибка: " + error;
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
      cell.textContent = "Переопределений нет — все приведения по умолчанию.";
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
        item.locked ? "да" : "нет",
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
