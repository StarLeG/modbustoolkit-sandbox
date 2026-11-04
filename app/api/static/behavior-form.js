// Схема поведений и сборка формы параметров (TASK-716): общий модуль страницы
// «Поведения» и карты регистров. Kind фильтруется по области (числовые —
// hr/ir, дискретные — cl/di), поля параметров рисуются по выбранному kind
// (PARAM_SPECS, дефолты совпадают с generators.py). Установка привязки —
// PUT /api/modbus/behaviors/{slave}/{area}/{address} (в модулях-потребителях).

export const NUMERIC_KINDS = [
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

export const DISCRETE_KINDS = ["constant", "toggle", "pwm", "random_bit", "counter_bits", "follow_bit"];

export function kindsForArea(area) {
  return area === "hr" || area === "ir" ? NUMERIC_KINDS : DISCRETE_KINDS;
}

// Типы значения (TASK-717): тип выбирается в настройках поведения, он же
// задаёт геометрию (uint16/int16 — регистр N, 32-битные — пара N, N+1) и
// математику генератора (float32 — дробные, целые — в диапазоне типа).
export const VALUE_DTYPES = ["uint16", "int16", "uint32", "int32", "float32"];

// Ширина типа в регистрах значений
export const DTYPE_WIDTH = { uint16: 1, int16: 1, uint32: 2, int32: 2, float32: 2 };

// Типы, доступные слейву: 16-битная сетка (slave 1) — все, 32-битная
// (слейвы 2..9, слот уже пара регистров) — только 32-битные
export function dtypesForFmt(fmt) {
  return String(fmt).startsWith("uint16") ? VALUE_DTYPES : ["uint32", "int32", "float32"];
}

// Имена поведений — русские строки; перевод даёт i18n.js (window.t)
export const KIND_RU = {
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

export function kindLabel(kind) {
  return window.t(KIND_RU[kind] || kind);
}

// Поля параметров kind'ов: name — имя параметра API, type — number/bool/text,
// def — значение по умолчанию (как в generators.py), optional — пустое поле
// не отправляется (сервер берёт дефолт; нужно для counter_bits: bit = address−1).
export const PARAM_SPECS = {
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

// Поля параметров в контейнер: label.bh-param > input[data-name=...];
// значения — дефолты PARAM_SPECS
export function renderParams(container, kind) {
  container.textContent = "";
  for (const spec of PARAM_SPECS[kind] || []) {
    const label = document.createElement("label");
    label.className = "bh-param";
    label.textContent = spec.label + " ";
    const input = document.createElement("input");
    input.dataset.name = spec.name;
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

// Значения параметров (params из API) в поля формы; список — строкой «0, 1»
export function fillParams(container, values) {
  for (const input of container.querySelectorAll("input[data-name]")) {
    const value = values ? values[input.dataset.name] : undefined;
    if (value === undefined || value === null) continue;
    if (input.type === "checkbox") input.checked = Boolean(value);
    else if (Array.isArray(value)) input.value = value.join(", ");
    else input.value = String(value);
  }
}

// Собрать params из формы: bool → checkbox, number → Number, text → строка
// (step.values — список чисел из строки «0, 1»); пустое optional-поле не
// отправляется — сервер возьмёт дефолт. Ошибка — onError(текст) и null.
export function collectParams(container, kind, onError) {
  const params = {};
  const specs = PARAM_SPECS[kind] || [];
  for (const spec of specs) {
    const input = container.querySelector('input[data-name="' + spec.name + '"]');
    if (!input) continue;
    if (spec.type === "bool") {
      params[spec.name] = input.checked;
      continue;
    }
    const raw = input.value.trim();
    if (raw === "") {
      if (spec.optional) continue;
      onError(window.t("заполните параметр «{label}»", { label: spec.label }));
      return null;
    }
    if (spec.type === "number") {
      const value = Number(raw);
      if (Number.isNaN(value)) {
        onError(window.t("параметр «{label}» должен быть числом", { label: spec.label }));
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
          onError(window.t("параметр «{label}»: «{part}» - не число", { label: spec.label, part: part }));
          return null;
        }
        values.push(value);
      }
      if (values.length === 0) {
        onError(window.t("параметр «{label}»: нужен хотя бы один элемент", { label: spec.label }));
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
        onError(window.t("параметр «{label}»: hold, loop или число", { label: spec.label }));
        return null;
      }
      params[spec.name] = value;
      continue;
    }
    params[spec.name] = raw;
  }
  return params;
}

// Параметры kind'а по умолчанию — те же, что отправила бы форма с дефолтами
// PARAM_SPECS (списки и «then» уже приведены к типам API)
export function defaultParams(kind) {
  const box = document.createElement("div");
  renderParams(box, kind);
  return collectParams(box, kind, function () {});
}
