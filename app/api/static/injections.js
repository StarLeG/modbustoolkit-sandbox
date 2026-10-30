// Инъекции ошибок (TASK-743): добавление, список активных, снятие.
// REST: GET/POST /api/modbus/inject, POST /api/modbus/inject/clear.
// Поля формы зависят от типа (exception → код, delay → задержка) и цели
// (весь слейв / диапазон адресов); список обновляется раз в 5 с —
// инъекции общие для всех пользователей, TTL убывает.

function el(id) {
  return document.getElementById(id);
}

function parseNumber(text) {
  const trimmed = text.trim();
  if (trimmed === "") return NaN;
  return Number(trimmed);
}

function updateFields() {
  const kind = el("inj-kind").value;
  el("inj-code").disabled = kind !== "exception";
  el("inj-delay").disabled = kind !== "delay";
  const scoped = el("inj-scope").value === "range";
  el("inj-area").disabled = !scoped;
  el("inj-address").disabled = !scoped;
  el("inj-count").disabled = !scoped;
}

function collectPayload() {
  const payload = {
    kind: el("inj-kind").value,
    slave: Number(el("inj-slave").value),
  };
  if (el("inj-scope").value === "range") {
    const address = parseNumber(el("inj-address").value);
    const count = parseNumber(el("inj-count").value);
    if (!Number.isInteger(address) || address < 1) {
      return { error: window.t("Адрес диапазона - целое число от 1 и больше.") };
    }
    payload.area = el("inj-area").value;
    payload.address = address;
    if (!Number.isInteger(count) || count < 1) {
      return { error: window.t("Количество значений - целое число от 1 и больше.") };
    }
    payload.count = count;
  }
  if (payload.kind === "exception") {
    payload.code = Number(el("inj-code").value);
  } else if (payload.kind === "delay") {
    const delay = parseNumber(el("inj-delay").value);
    if (!Number.isInteger(delay) || delay < 0 || delay > 5000) {
      return { error: window.t("Задержка - целое число от 0 до 5000 мс.") };
    }
    payload.delay_ms = delay;
  }
  const ttl = el("inj-ttl").value.trim();
  if (ttl !== "") {
    const seconds = parseNumber(ttl);
    if (!Number.isFinite(seconds) || seconds <= 0) {
      return { error: window.t("TTL - положительное число секунд.") };
    }
    payload.ttl_s = seconds;
  }
  return { payload };
}

function targetLabel(injection) {
  if (!injection.area) return window.t("весь слейв");
  let label = injection.area + " " + injection.address;
  if (injection.count && injection.count > 1) {
    label += " ×" + injection.count + " " + window.t("знач.");
  }
  return label;
}

function paramsLabel(injection) {
  if (injection.kind === "exception") {
    return "0x0" + injection.code.toString(16).toUpperCase();
  }
  if (injection.kind === "delay") {
    return window.t("{ms} мс", { ms: injection.delay_ms });
  }
  return window.t("молчание {ttl} с", { ttl: injection.ttl_s });
}

function renderRows(items) {
  const body = el("inj-rows");
  body.textContent = "";
  for (const injection of items) {
    const tr = document.createElement("tr");
    for (const cell of [
      String(injection.id),
      injection.kind,
      String(injection.slave),
      targetLabel(injection),
      paramsLabel(injection),
      String(injection.ttl_s),
      String(Math.round(injection.remaining_s)),
    ]) {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    }
    const td = document.createElement("td");
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "✕";
    button.title = window.t("Снять инъекцию #{id}", { id: injection.id });
    button.addEventListener("click", () => clearInjections({ id: injection.id }));
    td.appendChild(button);
    tr.appendChild(td);
    body.appendChild(tr);
  }
  if (items.length === 0) {
    const tr = document.createElement("tr");
    const td = document.createElement("td");
    td.colSpan = 8;
    td.textContent = window.t("Активных инъекций нет.");
    tr.appendChild(td);
    body.appendChild(tr);
  }
}

async function loadList() {
  const response = await fetch("/api/modbus/inject");
  if (!response.ok) return;
  renderRows((await response.json()).items);
}

async function addInjection() {
  const status = el("inj-status");
  const { payload, error } = collectPayload();
  if (error) {
    status.textContent = error;
    return;
  }
  const response = await fetch("/api/modbus/inject", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    status.textContent = (body && body.detail) || window.t("Ошибка ({status}).", { status: response.status });
    return;
  }
  status.textContent = window.t("Инъекция #{id} добавлена (TTL {ttl} с).", { id: body.id, ttl: body.ttl_s });
  await loadList();
}

async function clearInjections(filter) {
  const status = el("inj-status");
  const response = await fetch("/api/modbus/inject/clear", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(filter || {}),
  });
  const body = await response.json().catch(() => null);
  status.textContent = response.ok
    ? window.t("Снято инъекций: {count}", { count: body.cleared })
    : window.t("Ошибка снятия ({status}).", { status: response.status });
  await loadList();
}

function init() {
  el("inj-kind").addEventListener("change", updateFields);
  el("inj-scope").addEventListener("change", updateFields);
  el("inj-add").addEventListener("click", addInjection);
  el("inj-clear-all").addEventListener("click", () => clearInjections({}));
  updateFields();
  loadList();
  setInterval(loadList, 5000);
}

init();
