// Инструменты наладчика (TASK-741): конвертер байт/слов.
// Значение ↔ пара регистров во всех 4 порядках (ABCD/CDAB/BADC/DCBA) для
// uint32 и float. Пересчёт — серверный (POST /api/modbus/convert, тот же
// кодек, что и у слейвов 2..9): JS только собирает ввод и рисует таблицу.

function el(id) {
  return document.getElementById(id);
}

// Число из поля: десятичное или 0x-шестнадцатеричное; NaN — мусор.
function parseNumber(text) {
  const trimmed = text.trim();
  if (trimmed === "") return NaN;
  return Number(trimmed);
}

function parseRegs() {
  const hi = parseNumber(el("tool-reg-hi").value);
  const lo = parseNumber(el("tool-reg-lo").value);
  if (!Number.isInteger(hi) || !Number.isInteger(lo)) return null;
  if (hi < 0 || hi > 0xffff || lo < 0 || lo > 0xffff) return null;
  return [hi, lo];
}

function collectPayload() {
  const kind = el("tool-kind").value;
  const source = el("tool-source").value;
  if (source === "value") {
    const value = parseNumber(el("tool-value").value);
    if (!Number.isFinite(value)) return { error: "Введите значение (число или 0x…)." };
    return { payload: { kind, value } };
  }
  const regs = parseRegs();
  if (regs === null) {
    return { error: "Введите два регистра (0..65535, можно 0x…)." };
  }
  return { payload: { kind, regs } };
}

function hexReg(reg) {
  return "0x" + reg.toString(16).toUpperCase().padStart(4, "0");
}

function renderRows(rows) {
  const body = el("tool-rows");
  body.textContent = "";
  for (const row of rows) {
    const tr = document.createElement("tr");
    for (const cell of [
      row.order,
      hexReg(row.regs[0]) + " " + hexReg(row.regs[1]),
      row.regs[0] + ", " + row.regs[1],
      String(row.value),
    ]) {
      const td = document.createElement("td");
      td.textContent = cell;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

async function convert() {
  const status = el("tool-status");
  const { payload, error } = collectPayload();
  if (error) {
    status.textContent = error;
    return;
  }
  const response = await fetch("/api/modbus/convert", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    status.textContent =
      (body && body.detail) || "Ошибка конвертации (" + response.status + ").";
    return;
  }
  renderRows((await response.json()).rows);
  status.textContent = "Готово: 4 порядка.";
}

function init() {
  el("tool-convert").addEventListener("click", convert);
  // Enter в любом поле ввода — тоже конвертация
  for (const id of ["tool-value", "tool-reg-hi", "tool-reg-lo"]) {
    el(id).addEventListener("keydown", (event) => {
      if (event.key === "Enter") convert();
    });
  }
}

init();
